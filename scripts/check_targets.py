#!/usr/bin/env python3
"""Check that every active redirect target in the manifest still resolves.

Sends a HEAD request (falling back to GET if HEAD is refused) to each unique
target_url, slowly, and writes failures to a CSV. Meant for a scheduled GitLab
pipeline, not every commit, so Preservica isn't hit with thousands of requests
on each merge request.

Usage:
    python scripts/check_targets.py manifest/redirects.csv manifest/overrides.csv \
        --report target_failures.csv --pause 0.5
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from manifest import read_manifest  # noqa: E402


def probe(url: str, timeout: float) -> str:
    for method in ("HEAD", "GET"):
        req = urllib.request.Request(url, method=method, headers={"User-Agent": "redirect-target-check/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return str(resp.status)
        except urllib.error.HTTPError as exc:
            if method == "HEAD" and exc.code in (403, 405, 501):
                continue
            return str(exc.code)
        except (urllib.error.URLError, TimeoutError) as exc:
            return f"error: {getattr(exc, 'reason', exc)}"
    return "error"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--report", default="target_failures.csv")
    ap.add_argument("--pause", type=float, default=0.5)
    ap.add_argument("--timeout", type=float, default=20)
    ap.add_argument("--limit", type=int, default=0, help="check at most N targets (0 = all)")
    args = ap.parse_args()

    targets: dict[str, str] = {}
    for f in args.files:
        for r in read_manifest(f):
            if r["status"] == "active" and r["target_url"]:
                targets.setdefault(r["target_url"], r.where)

    urls = sorted(targets)[: args.limit or None]
    failures = []
    for i, url in enumerate(urls, 1):
        status = probe(url, args.timeout)
        if not status.startswith("2") and not status.startswith("3"):
            failures.append({"target_url": url, "status": status, "manifest_row": targets[url]})
        if i % 200 == 0:
            print(f"{i}/{len(urls)} checked, {len(failures)} failing", file=sys.stderr)
        time.sleep(args.pause)

    with open(args.report, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["target_url", "status", "manifest_row"])
        w.writeheader()
        w.writerows(failures)
    print(f"checked {len(urls)} targets, {len(failures)} failing (see {args.report})")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
