#!/usr/bin/env python3
"""Validate the redirect manifest before it is built into Apache maps.

Usage:
    python scripts/validate.py manifest/redirects.csv manifest/overrides.csv \
        --allowed-host digital.library.uni.edu

Allowed hosts can also come from the ALLOWED_TARGET_HOSTS environment variable
(comma-separated), and the NAAN from ARK_NAAN, which is how the GitHub workflows
pass them (from settings.env). ARKs under any other NAAN are rejected.

Exit status is 1 if any errors were found, so the checks fail the pull request.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from manifest import check_rows, parse_hosts, read_manifest  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", help="manifest CSV files (base first, then overrides)")
    ap.add_argument("--allowed-host", action="append", help="permitted target hostname (repeatable)")
    ap.add_argument("--naan", default=os.environ.get("ARK_NAAN", ""),
                    help="our ARK NAAN (e.g. 32498); ARKs under other NAANs are errors")
    ap.add_argument("--max-errors", type=int, default=200, help="stop printing after this many")
    args = ap.parse_args()

    hosts = parse_hosts(args.allowed_host) | parse_hosts([os.environ.get("ALLOWED_TARGET_HOSTS", "")])
    if not hosts:
        print("warning: no allowed target hosts set; any https host will be accepted", file=sys.stderr)

    all_errors: list[str] = []
    total = Counter()
    for f in args.files:
        if not Path(f).exists():
            print(f"note: {f} not found, skipping")
            continue
        rows = read_manifest(f)
        total.update(r["status"] for r in rows)
        errs = check_rows(rows, hosts, args.naan)
        print(f"{f}: {len(rows)} rows, {len(errs)} error(s)")
        all_errors.extend(errs)

    for e in all_errors[: args.max_errors]:
        print(f"  ERROR {e}")
    if len(all_errors) > args.max_errors:
        print(f"  ... and {len(all_errors) - args.max_errors} more")

    print("status counts: " + ", ".join(f"{k}={v}" for k, v in sorted(total.items())))
    if total.get("unmatched"):
        print(f"note: {total['unmatched']} unmatched row(s) will not be redirected")
    return 1 if all_errors else 0


if __name__ == "__main__":
    sys.exit(main())
