#!/usr/bin/env python3
"""Turn the redirect lists into the lookup files Apache reads, rejecting bad rows.

Reads one or more CSV files, normally both of:
  * redirects.csv, maintained by DCDS: old ScholarWorks addresses
  * the ARK redirects built from arks/arks_manifest.csv
    (python3 arks/ark_manifest.py redirects --out build/ark_redirects.csv)
An address or ARK may appear in more than one file only if it points to the same place.

Columns:

    dc_path   the old ScholarWorks address, e.g.
                https://scholarworks.uni.edu/unigreek_images/1
              File addresses work too:
                https://scholarworks.uni.edu/cgi/viewcontent.cgi?article=1001&context=unigreek_images
                https://scholarworks.uni.edu/context/unigreek_images/article/1001/viewcontent/x.jpg
    p_path    where it now lives, e.g.
                https://uni.access.preservica.com/portal/en-US/asset/sdb%3AIO%7C9dbdd64c-...
              or "410" / "gone" for an item that was withdrawn (answers 410 Gone).
    ark       optional column: the item's ARK, e.g. ark:/32498/xo2gohohwd, so the ARK
              resolves to p_path too. Leave the cell (or the whole column) out if none.
              A row may have an ark and no dc_path (an ARK with no old address).

Writes into --out (with --check, only validates and writes nothing):
    dc_paths.txt     "unigreek_images/1 https://..."   any old page address
    dc_article.txt   "unigreek_images/1001 https://..." old file addresses
    ark.txt          "32498/xo2gohohwd https://..."
    sample.tsv       requests and expected answers, for tests and post-install checks

Usage:
    python3 scripts/build_map.py --check redirects.csv build/ark_redirects.csv
    python3 scripts/build_map.py redirects.csv build/ark_redirects.csv --out /path/to/maps
Exit status 1 if any row has an error, so the pipeline fails and nothing is installed.
"""

from __future__ import annotations

import argparse
import csv
import os
import random
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

GONE = "GONE"
URL_CHARS_RE = re.compile(r"^(?:[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=]|%[0-9A-Fa-f]{2})+$")
CONTEXT_FILE_RE = re.compile(r"^context/([^/]+)/article/(\d+)(?:/|$)")
ARK_RE = re.compile(r"^(?:ark:/?)?(\d{5,})/([0-9a-z][0-9a-z./_~-]*)$", re.I)


def env_list(name: str, default: str) -> list[str]:
    return [h.strip().lower() for h in os.environ.get(name, default).split(",") if h.strip()]


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", nargs="*", default=["redirects.csv"], help="redirect list(s)")
    ap.add_argument("--out", help="directory for the map files")
    ap.add_argument("--check", action="store_true", help="validate only")
    ap.add_argument("--dc-host", action="append",
                    help="old ScholarWorks hostname(s) accepted in dc_path "
                         "(default: DC_HOSTS env or scholarworks.uni.edu)")
    ap.add_argument("--allowed-host", action="append",
                    help="hostname(s) allowed in p_path (default: ALLOWED_TARGET_HOSTS env)")
    ap.add_argument("--naan", default=os.environ.get("ARK_NAAN", "32498"))
    ap.add_argument("--sample", type=int, default=200, help="rows to put in sample.tsv (0 = all)")
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()
    if not args.check and not args.out:
        ap.error("give --out DIR, or --check to validate only")
    args.dc_hosts = [h.lower() for h in args.dc_host] if args.dc_host else env_list("DC_HOSTS", "scholarworks.uni.edu")
    args.allowed = ([h.lower() for h in args.allowed_host] if args.allowed_host
                    else env_list("ALLOWED_TARGET_HOSTS", "uni.access.preservica.com,digital.library.uni.edu"))
    return args


def old_address_key(value: str, dc_hosts: list[str]) -> tuple[str, str]:
    """Return (map, key) for an old ScholarWorks address, or raise ValueError."""
    u = urlparse(value.strip())
    if u.scheme not in ("http", "https") or not u.netloc:
        raise ValueError("dc_path must be a full address starting with https://")
    if (u.hostname or "").lower() not in dc_hosts:
        raise ValueError(f"dc_path host '{u.hostname}' isn't {' or '.join(dc_hosts)}")
    path = unquote(u.path).strip("/").lower()
    if path == "cgi/viewcontent.cgi":
        q = parse_qs(u.query)
        article, context = q.get("article", [""])[0], q.get("context", [""])[0].lower()
        if not (article.isdigit() and context):
            raise ValueError("viewcontent.cgi address needs article= and context=")
        return "dc_article", f"{context}/{article}"
    m = CONTEXT_FILE_RE.match(path)
    if m:
        return "dc_article", f"{m.group(1)}/{m.group(2)}"
    if u.query:
        raise ValueError("dc_path has a query string (only viewcontent.cgi addresses may)")
    if not path:
        raise ValueError("dc_path is the home page; that redirect is set in the Apache config")
    if any(c.isspace() for c in path):
        raise ValueError("dc_path contains a space")
    return "dc_paths", path


