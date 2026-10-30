#!/usr/bin/env python3
"""Match harvested Digital Commons items to Preservica entities and write the manifest.

For each staging row (from harvest_dc.py) this asks Preservica's Entity API for the
entity carrying an external identifier built from the Digital Commons item, e.g.
type "dc_article", value "etd-1124". It then writes manifest rows with the
Preservica reference and Universal Access URL. Optionally it also:
  * reads an ARK stored on the same entity as another external identifier, and
  * matches collection pages to Structural Objects: each series or journal (/etd/,
    /pias/) and, for journals, each volume and issue (/pias/vol94/, /pias/vol94/iss4/).
    A volume or issue with no Folder of its own falls back to its journal's Folder.

Rows already resolved in an existing manifest are carried forward and not looked
up again, so this can be re-run as more content is ingested.

Credentials come from environment variables, never the command line:
    PRESERVICA_HOST       the Preservica server you sign in to, e.g. us.preservica.com
    PRESERVICA_USER       a Preservica user name (a read-only service account)
    PRESERVICA_PASSWORD
    PRESERVICA_TENANT     optional on Preservica 6.3 and later; needed on older systems

Usage:
    python scripts/match_preservica.py staging/dc_items.csv \
        --access-host uni.access.preservica.com \
        --existing manifest/redirects.csv --out manifest/redirects.csv \
        --ark-id-type ark --series

Redirect targets come from --target-template (or PRESERVICA_TARGET_TEMPLATE).
Fields available in the template:
    {access_host}  the --access-host value
    {kind}         "asset" for Assets (IO), "folder" for Folders (SO)
    {type}         "IO" or "SO"
    {ref}          the entity's UUID
    {sdb}          "sdb:IO|<uuid>" percent-encoded: sdb%3AIO%7C<uuid>
Examples:
    https://{access_host}/portal/en-US/{kind}/{sdb}     (Preservica Access portal)
    https://{access_host}/uncategorized/{type}_{ref}/   (older Universal Access)

When the template changes, rerun with --existing: carried-forward rows get their
target_url rebuilt from preservica_ref, so the whole manifest follows the new format.
Rows in overrides.csv are never touched.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from manifest import COLUMNS, build_target, normalize_ark, read_manifest  # noqa: E402


def collection_paths(dc_path: str, context: str) -> set[str]:
    """Collection pages above an item: its series or journal, plus journal volume and issue.

    "etd/123"           -> {"etd"}
    "pias/vol94/iss4/3" -> {"pias", "pias/vol94", "pias/vol94/iss4"}
    """
    parts = dc_path.split("/") if dc_path else []
    found = {context} if context else set()
    if parts:
        found.add(parts[0])
        if len(parts) == 4 and parts[1].startswith("vol") and parts[2].startswith("iss"):
            found.update({"/".join(parts[:2]), "/".join(parts[:3])})
    return found


def parents(path: str) -> list[str]:
    """Nearest first: "pias/vol94/iss4" -> ["pias/vol94", "pias"]."""
    parts = path.split("/")
    return ["/".join(parts[:i]) for i in range(len(parts) - 1, 0, -1)]


def local(tag: str) -> str:
    """Element name without its namespace (the Entity API namespace changes by version)."""
    return tag.rsplit("}", 1)[-1]


class Preservica:
    def __init__(self, host: str, user: str, password: str, tenant: str, pause: float):
        # Accept a bare hostname, or a full base URL (useful for testing).
        self.base = host.rstrip("/") if host.startswith(("http://", "https://")) else f"https://{host}"
        self.creds = {"username": user, "password": password}
        if tenant:  # optional from Preservica 6.3
            self.creds["tenant"] = tenant
        self.pause = pause
        self.token = ""
        self.login()

    def login(self) -> None:
        data = urllib.parse.urlencode(self.creds).encode()
        req = urllib.request.Request(f"{self.base}/api/accesstoken/login", data=data, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                self.token = json.loads(resp.read())["token"]
        except urllib.error.HTTPError as exc:
            raise SystemExit(
                f"error: Preservica login at {self.base} failed (HTTP {exc.code}). Check "
                "PRESERVICA_USER and PRESERVICA_PASSWORD, add PRESERVICA_TENANT if your "
                "system needs it, and make sure the account doesn't require two-factor sign-in."
            ) from None

    def get(self, path: str, params: dict | None = None) -> ET.Element:
        url = f"{self.base}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        return self.get_url(url)

    def get_url(self, url: str) -> ET.Element:
        """GET an absolute API URL (e.g. a paging 'Next' link) and parse the XML."""
        for attempt in range(1, 6):
            req = urllib.request.Request(url, headers={"Preservica-Access-Token": self.token})
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    body = resp.read()
                time.sleep(self.pause)
                return ET.fromstring(body)
            except urllib.error.HTTPError as exc:
                if exc.code == 401:  # token expired
                    self.login()
                    continue
                if exc.code in (429, 500, 502, 503, 504) and attempt < 5:
                    time.sleep(2 ** attempt)
                    continue
                raise
            except urllib.error.URLError:
                if attempt == 5:
                    raise
                time.sleep(2 ** attempt)
        raise RuntimeError(f"giving up on {url}")

    def by_identifier(self, id_type: str, value: str) -> list[tuple[str, str]]:
        """Return [(type, ref)] for entities with this external identifier."""
        root = self.get("/api/entity/entities/by-identifier", {"type": id_type, "value": value})
        found = []
        for el in root.iter():
            if local(el.tag) == "Entity" and el.get("ref"):
                found.append((el.get("type", ""), el.get("ref")))
        return found

    def identifiers(self, etype: str, ref: str) -> list[tuple[str, str]]:
        kind = "information-objects" if etype == "IO" else "structural-objects"
        root = self.get(f"/api/entity/{kind}/{ref}/identifiers")
        pairs = []
        for el in root.iter():
            if local(el.tag) == "Identifier":
                t = v = ""
                for child in el:
                    if local(child.tag) == "Type":
                        t = (child.text or "").strip()
                    elif local(child.tag) == "Value":
                        v = (child.text or "").strip()
                if t:
                    pairs.append((t, v))
        return pairs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("staging", help="CSV from harvest_dc.py (needs context, article, dc_path)")
    ap.add_argument("--access-host", required=True, help="Preservica Universal Access hostname")
    ap.add_argument("--out", default="manifest/redirects.csv")
    ap.add_argument("--existing", help="existing manifest; resolved rows are kept as-is")
    ap.add_argument("--unmatched-report", default="staging/unmatched.csv")
    ap.add_argument("--id-type", default="dc_article", help="identifier type on IO entities")
    ap.add_argument("--id-template", default="{context}-{article}",
                    help="identifier value pattern; fields: context, article, dc_path")
    ap.add_argument("--target-template",
                    default=os.environ.get("PRESERVICA_TARGET_TEMPLATE")
                    or "https://{access_host}/portal/en-US/{kind}/{sdb}",
                    help="redirect target pattern; see the fields above")
    ap.add_argument("--ark-id-type", help="identifier type holding the ARK, if ARKs are stored in Preservica")
    ap.add_argument("--series", action="store_true",
                    help="also match series, journals, volumes and issues to SOs")
    ap.add_argument("--series-id-type", default="dc_series")
    ap.add_argument("--pause", type=float, default=0.2, help="seconds between API calls")
    ap.add_argument("--no-lookup", action="store_true",
                    help="don't query Preservica: only build the row list from the harvest, "
                         "keeping resolved rows (use before match_by_title.py)")
    args = ap.parse_args()

    api = None
    if not args.no_lookup:
        env = {k: os.environ.get(k, "") for k in
               ("PRESERVICA_HOST", "PRESERVICA_USER", "PRESERVICA_PASSWORD", "PRESERVICA_TENANT")}
        missing = [k for k, v in env.items() if not v and k != "PRESERVICA_TENANT"]
        if missing:
            print(f"error: set {', '.join(missing)}", file=sys.stderr)
            return 1
        api = Preservica(env["PRESERVICA_HOST"], env["PRESERVICA_USER"], env["PRESERVICA_PASSWORD"],
                         env["PRESERVICA_TENANT"], args.pause)

    def target(etype: str, ref: str) -> str:
        return build_target(f"{etype}_{ref}", args.target_template, args.access_host)

    # Carry forward rows that are already resolved, rebuilding their links with the
    # current template so a format change applies to the whole manifest.
    kept: dict[str, dict] = {}
    rebuilt = 0
    if args.existing:
        for r in read_manifest(args.existing):
            if r["status"] in ("active", "withdrawn"):
                row = dict(r.data)
                ref = row.get("preservica_ref", "")
                if row["status"] == "active" and "_" in ref:
                    etype, uuid = ref.split("_", 1)
                    new = target(etype, uuid)
                    if new != row["target_url"]:
                        row["target_url"] = new
                        rebuilt += 1
                # ARK-only rows have no ScholarWorks key; keep each one under its ARK.
                kept[r["dc_article"] or r["dc_path"] or f"ark:{r['ark']}" or f"line:{r.line}"] = row
    if rebuilt:
        print(f"rebuilt {rebuilt} existing link(s) with the current target template", file=sys.stderr)

    with open(args.staging, newline="", encoding="utf-8") as fh:
        staging = list(csv.DictReader(fh))

    out_rows: list[dict] = []
    unmatched: list[dict] = []
    collections: set[str] = set()
    looked_up = 0

    for i, item in enumerate(staging, 1):
        context, article = item.get("context", "").lower(), item.get("article", "")
        dc_path = item.get("dc_path", "").lower()
        collections.update(collection_paths(dc_path, context))
        key = f"{context}/{article}" if context and article else dc_path
        if key in kept:
            out_rows.append(kept.pop(key))
            continue

        row = {c: "" for c in COLUMNS}
        row.update(dc_path=dc_path, dc_article=f"{context}/{article}" if context and article else "")
        id_value = args.id_template.format(context=context, article=article, dc_path=dc_path)
        if api is None:
            row.update(status="unmatched", notes="not looked up")
            out_rows.append(row)
            continue
        hits = api.by_identifier(args.id_type, id_value)
        looked_up += 1
        ios = [h for h in hits if h[0] == "IO"] or hits

        if len(ios) != 1:
            row["status"] = "unmatched"
            row["notes"] = f"{len(ios)} entities for {args.id_type}={id_value}"
            unmatched.append({**item, "reason": row["notes"]})
        else:
            etype, ref = ios[0]
            row.update(preservica_ref=f"{etype}_{ref}", target_url=target(etype, ref), status="active")
            if args.ark_id_type:
                arks = [v for t, v in api.identifiers(etype, ref) if t == args.ark_id_type]
                if arks:
                    row["ark"] = normalize_ark(arks[0])
        out_rows.append(row)
        if i % 100 == 0:
            print(f"{i}/{len(staging)} processed", file=sys.stderr)

    # Keep any carried-forward rows that weren't in this staging file (e.g. overrides of old runs).
    out_rows.extend(kept.values())

    if args.series:
        # Fallback rows are retried each run in case the Folder has since been ingested.
        out_rows = [r for r in out_rows if not r.get("notes", "").startswith("fallback")]
        resolved = {r["dc_path"]: r for r in out_rows if r["status"] == "active"}
        # Shortest paths first, so a journal is resolved before its volumes and issues.
        for path in sorted(collections - set(resolved), key=lambda p: (p.count("/"), p)):
            row = {c: "" for c in COLUMNS}
            row["dc_path"] = path
            hits = ([h for h in api.by_identifier(args.series_id_type, path) if h[0] == "SO"]
                    if api else [])
            parent = next((resolved[p] for p in parents(path) if p in resolved), None)
            if len(hits) == 1:
                etype, ref = hits[0]
                row.update(preservica_ref=f"{etype}_{ref}", target_url=target(etype, ref),
                           status="active", notes="collection")
                resolved[path] = row
            elif parent:
                row.update(preservica_ref=parent["preservica_ref"], target_url=parent["target_url"],
                           status="active", notes=f"fallback to {parent['dc_path']}")
            else:
                row.update(status="unmatched", notes=f"collection: {len(hits)} SO matches")
                unmatched.append({"dc_path": path, "reason": row["notes"]})
            out_rows.append(row)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out_rows.sort(key=lambda r: (r["dc_path"].split("/")[0], r["dc_article"], r["dc_path"]))
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(out_rows)

    if unmatched:
        rep = Path(args.unmatched_report)
        rep.parent.mkdir(parents=True, exist_ok=True)
        fields = sorted({k for u in unmatched for k in u})
        with rep.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            w.writerows(unmatched)

    active = sum(r["status"] == "active" for r in out_rows)
    print(f"wrote {len(out_rows)} rows to {out}: {active} active, {len(unmatched)} unmatched, "
          f"{looked_up} API lookups")
    if unmatched:
        print(f"unmatched items listed in {args.unmatched_report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
