#!/usr/bin/env bash
# Fetch the latest redirect maps published by GitHub Actions and install them for Apache.
#
# Runs on the Apache server (from the systemd timer in this directory, or by hand).
# The server pulls from GitHub, so GitHub never needs SSH access to production.
# The Checks workflow publishes redirect-maps.zip on the "maps" release only after
# every check on main has passed.
#
# Layout under $REDIRECT_ROOT:
#   releases/20261013T184500Z/{dc_paths,dc_article,ark}.db  + .txt, stats.json, smoke_test.tsv
#   current -> releases/20261013T184500Z     (what redirects.conf reads)
#
# Each run:
#   1. downloads redirect-maps.zip (or uses LOCAL_ARTIFACT=/path/to/redirect-maps.zip)
#   2. stops if nothing changed since the installed release
#   3. refuses the update if any map shrank by more than MAX_DROP_PCT (FORCE=1 overrides)
#   4. builds DBM files into a new release directory
#   5. switches the "current" symlink atomically; Apache picks up the new maps on its own
#   6. runs the smoke-test sample against this server and rolls back on failure
#   7. keeps the newest $KEEP releases
#
# Settings come from /etc/dc-redirects/pull.env (or the environment):
#   GITHUB_REPO=rodlibrary/redirects     # owner/name of the repository
#   GITHUB_TOKEN=github_pat_...          # fine-grained token: this repository only, Contents read-only
#   RELEASE_TAG=maps  ASSET=redirect-maps.zip
#   REDIRECT_ROOT=/etc/httpd/redirects
#   SMOKE_SERVER=https://127.0.0.1       # where to send smoke-test requests
#   SMOKE_HOST=scholarworks.uni.edu      # Host/SNI name this server answers to
#   SMOKE_ONLY=all                       # all | ark (e.g. on library.uni.edu) | legacy
#   MAX_DROP_PCT=2  KEEP=10
set -euo pipefail

ENV_FILE="${ENV_FILE:-/etc/dc-redirects/pull.env}"
# shellcheck disable=SC1090
[ -f "$ENV_FILE" ] && . "$ENV_FILE"

RELEASE_TAG="${RELEASE_TAG:-maps}"
ASSET="${ASSET:-redirect-maps.zip}"
REDIRECT_ROOT="${REDIRECT_ROOT:-/etc/httpd/redirects}"
SMOKE_SERVER="${SMOKE_SERVER:-https://127.0.0.1}"
MAX_DROP_PCT="${MAX_DROP_PCT:-2}"
KEEP="${KEEP:-10}"
FORCE="${FORCE:-0}"
TXT2DBM="${TXT2DBM:-$(command -v httxt2dbm || echo /usr/local/apache2/bin/httxt2dbm)}"

log() { echo "$(date -u +%FT%TZ) $*"; }
die() { log "ERROR: $*"; exit 1; }

command -v python3 >/dev/null || die "python3 is required"
[ -x "$TXT2DBM" ] || die "httxt2dbm not found (set TXT2DBM)"

RELEASES="$REDIRECT_ROOT/releases"
CURRENT="$REDIRECT_ROOT/current"
mkdir -p "$RELEASES"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# 1. Get the maps.
if [ -n "${LOCAL_ARTIFACT:-}" ]; then
    cp "$LOCAL_ARTIFACT" "$WORK/artifacts.zip"
else
    : "${GITHUB_REPO:?set GITHUB_REPO}" "${GITHUB_TOKEN:?set GITHUB_TOKEN}"
    # Accept a pasted repository URL (https://github.com/rodlibrary/redirects): keep owner/name.
    GITHUB_REPO="$(printf '%s' "$GITHUB_REPO" | sed -E 's#^https?://github\.com/##; s#\.git$##; s#/+$##')"
    API="${GITHUB_API:-https://api.github.com}"
    AUTH=(--header "Authorization: Bearer $GITHUB_TOKEN" --header "X-GitHub-Api-Version: 2022-11-28")
    code="$(curl --silent --show-error --max-time 60 "${AUTH[@]}" \
        --header "Accept: application/vnd.github+json" --write-out '%{http_code}' \
        --output "$WORK/release.json" "$API/repos/$GITHUB_REPO/releases/tags/$RELEASE_TAG")" \
        || die "could not reach $API (check this server's outbound HTTPS access to GitHub)"
    case "$code" in
        200) ;;
        401) die "GitHub rejected GITHUB_TOKEN (HTTP 401): it is wrong, expired or revoked" ;;
        403) die "GITHUB_TOKEN can't read $GITHUB_REPO (HTTP 403): it needs Contents: Read-only on that repository, and the organization may need to approve it" ;;
        404) die "no $RELEASE_TAG release in $GITHUB_REPO (HTTP 404): it appears after the Checks workflow first passes on main; also check GITHUB_REPO and that the token covers that repository" ;;
        *)   die "GitHub answered HTTP $code for the $RELEASE_TAG release of $GITHUB_REPO" ;;
    esac
    ASSET_URL="$(python3 - "$WORK/release.json" "$ASSET" <<'PY'
