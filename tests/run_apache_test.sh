#!/usr/bin/env bash
# Build maps from the test fixtures, start a throwaway Apache on 127.0.0.1:8080,
# and check every request in tests/expected.tsv (plus the generated smoke sample).
#
# Works with the official httpd:2.4 image and with Debian/Ubuntu apache2.
# Usage: tests/run_apache_test.sh                       # fixtures + tests/expected.tsv
#        tests/run_apache_test.sh manifest.csv overrides.csv  # real manifest, sampled checks only
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MANIFEST="${1:-$REPO_ROOT/tests/fixtures/redirects.csv}"
OVERRIDES="${2:-$REPO_ROOT/tests/fixtures/overrides.csv}"
# Hand-written cases only apply to the fixtures.
EXPECTED=""; [ $# -eq 0 ] && EXPECTED="$REPO_ROOT/tests/expected.tsv"
TEST_ROOT="$(mktemp -d)"
trap 'kill "$(cat "$TEST_ROOT/httpd.pid" 2>/dev/null)" 2>/dev/null || true; [ -n "${KEEP_TEST_ROOT:-}" ] || rm -rf "$TEST_ROOT"' EXIT

# Locate httpd, httxt2dbm and the module directory for this distribution.
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

python3 "$REPO_ROOT/scripts/build_maps.py" --manifest "$MANIFEST" --overrides "$OVERRIDES" \
    --out "$TEST_ROOT/maps" --dc-host scholarworks.uni.edu --ark-host library.uni.edu --seed 1 --sample 200

for m in dc_paths dc_article ark; do
    "$TXT2DBM" -f default -i "$TEST_ROOT/maps/$m.txt" -o "$TEST_ROOT/maps/$m.db"
done
mkdir -p "$TEST_ROOT/site" && echo "library website" > "$TEST_ROOT/site/index.html"
chmod -R a+rX "$TEST_ROOT"

# Apache resolves ${VAR} in the config from the environment.
export MODULE_DIR TEST_ROOT REPO_ROOT RUN_USER
ARGS=(-f "$REPO_ROOT/tests/httpd-test.conf")

"$HTTPD" "${ARGS[@]}" -t
"$HTTPD" "${ARGS[@]}" -k start
for _ in $(seq 1 50); do [ -s "$TEST_ROOT/httpd.pid" ] && break; sleep 0.1; done
sleep 0.5

status=0
if [ -n "$EXPECTED" ]; then
    echo "== hand-written cases (tests/expected.tsv) =="
    python3 "$REPO_ROOT/scripts/check_redirects.py" "$EXPECTED" \
        --server http://127.0.0.1:8080 --quiet || status=1
fi
echo "== generated sample (smoke_test.tsv) =="
python3 "$REPO_ROOT/scripts/check_redirects.py" "$TEST_ROOT/maps/smoke_test.tsv" \
    --server http://127.0.0.1:8080 --quiet || status=1

if [ "$status" -ne 0 ]; then
    echo "== Apache error log (rewrite trace) =="
    tail -n 80 "$TEST_ROOT/error.log"
fi
exit "$status"
