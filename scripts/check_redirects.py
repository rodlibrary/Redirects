#!/usr/bin/env python3
"""Send requests to an Apache server and check status codes and Location headers.

Reads a TSV of: host <TAB> path <TAB> expected_status <TAB> expected_location
(lines starting with # are skipped; expected_location may be blank).

The request goes to --server (an IP or hostname, optionally :port) while the
Host header, and for https the TLS SNI name, use the host column. That lets you
test the real vhosts on a server before DNS points at it, or check production
from the server itself.

Usage:
    python scripts/check_redirects.py tests/expected.tsv --server http://127.0.0.1:8080
    python scripts/check_redirects.py maps/smoke_test.tsv --server https://127.0.0.1
"""

from __future__ import annotations

import argparse
import http.client
import socket
import ssl
import sys
from urllib.parse import urlparse


class SNIConnection(http.client.HTTPSConnection):
    """HTTPS to a fixed address while presenting a chosen hostname for SNI."""

    def __init__(self, addr: str, port: int, sni: str, context: ssl.SSLContext, timeout: float):
        super().__init__(addr, port, context=context, timeout=timeout)
        self._sni = sni
        self._ctx = context

    def connect(self):
        sock = socket.create_connection((self.host, self.port), self.timeout)
        self.sock = self._ctx.wrap_socket(sock, server_hostname=self._sni)


def request(server: str, host: str, path: str, insecure: bool, timeout: float):
    u = urlparse(server)
    addr = u.hostname or "127.0.0.1"
    if u.scheme == "https":
        ctx = ssl.create_default_context()
        if insecure:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        conn = SNIConnection(addr, u.port or 443, host, ctx, timeout)
    else:
        conn = http.client.HTTPConnection(addr, u.port or 80, timeout=timeout)
    try:
        conn.request("GET", path, headers={"Host": host, "User-Agent": "redirect-check/1.0"})
        resp = conn.getresponse()
        return str(resp.status), resp.getheader("Location", "")
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tsv")
    ap.add_argument("--server", default="http://127.0.0.1")
    ap.add_argument("--insecure", action="store_true", help="skip TLS certificate checks")
    ap.add_argument("--timeout", type=float, default=10)
    ap.add_argument("--quiet", action="store_true", help="only print failures")
    ap.add_argument("--host", help="send every request with this Host name instead of the host column")
    ap.add_argument("--only", choices=["all", "ark", "legacy"], default="all",
                    help="check only ARK paths, only legacy ScholarWorks paths, or all")
    args = ap.parse_args()

    passed = failed = 0
    with open(args.tsv, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.rstrip("\n")
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                print(f"line {n}: expected at least 3 tab-separated columns", file=sys.stderr)
                failed += 1
                continue
            host, path, want_status = parts[0], parts[1], parts[2]
            is_ark = path.startswith("/ark:")
            if (args.only == "ark" and not is_ark) or (args.only == "legacy" and is_ark):
                continue
            host = args.host or host
            want_loc = parts[3] if len(parts) > 3 else ""
            try:
                status, loc = request(args.server, host, path, args.insecure, args.timeout)
            except OSError as exc:
                print(f"FAIL {host}{path}: {exc}")
                failed += 1
                continue
            ok = status == want_status and (not want_loc or loc == want_loc)
            if ok:
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