import json, sys
assets = json.load(open(sys.argv[1])).get("assets", [])
print(next((a["url"] for a in assets if a["name"] == sys.argv[2]), ""))
PY
)"
    [ -n "$ASSET_URL" ] || die "the $RELEASE_TAG release has no $ASSET (has the Checks workflow passed on main yet?)"
    # GitHub answers with a redirect to its file storage; curl doesn't forward the token there.
    curl --fail --silent --show-error --location --max-time 300 "${AUTH[@]}" \
        --header "Accept: application/octet-stream" \
        --output "$WORK/artifacts.zip" "$ASSET_URL" \
        || die "could not download $ASSET from the $RELEASE_TAG release"
fi
python3 -c "import sys,zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" \
    "$WORK/artifacts.zip" "$WORK/unpacked"
NEW="$WORK/unpacked/maps"
for f in dc_paths.txt dc_article.txt ark.txt stats.json smoke_test.tsv; do
    [ -s "$NEW/$f" ] || die "$ASSET is missing maps/$f"
done
CHECKER="$WORK/unpacked/scripts/check_redirects.py"

# 2 and 3. Compare with what's installed.
if [ -f "$CURRENT/stats.json" ]; then
    verdict=$(python3 - "$CURRENT/stats.json" "$NEW/stats.json" "$MAX_DROP_PCT" <<'PY'
import json, sys
old = json.load(open(sys.argv[1]))["maps"]
new = json.load(open(sys.argv[2]))["maps"]
limit = float(sys.argv[3])
if all(old.get(k, {}).get("sha256") == v["sha256"] for k, v in new.items()):
    print("unchanged"); sys.exit()
problems = []
for name, o in old.items():
    n = new.get(name, {}).get("entries", 0)
    if o["entries"] and (o["entries"] - n) * 100.0 / o["entries"] > limit:
        problems.append(f"{name}: {o['entries']} -> {n}")
print("shrank " + "; ".join(problems) if problems else "ok")
PY
)
    case "$verdict" in
        unchanged) log "maps unchanged; nothing to do"; exit 0 ;;
        shrank*)   [ "$FORCE" = 1 ] || die "refusing update, maps $verdict (more than ${MAX_DROP_PCT}% drop; rerun with FORCE=1 if intended)"
                   log "WARNING: $verdict (FORCE=1)" ;;
    esac
fi

# 4. Build the release.
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
# Two runs in the same second (the timer and a manual run) must not share a directory.
n=1; while [ -e "$RELEASES/$STAMP" ] || [ -e "$RELEASES/$STAMP.failed" ]; do n=$((n + 1)); STAMP="$(date -u +%Y%m%dT%H%M%SZ)-$n"; done
REL="$RELEASES/$STAMP"
mkdir -p "$REL"
cp "$NEW"/*.txt "$NEW/stats.json" "$NEW/smoke_test.tsv" "$REL/"
for m in dc_paths dc_article ark; do
    "$TXT2DBM" -f default -i "$REL/$m.txt" -o "$REL/$m.db" >/dev/null || die "httxt2dbm failed for $m"
done
chmod -R a+rX "$REL"

# 5. Switch atomically: build the new symlink beside the old one, then rename over it.
PREVIOUS="$(readlink "$CURRENT" 2>/dev/null || true)"
ln -sfn "$REL" "$REDIRECT_ROOT/.current.new"
mv -Tf "$REDIRECT_ROOT/.current.new" "$CURRENT"
SUMMARY=$(python3 - "$REL/stats.json" <<'PY'
import json, sys
maps = json.load(open(sys.argv[1]))["maps"]
print(", ".join(f"{k}={v['entries']}" for k, v in maps.items()))
PY
)
log "installed release $STAMP ($SUMMARY)"

# 6. Smoke test against this server; roll back on failure.
# SKIP_SMOKE=1 is for the very first install, before Apache can start (it refuses
# to start until the map files exist).
if [ "${SKIP_SMOKE:-0}" = 1 ]; then log "smoke test skipped (SKIP_SMOKE=1)"; log "done"; exit 0; fi
sleep 1
INSECURE=""; [ "${SMOKE_INSECURE:-0}" = 1 ] && INSECURE="--insecure"
HOSTARG=(); [ -n "${SMOKE_HOST:-}" ] && HOSTARG=(--host "$SMOKE_HOST")
if ! python3 "$CHECKER" "$REL/smoke_test.tsv" --server "$SMOKE_SERVER" --quiet $INSECURE \
        ${HOSTARG[@]+"${HOSTARG[@]}"} --only "${SMOKE_ONLY:-all}"; then
    if [ -n "$PREVIOUS" ] && [ -d "$PREVIOUS" ]; then
        # Touch so Apache sees a changed modification time and reloads the old maps.
        touch "$PREVIOUS"/*.db* 2>/dev/null || true
        ln -sfn "$PREVIOUS" "$REDIRECT_ROOT/.current.new"
        mv -Tf "$REDIRECT_ROOT/.current.new" "$CURRENT"
        mv "$REL" "$REL.failed"
        die "smoke test failed; rolled back to $(basename "$PREVIOUS")"
    fi
    die "smoke test failed and there is no previous release to roll back to"
fi

# 7. Prune old releases (and failed ones older than the kept set).
ls -1d "$RELEASES"/*/ 2>/dev/null | sort -r | tail -n +"$((KEEP + 1))" | while read -r old; do
    [ "$(readlink -f "$old")" = "$(readlink -f "$CURRENT")" ] || rm -rf "$old"
done
log "done"
