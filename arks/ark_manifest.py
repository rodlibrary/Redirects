#!/usr/bin/env python3
"""
Maintain arks_manifest.csv: every ARK Rod Library has minted, where it points in
Preservica, and the redirects.csv built from it for the GitLab redirect service.

Commands
    sync        Add any ARK in ark_master_list.csv that isn't in the manifest yet.
    add-minted  Add the rows of a generate_arks.py output CSV (a new minting run).
    match       Find each ARK in Preservica's MODS metadata and record the record it
                belongs to (one search over Preservica, then matched here).
    redirects   Write redirects.csv from the manifest.
    check       Check the manifest and master list for problems (used on pull requests).

Matching rules
    * An ARK found in exactly one Preservica record (Asset or Folder) is matched to it:
      preservica_ref becomes IO_<uuid> or SO_<uuid> and redirect_status "active".
    * A preservica_ref that was already filled in (by hand, or by an earlier run) is
      never replaced. If Preservica now says something different, that is reported
      in the notes and the run summary, for a person to decide.
    * An ARK found in several records, or in none, is left as it is and reported.
    * redirect_status "withdrawn" is never changed.

Preservica settings come from environment variables (settings.env in the workflows):
    PRESERVICA_HOST, PRESERVICA_TENANT, PRESERVICA_USER, PRESERVICA_PASSWORD
    ARK_FIELDS   indexed fields to look for ARKs in (default mods.identifier)
    ARK_NAAN     only ARKs under this NAAN are matched (default 32498)

Uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

MANIFEST_FIELDS = [
    "ark", "resolvable_url", "naan", "shoulder", "ark_blade", "identifier", "title",
    "url", "type", "commitment", "format", "relation", "source",
    "dc_path", "dc_article", "preservica_ref", "redirect_status",
    "match", "preservica_title", "checked", "notes",
]
REDIRECT_FIELDS = ["dc_path", "dc_article", "ark", "preservica_ref", "target_url", "status", "notes"]
STATUSES = ("active", "withdrawn", "unmatched")
UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
REF_RE = re.compile(rf"^(?:(IO|SO)_)?({UUID})$")
RESOLVER = "https://n2t.net"


# ---------------------------------------------------------------------------
# ARKs
# ---------------------------------------------------------------------------

def ark_key(text: str, naan: str = "") -> str:
    """Comparable form of an ARK: 'NAAN/name' with hyphens dropped (they carry no
    meaning in ARKs), or '' if the text holds no ARK. Accepts 'ark:/32498/x7k9q',
    'ark:32498/x7-k9q', 'https://n2t.net/ark:/32498/x7k9q' and '32498/x7k9q'."""
    m = re.search(r"ark:/?(\d{5,})/([A-Za-z0-9=~*+@_$.\-/]+)", text or "")
    if not m:
        m = re.fullmatch(r"\s*(\d{5,})/([A-Za-z0-9=~*+@_$.\-/]+)\s*", text or "")
    if not m:
        return ""
    if naan and m.group(1) != naan:
        return ""
    name = m.group(2).rstrip("./").replace("-", "")
    return f"{m.group(1)}/{name}"


def arks_in(value, naan: str) -> set[str]:
    """Every ARK under the NAAN mentioned in a metadata value (string or list)."""
    values = value if isinstance(value, list) else [value]
    found = set()
    for v in values:
        for m in re.finditer(r"(?:ark:/?)?\d{5,}/[A-Za-z0-9=~*+@_$.\-/]+", str(v or "")):
            k = ark_key(m.group(0), naan)
            if k:
                found.add(k)
    return found


def normalize_ref(value: str, default_type: str = "IO") -> str:
    """'IO_<uuid>', 'sdb:IO|<uuid>', a portal address or a bare UUID -> 'IO_<uuid>'."""
    v = urllib.parse.unquote(value or "").strip()
    m = re.search(rf"(IO|SO)[_|:]({UUID})", v)
    if m:
        return f"{m.group(1)}_{m.group(2).lower()}"
    m = re.search(UUID, v)
    return f"{default_type}_{m.group(0).lower()}" if m else v


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

def read_csv(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        reader.fieldnames = [f.strip().lower() for f in (reader.fieldnames or [])]
        return [{k: (v or "").strip() for k, v in row.items() if k} for row in reader]


def write_csv(path: str, rows: list[dict], fields: list[str]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def manifest_row(src: dict) -> dict:
    """A manifest row from any source with at least an 'ark' column."""
    row = {f: src.get(f, "") for f in MANIFEST_FIELDS}
    ark = src.get("ark", "")
    key = ark_key(ark)
    if key:
        naan, name = key.split("/", 1)
        row["ark"] = ark if ark.startswith("ark:") else f"ark:/{ark}"
        row["naan"] = row["naan"] or naan
        row["ark_blade"] = row["ark_blade"] or src.get("ark_blade", "") or name.split("/")[-1]
        row["resolvable_url"] = row["resolvable_url"] or f"{RESOLVER}/ark:/{key}"
    row["redirect_status"] = (src.get("redirect_status") or src.get("status") or "").lower()
    if row["redirect_status"] not in STATUSES:
        row["redirect_status"] = ""
    if row["preservica_ref"]:
        row["preservica_ref"] = normalize_ref(row["preservica_ref"])
    return row


def finish(row: dict) -> dict:
    """Fill redirect_status from what the row holds."""
    if row["redirect_status"] == "withdrawn":
        return row
    row["redirect_status"] = "active" if (row["preservica_ref"] or row["url"]) else "unmatched"
    return row


def sort_key(row: dict):
    return ark_key(row["ark"])


# ---------------------------------------------------------------------------
# Preservica
# ---------------------------------------------------------------------------

class Preservica:
    def __init__(self) -> None:
        host = os.environ.get("PRESERVICA_HOST", "").strip()
        self.user = os.environ.get("PRESERVICA_USER", "").strip()
        self.password = os.environ.get("PRESERVICA_PASSWORD", "")
        self.tenant = os.environ.get("PRESERVICA_TENANT", "").strip()
        missing = [n for n, v in (("PRESERVICA_HOST", host), ("PRESERVICA_USER", self.user),
                                  ("PRESERVICA_PASSWORD", self.password)) if not v]
        if missing:
            sys.exit(f"error: set {', '.join(missing)}")
        self.base = host.rstrip("/") if host.startswith(("http://", "https://")) else f"https://{host}"
        self.token = ""
        self.login()

    def login(self) -> None:
        form = {"username": self.user, "password": self.password}
        if self.tenant:
            form["tenant"] = self.tenant
        req = urllib.request.Request(f"{self.base}/api/accesstoken/login",
                                     data=urllib.parse.urlencode(form).encode(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                self.token = json.loads(resp.read())["token"]
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", "replace")
            try:
                reason = json.loads(text).get("message", "")
            except ValueError:
                reason = ""
            hint = ("check PRESERVICA_TENANT (UNIOWA) and PRESERVICA_USER" if "incorrect.tenant" in reason else
                    "the password is wrong, or the account has no Preservica password of its own"
                    if "Bad credentials" in reason else
                    "PRESERVICA_HOST must be the API server (us.preservica.com), not the public portal"
                    if exc.code == 404 else "check the Preservica user name, password and tenant")
            sys.exit(f"error: Preservica login failed (HTTP {exc.code})"
                     f"{': ' + reason if reason else ''}. Most likely {hint}.")

    def search(self, fields: dict, metadata: list[str], start: int, size: int) -> dict:
        """One page of POST /api/content/search. The query is '*': on Preservica 9.4
        the '%' that pyPreservica uses by default returns nothing."""
        q = json.dumps({"q": "*", "fields": [{"name": k, "values": v} for k, v in fields.items()]})
        form = [("start", str(start)), ("max", str(size)), ("q", q)] + [("metadata", m) for m in metadata]
        for attempt in range(1, 6):
            req = urllib.request.Request(f"{self.base}/api/content/search",
                                         data=urllib.parse.urlencode(form).encode(), method="POST",
                                         headers={"Preservica-Access-Token": self.token,
                                                  "Content-Type": "application/x-www-form-urlencoded"})
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    return json.loads(resp.read())["value"]
            except urllib.error.HTTPError as exc:
                if exc.code == 401:
                    self.login()
                    continue
                if exc.code in (429, 500, 502, 503, 504) and attempt < 5:
                    time.sleep(2 ** attempt)
                    continue
                raise SystemExit(f"error: Preservica search failed (HTTP {exc.code}): "
                                 f"{exc.read().decode('utf-8', 'replace')[:300]}")
            except urllib.error.URLError as exc:
                if attempt == 5:
                    raise SystemExit(f"error: can't reach {self.base}: {exc.reason}")
                time.sleep(2 ** attempt)
        raise SystemExit("error: Preservica search kept failing")


def preservica_arks(api: Preservica, ark_fields: list[str], naan: str, page: int = 100):
    """{ark_key: [(ref, title, field)]} for every record whose ARK fields mention an ARK."""
    if api.search({}, ["xip.title"], 0, 1).get("totalHits", 0) == 0:
        sys.exit(f"error: logged in as {api.user}, but the account can't see any records. "
                 "Ask your Preservica administrator to give its role Read Metadata on the "
                 "records' security tags (Read Content alone isn't enough).")
    found: dict[str, list[tuple[str, str, str]]] = {}
    stats = {}
    for field in ark_fields:
        start, seen, with_ark = 0, 0, 0
        while True:
            v = api.search({field: ["*"]}, ["xip.title", field], start, page)
            ids, meta = v.get("objectIds", []), v.get("metadata", [])
            for oid, rec in zip(ids, meta):
                m = re.search(rf"(IO|SO)\|({UUID})", oid)
                if not m:
                    continue
                ref = f"{m.group(1)}_{m.group(2).lower()}"
                values = {f["name"]: f.get("value") for f in rec}
                keys = arks_in(values.get(field), naan)
                with_ark += bool(keys)
                for k in keys:
                    found.setdefault(k, [])
                    if ref not in [r for r, _, _ in found[k]]:
                        found[k].append((ref, str(values.get("xip.title") or ""), field))
            seen += len(ids)
            start += len(ids)
            if not ids or start >= v.get("totalHits", 0):
                break
        stats[field] = (seen, with_ark)
    return found, stats


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_sync(args) -> int:
    manifest = [manifest_row(r) for r in read_csv(args.manifest)]
    have = {ark_key(r["ark"]) for r in manifest}
    added = 0
    for m in read_csv(args.master_list):
        k = ark_key(m.get("ark", ""))
        if k and k not in have:
            manifest.append(finish(manifest_row(m)))
            have.add(k)
            added += 1
    write_csv(args.manifest, sorted((finish(r) for r in manifest), key=sort_key), MANIFEST_FIELDS)
    print(f"{args.manifest}: {len(manifest)} ARKs ({added} added from {args.master_list})")
    return 0


def cmd_add_minted(args) -> int:
    manifest = [manifest_row(r) for r in read_csv(args.manifest)]
    have = {ark_key(r["ark"]) for r in manifest}
    added, skipped = 0, 0
    for r in read_csv(args.minted):
        if r.get("status", "").upper().startswith("ERROR") or not ark_key(r.get("ark", "")):
            skipped += 1
            continue
        if ark_key(r["ark"]) in have:
            continue
        row = manifest_row(r)
        row["notes"] = r.get("notes", "")
        manifest.append(finish(row))
        have.add(ark_key(r["ark"]))
        added += 1
    write_csv(args.manifest, sorted(manifest, key=sort_key), MANIFEST_FIELDS)
    print(f"{args.manifest}: {added} new ARKs added, {skipped} rows with errors skipped")
    return 1 if skipped else 0


def cmd_match(args) -> int:
    naan = os.environ.get("ARK_NAAN", "32498")
    ark_fields = [f.strip() for f in os.environ.get("ARK_FIELDS", "mods.identifier").split(",") if f.strip()]
    manifest = [manifest_row(r) for r in read_csv(args.manifest)]
    before = {ark_key(r["ark"]): {k: v for k, v in r.items() if k != "checked"} for r in manifest}
    api = Preservica()
    found, stats = preservica_arks(api, ark_fields, naan)
    today = dt.date.today().isoformat()

    counts = {"newly matched": 0, "already matched": 0, "conflict": 0, "several records": 0,
              "not found": 0, "withdrawn": 0}
    report = []
    for row in manifest:
        key = ark_key(row["ark"])
        hits = found.get(key, [])
        refs = [r for r, _, _ in hits]
        row["checked"] = today
        if row["redirect_status"] == "withdrawn":
            counts["withdrawn"] += 1
            continue
        if row["preservica_ref"]:
            # A bare UUID was read as an Asset; take the type Preservica reports.
            same = [r for r in refs if r[3:] == row["preservica_ref"][3:]]
            if len(refs) == 1 and same:
                row["preservica_ref"] = same[0]
            if not hits or (len(refs) == 1 and row["preservica_ref"] in refs):
                counts["already matched"] += 1
                if hits:
                    row["preservica_title"] = hits[0][1]
                    row["match"] = row["match"] or f"ark in {hits[0][2]}"
                elif not row["match"]:
                    row["match"] = "entered by hand"
                continue
            counts["conflict"] += 1
            row["match"] = "conflict"
            row["notes"] = f"ARK found on {', '.join(refs)}, but preservica_ref is {row['preservica_ref']}"
            report.append((row["ark"], row["notes"]))
            continue
        if len(hits) == 1:
            ref, title, field = hits[0]
            row.update(preservica_ref=ref, preservica_title=title, match=f"ark in {field}", notes="")
            counts["newly matched"] += 1
        elif len(hits) > 1:
            row["match"] = "several records"
            row["notes"] = f"ARK found on {len(hits)} records: {', '.join(refs)}"
            counts["several records"] += 1
            report.append((row["ark"], row["notes"]))
        else:
            row["match"] = "not found"
            counts["not found"] += 1
        finish(row)

    known = {ark_key(r["ark"]) for r in manifest}
    unknown = sorted(k for k in found if k not in known)
    # Rows that changed in anything but the check date: what a person needs to review.
    changed = sum({k: v for k, v in r.items() if k != "checked"} != before.get(ark_key(r["ark"]))
                  for r in manifest)
    write_csv(args.manifest, sorted(manifest, key=sort_key), MANIFEST_FIELDS)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
            fh.write(f"changed={changed}\n")

    lines = [f"Searched Preservica: " + "; ".join(
        f"{n} record(s) with {f}, {a} of them mentioning a {naan} ARK" for f, (n, a) in stats.items()),
        "Manifest: " + ", ".join(f"{v} {k}" for k, v in counts.items() if v),
        f"{changed} row(s) changed since the last run"]
    if unknown:
        lines.append(f"{len(unknown)} ARK(s) appear in Preservica but not in the manifest "
                     f"(minted elsewhere?): " + ", ".join(f"ark:/{k}" for k in unknown[:20])
                     + (" ..." if len(unknown) > 20 else ""))
    for ark, note in report[:50]:
        lines.append(f"{ark}: {note}")
    print("\n".join(lines))
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as fh:
            fh.write("### ARK matching\n\n" + "\n".join(f"- {l}" for l in lines) + "\n")
    return 0


def cmd_redirects(args) -> int:
    rows = []
    for r in sorted((manifest_row(x) for x in read_csv(args.manifest)), key=sort_key):
        finish(r)
        rows.append({
            "dc_path": r["dc_path"], "dc_article": r["dc_article"],
            "ark": r["ark"], "preservica_ref": r["preservica_ref"],
            "target_url": r["url"], "status": r["redirect_status"],
            "notes": r["notes"] if r["match"] in ("conflict", "several records") else "",
        })
    write_csv(args.out, rows, REDIRECT_FIELDS)
    active = sum(r["status"] == "active" for r in rows)
    print(f"{args.out}: {len(rows)} rows ({active} active)")
    return 0


def cmd_check(args) -> int:
    naan = os.environ.get("ARK_NAAN", "32498")
    errors, warnings = [], []
    manifest = read_csv(args.manifest)
    master = {ark_key(m.get("ark", "")) for m in read_csv(args.master_list)}
    seen = {}
    for n, r in enumerate(manifest, 2):
        where = f"{args.manifest}:{n}"
        k = ark_key(r.get("ark", ""))
        if not k:
            errors.append(f"{where}: '{r.get('ark', '')}' isn't an ARK")
            continue
        if not k.startswith(naan + "/"):
            errors.append(f"{where}: {r['ark']} isn't under NAAN {naan}")
        if k in seen:
            errors.append(f"{where}: {r['ark']} is also on line {seen[k]}")
        seen[k] = n
        if master and k not in master:
            errors.append(f"{where}: {r['ark']} isn't in {args.master_list} (mint ARKs with the Mint ARKs workflow)")
        ref = r.get("preservica_ref", "")
        if ref and not REF_RE.match(ref) and not re.search(UUID, ref):
            errors.append(f"{where}: preservica_ref '{ref}' should look like IO_<uuid> or SO_<uuid>")
        status = (r.get("redirect_status") or "").lower()
        if status and status not in STATUSES:
            errors.append(f"{where}: redirect_status '{status}' should be one of {', '.join(STATUSES)}")
        url = r.get("url", "")
        if url and not url.startswith("https://"):
            errors.append(f"{where}: url should start with https://")
    missing = master - set(seen)
    if missing:
        warnings.append(f"{len(missing)} ARK(s) in {args.master_list} aren't in the manifest yet "
                        "(the next Match ARKs run adds them)")
    for w in warnings:
        print(f"warning: {w}")
    for e in errors:
        print(f"ERROR {e}")
    print(f"{args.manifest}: {len(manifest)} ARKs, {len(errors)} error(s)")
    return 1 if errors else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", default="arks/arks_manifest.csv")
    ap.add_argument("--master-list", default="arks/ark_master_list.csv")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("sync")
    p = sub.add_parser("add-minted")
    p.add_argument("minted", help="CSV written by generate_arks.py")
    p = sub.add_parser("match")
    p.add_argument("--summary", default=os.environ.get("GITHUB_STEP_SUMMARY"),
                   help="also append the report to this Markdown file")
    p = sub.add_parser("redirects")
    p.add_argument("--out", default="redirects.csv")
    sub.add_parser("check")
    args = ap.parse_args()
    return {"sync": cmd_sync, "add-minted": cmd_add_minted, "match": cmd_match,
            "redirects": cmd_redirects, "check": cmd_check}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