def new_address(value: str, allowed: list[str]) -> str:
    v = value.strip()
    if v.lower() in ("410", "gone"):
        return GONE
    if not v:
        raise ValueError("p_path is empty (use 410 for a withdrawn item)")
    if any(c.isspace() for c in v):
        raise ValueError("p_path contains a space")
    if not URL_CHARS_RE.match(v):
        raise ValueError("p_path has characters that must be percent-encoded (e.g. | as %7C)")
    u = urlparse(v)
    if u.scheme != "https" or not u.netloc:
        raise ValueError("p_path must start with https://")
    if allowed and (u.hostname or "").lower() not in allowed:
        raise ValueError(f"p_path host '{u.hostname}' isn't one of {', '.join(allowed)}")
    return v


def ark_key(value: str, naan: str) -> str:
    v = value.strip().replace("-", "")
    m = ARK_RE.match(v)
    if not m:
        raise ValueError(f"ark '{value}' should look like ark:/{naan}/xo2gohohwd")
    if m.group(1) != naan:
        raise ValueError(f"ark '{value}' isn't under our NAAN {naan}")
    return f"{m.group(1)}/{m.group(2).lower().rstrip('/')}"


def read_rows(path: Path) -> tuple[list[dict], str]:
    """Rows of one CSV, or an error message about its header."""
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        cols = [c.strip().lower() for c in (reader.fieldnames or [])]
        if "p_path" not in cols or not ({"dc_path", "ark"} & set(cols)):
            return [], (f"{path}: needs a p_path column and a dc_path or ark column; "
                        f"found {', '.join(cols) or 'none'}")
        rows = []
        for raw in reader:
            extra = raw.pop(None, None)   # more cells than the header has columns
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
            if extra and any(x.strip() for x in extra):
                row["_extra"] = "1"
            rows.append(row)
    return rows, ""


def main() -> int:
    args = parse_args()
    maps: dict[str, dict[str, str]] = {"dc_paths": {}, "dc_article": {}, "ark": {}}
    where: dict[tuple[str, str], str] = {}
    errors: list[str] = []
    samples: list[tuple[str, str, str]] = []   # (kind, original value, target)
    total = 0

    for path in map(Path, args.csv):
        rows, problem = read_rows(path)
        if problem:
            errors.append(problem)
            continue
        total += len(rows)
        for line, row in enumerate(rows, start=2):  # line 1 is the header
            if not any(row.values()):
                continue
            here = f"{path}:{line}"
            if row.pop("_extra", None):
                errors.append(f"{here}: more cells than columns: two rows on one line "
                              "(missing line break), or a comma inside an address")
                continue
            try:
                target = new_address(row.get("p_path", ""), args.allowed)
                keys = []
                if row.get("dc_path"):
                    keys.append(old_address_key(row["dc_path"], args.dc_hosts))
                if row.get("ark"):
                    keys.append(("ark", ark_key(row["ark"], args.naan)))
                if not keys:
                    raise ValueError("row has neither a dc_path nor an ark")
            except ValueError as exc:
                errors.append(f"{here}: {exc}")
                continue
            for name, key in keys:
                prev = maps[name].get(key)
                if prev is not None and prev != target:
                    errors.append(f"{here}: {key} already points elsewhere ({where[(name, key)]})")
                    continue
                if prev is None:
                    samples.append((name, row["dc_path"] if name != "ark" else key, target))
                maps[name][key] = target
                where[(name, key)] = here

    counts = ", ".join(f"{n}: {len(m)}" for n, m in maps.items())
    print(f"{' + '.join(args.csv)}: {total} rows ({counts}); {len(errors)} error(s)")
    for e in errors[:200]:
        print(f"  ERROR {e}")
    if len(errors) > 200:
        print(f"  ... and {len(errors) - 200} more")
    if errors:
        return 1
    if args.check:
        return 0

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, entries in maps.items():
        with (out / f"{name}.txt").open("w", encoding="utf-8") as fh:
            names = ", ".join(Path(c).name for c in args.csv)   # names only: paths vary between runs
            fh.write(f"# Built from {names} by scripts/build_map.py; edit those instead.\n")
            for key in sorted(entries):
                fh.write(f"{key} {entries[key]}\n")

    # Requests to try, with the answer each should get: tests and post-install checks.
    rng = random.Random(args.seed)
    picked = samples if args.sample == 0 or len(samples) <= args.sample else rng.sample(samples, args.sample)
    with (out / "sample.tsv").open("w", encoding="utf-8") as fh:
        fh.write("# host_role\tpath\texpected_status\texpected_location\n")
        for kind, original, target in picked:
            status, loc = ("410", "") if target == GONE else ("302" if kind == "ark" else "301", target)
            if kind == "ark":
                fh.write(f"ark\t/ark:/{original}\t{status}\t{loc}\n")
            else:
                u = urlparse(original)
                fh.write(f"dc\t{u.path or '/'}{'?' + u.query if u.query else ''}\t{status}\t{loc}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
