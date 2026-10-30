#!/usr/bin/env python3
"""Harvest item identifiers from Digital Commons over OAI-PMH.

Produces a staging CSV with, for each record:
    context, article, dc_path, landing_url, file_url, title, oai_identifier, article_source

Digital Commons exposes two numbers per item:
  * the PATH number in the landing URL:       /etd/123/  (journals: /pias/vol94/iss4/3)
  * the ARTICLE number in file URLs:          /cgi/viewcontent.cgi?article=1124&context=etd
The article number is taken from the viewcontent.cgi URL in dc:identifier when one
is present ("file_url"). For metadata-only records it falls back to the number in
the OAI identifier (oai:host:etd-1124), marked article_source=oai_id. Spot-check a
few of those rows against the live site before trusting them.

Usage:
    python scripts/harvest_dc.py --base-url https://scholarworks.uni.edu/do/oai/ \
        --out staging/dc_items.csv [--set publication:etd]

Needs only the Python standard library. Run it while Digital Commons is still live.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

NS = {
    "oai": "http://www.openarchives.org/OAI/2.0/",
    "dc": "http://purl.org/dc/elements/1.1/",
}
# The context can itself contain hyphens (journal-stem-arts-1046), so split on the last one.
OAI_ID_RE = re.compile(r"^oai:[^:]+:(?P<context>[A-Za-z0-9_-]+)-(?P<article>\d+)$")
# Landing pages: /etd/123 (series) or /pias/vol94/iss4/3 (journal article).
LANDING_RE = re.compile(r"^/([A-Za-z0-9_][A-Za-z0-9_-]*(?:/vol[0-9A-Za-z_]+/iss[0-9A-Za-z_]+)?/\d+)/?$")
FIELDS = ["context", "article", "dc_path", "landing_url", "file_url", "title",
          "oai_identifier", "article_source"]


def fetch(url: str, retries: int = 5, delay: float = 1.0) -> ET.Element:
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "dc-preservica-redirects/1.0"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                return ET.fromstring(resp.read())
        except (urllib.error.URLError, TimeoutError, ET.ParseError) as exc:
            if attempt == retries:
                raise
            wait = delay * 2 ** attempt
            print(f"  retry {attempt} after error: {exc} (waiting {wait:.0f}s)", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def parse_record(rec: ET.Element, host: str) -> dict | None:
    header = rec.find("oai:header", NS)
    if header is None or header.get("status") == "deleted":
        return None
    oai_id = (header.findtext("oai:identifier", default="", namespaces=NS)).strip()

    idents = [(e.text or "").strip() for e in rec.iterfind(".//dc:identifier", NS)]
    title = (rec.findtext(".//dc:title", default="", namespaces=NS)).strip()

    landing_url = file_url = ""
    context = article = dc_path = ""
    source = ""
    for ident in idents:
        u = urllib.parse.urlparse(ident)
        if not u.scheme.startswith("http"):
            continue
        if host and u.hostname and u.hostname.lower() != host:
            continue
        if u.path.endswith("viewcontent.cgi"):
            q = urllib.parse.parse_qs(u.query)
            if "article" in q and "context" in q:
                file_url = ident
                article, context = q["article"][0], q["context"][0].lower()
                source = "viewcontent"
            continue
        m = re.match(r"^/context/([^/]+)/article/(\d+)/", u.path)
        if m:
            file_url = file_url or ident
            if not article:
                context, article, source = m.group(1).lower(), m.group(2), "context_url"
            continue
        m = LANDING_RE.match(u.path)
        if m and not landing_url:
            landing_url = ident
            dc_path = m.group(1).lower()

    if not article:
        m = OAI_ID_RE.match(oai_id)
        if m:
            context, article = m.group("context").lower(), m.group("article")
            source = "oai_id"

    return {
        "context": context, "article": article, "dc_path": dc_path,
        "landing_url": landing_url, "file_url": file_url, "title": title,
        "oai_identifier": oai_id, "article_source": source,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", required=True, help="OAI-PMH endpoint, e.g. https://host/do/oai/")
    ap.add_argument("--out", default="staging/dc_items.csv")
    ap.add_argument("--set", dest="set_spec", help="limit to one set, e.g. publication:etd")
    ap.add_argument("--prefix", default="oai_dc", help="metadataPrefix (default oai_dc)")
    ap.add_argument("--pause", type=float, default=1.0, help="seconds between requests")
    ap.add_argument("--dc-host", help="public hostname in item URLs, if different from the OAI endpoint host")
    args = ap.parse_args()

    base = args.base_url.rstrip("?")
    host = (urllib.parse.urlparse(base).hostname or "").lower()
    if args.dc_host:
        host = args.dc_host.lower()
    params = {"verb": "ListRecords", "metadataPrefix": args.prefix}
    if args.set_spec:
        params["set"] = args.set_spec
    url = f"{base}?{urllib.parse.urlencode(params)}"

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    count = pages = no_path = 0
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        while url:
            root = fetch(url)
            err = root.find("oai:error", NS)
            if err is not None:
                if err.get("code") == "noRecordsMatch":
                    break
                print(f"OAI error {err.get('code')}: {err.text}", file=sys.stderr)
                return 1
            for rec in root.iterfind(".//oai:record", NS):
                row = parse_record(rec, host)
                if row:
                    writer.writerow(row)
                    count += 1
                    no_path += not row["dc_path"]
            pages += 1
            token = root.findtext(".//oai:resumptionToken", default="", namespaces=NS).strip()
            print(f"page {pages}: {count} records so far", file=sys.stderr)
            url = f"{base}?{urllib.parse.urlencode({'verb': 'ListRecords', 'resumptionToken': token})}" if token else ""
            if url:
                time.sleep(args.pause)

    print(f"wrote {count} records to {out}")
    if no_path:
        print(f"warning: {no_path} record(s) had no landing URL in dc:identifier", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
