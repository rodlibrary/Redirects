#!/usr/bin/env bash
# Start a throwaway Apache on 127.0.0.1:8080 with this repository's rules and check:
#   1. tests/expected.tsv against tests/fixtures/redirects.csv (every kind of redirect)
#   2. every redirect in the real redirects.csv (a sample of 500 once it's bigger)
#   3. that build_map.py rejects tests/fixtures/bad.csv, and two lists that disagree
#
# Works on Ubuntu/Debian (as in GitHub Actions), RHEL, or the official httpd:2.4 image.
# Run as root (sudo), so Apache can switch to its usual user.
# Usage: tests/run_apache_test.sh [redirects.csv ...]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TEST_ROOT="$(mktemp -d)"
trap 'stop_apache; [ -n "${KEEP_TEST_ROOT:-}" ] || rm -rf "$TEST_ROOT"' EXIT

if [ -x /usr/local/apache2/bin/httpd ]; then
    HTTPD=/usr/local/apache2/bin/httpd; TXT2DBM=/usr/local/apache2/bin/httxt2dbm
    MODULE_DIR=/usr/local/apache2/modules; RUN_USER=daemon
elif [ -x /usr/sbin/apache2 ]; then
    HTTPD=/usr/sbin/apache2; TXT2DBM=$(command -v httxt2dbm)
    MODULE_DIR=/usr/lib/apache2/modules; RUN_USER=www-data
else
    HTTPD=$(command -v httpd); TXT2DBM=$(command -v httxt2dbm)
    MODULE_DIR=/usr/lib64/httpd/modules; RUN_USER=apache
fi
export MODULE_DIR TEST_ROOT REPO_ROOT RUN_USER   # read by tests/httpd-test.conf

# A copy of the "moved" page: Apache's user may not be able to read the checkout
# (on GitHub's runners /home/runner is private), as on the servers it lives in /etc.
cp -r "$REPO_ROOT/apache/www" "$TEST_ROOT/www"

if [ "$#" -gt 0 ]; then REAL=("$@"); else REAL=("$REPO_ROOT/redirects.csv"); fi

build() {  # build() CSV...: lookup files from the lists into $TEST_ROOT/maps
    rm -rf "$TEST_ROOT/maps"
    python3 "$REPO_ROOT/scripts/build_map.py" "$@" --out "$TEST_ROOT/maps" --sample 500 --seed 1
    for m in dc_paths dc_article; do
        "$TXT2DBM" -f default -i "$TEST_ROOT/maps/$m.txt" -o "$TEST_ROOT/maps/$m.db" >/dev/null
    done
    chmod -R a+rX "$TEST_ROOT"
}
start_apache() {
    "$HTTPD" -f "$REPO_ROOT/tests/httpd-test.conf" -t
    "$HTTPD" -f "$REPO_ROOT/tests/httpd-test.conf" -k start
    for _ in $(seq 1 50); do [ -s "$TEST_ROOT/httpd.pid" ] && break; sleep 0.1; done
    sleep 0.5
}
stop_apache() {
    [ -s "$TEST_ROOT/httpd.pid" ] || return 0
    kill "$(cat "$TEST_ROOT/httpd.pid")" 2>/dev/null || true
    for _ in $(seq 1 50); do kill -0 "$(cat "$TEST_ROOT/httpd.pid" 2>/dev/null)" 2>/dev/null || break; sleep 0.1; done
    rm -f "$TEST_ROOT/httpd.pid"
}
check() { python3 "$REPO_ROOT/tests/check_redirects.py" "$1" --server http://127.0.0.1:8080 --quiet; }

status=0
echo "== 1. every kind of redirect (tests/fixtures + tests/expected.tsv) =="
build "$REPO_ROOT/tests/fixtures/redirects.csv"
start_apache
check "$REPO_ROOT/tests/expected.tsv" || status=1
stop_apache

echo "== 2. the real redirects (${REAL[*]##*/}) =="
build "${REAL[@]}"
start_apache
check "$TEST_ROOT/maps/sample.tsv" || status=1
stop_apache

echo "== 3. bad rows are rejected (tests/fixtures/bad.csv) =="
if python3 "$REPO_ROOT/scripts/build_map.py" --check "$REPO_ROOT/tests/fixtures/bad.csv" >"$TEST_ROOT/bad.log"; then
    echo "FAIL: bad.csv was accepted"; status=1
else
    n=$(grep -c "ERROR" "$TEST_ROOT/bad.log" || true)
    if [ "$n" -eq 9 ]; then echo "ok   all 9 bad rows rejected"; else echo "FAIL: expected 9 errors, got $n"; cat "$TEST_ROOT/bad.log"; status=1; fi
fi
if python3 "$REPO_ROOT/scripts/build_map.py" --check "$REPO_ROOT/tests/fixtures/redirects.csv" \
        "$REPO_ROOT/tests/fixtures/conflict.csv" >"$TEST_ROOT/conflict.log"; then
    echo "FAIL: two lists that disagree were accepted"; status=1
elif grep -q "already points elsewhere (.*fixtures/redirects.csv:" "$TEST_ROOT/conflict.log"; then
    echo "ok   two lists that disagree are rejected, naming both lines"
else
    echo "FAIL: unexpected output for conflict.csv"; cat "$TEST_ROOT/conflict.log"; status=1
fi

if [ "$status" -ne 0 ] && [ -f "$TEST_ROOT/error.log" ]; then
    echo "== Apache error log (last lines) =="; tail -n 40 "$TEST_ROOT/error.log"
fi
exit "$status"
