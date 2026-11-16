#!/usr/bin/env python3
"""Send requests to an Apache server and check status codes and Location headers.

Reads a TSV of: host <TAB> path <TAB> expected_status <TAB> expected_location
(# lines skipped; expected_location may be blank). The host column may also be
"dc" or "ark" (as in build_map.py's sample.tsv), meaning --dc-host or --ark-host.
Requests go to --server, with the Host header set from the host column.

    python3 tests/check_redirects.py tests/expected.tsv --server http://127.0.0.1:8080
"""
from __future__ import annotations

import argparse
import http.client
import sys
from urllib.parse import urlparse


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tsv")
    ap.add_argument("--server", default="http://127.0.0.1:8080")
    ap.add_argument("--dc-host", default="scholarworks.uni.edu")
    ap.add_argument("--ark-host", default="library.uni.edu")
    ap.add_argument("--quiet", action="store_true", help="only print failures")
    args = ap.parse_args()
    alias = {"dc": args.dc_host, "ark": args.ark_host}
    u = urlparse(args.server)
    passed = failed = 0
    with open(args.tsv, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line.strip() or line.startswith("#"):
                continue
            host, path, want_status, *rest = line.split("\t")
            host = alias.get(host, host)
            want_loc = rest[0] if rest else ""
            conn_cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
            conn = conn_cls(u.hostname, u.port, timeout=10)
            try:
                conn.request("GET", path, headers={"Host": host})
                resp = conn.getresponse()
                status, loc = str(resp.status), resp.getheader("Location", "")
            except OSError as exc:
                status, loc = "error", str(exc)
            finally:
                conn.close()
            if status == want_status and (not want_loc or loc == want_loc):
                passed += 1
                if not args.quiet:
                    print(f"ok   {status} {host}{path}")
            else:
                failed += 1
                print(f"FAIL {host}{path}\n     expected {want_status} {want_loc}\n     got      {status} {loc}")
    print(f"{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
