#!/usr/bin/env bash
# Install the redirects on an Apache server as the lookup files Apache reads.
#
#   scripts/install_maps.sh [redirect-list.csv ...]
#
# With no arguments it uses this repository's two lists: redirects.csv (DCDS's old
# ScholarWorks addresses) and the ARK redirects built from arks/arks_manifest.csv.
#
# Typical use: the server keeps a copy of this repository, and cron runs
#   git -C /opt/redirects pull -q --ff-only && /opt/redirects/scripts/install_maps.sh
# so a change merged into main is live within the cron interval.
#
# Each run:
#   1. validates the lists and builds the lookup files (scripts/build_map.py);
#      any bad row stops the run and nothing changes
#   2. does nothing if the result is the same as what's installed
#   3. refuses an update that loses more than MAX_DROP_PCT of the redirects
#      (FORCE=1 to accept it, e.g. after a deliberate clean-up)
#   4. installs into a new release directory and switches the "current" link in one
#      step; Apache notices the new files without a reload
#   5. tries a few redirects against this server and switches back if any fail
#   6. keeps the newest KEEP releases for manual roll-back
#
# Settings (environment variables):
#   REDIRECT_ROOT=/etc/httpd/redirects   where releases and the "current" link live
#   SMOKE=1                              set to 0 to skip step 5 (first install,
#                                        before Apache has been configured)
#   SMOKE_SERVER=https://127.0.0.1       where to send the test requests
#   SMOKE_HOST=scholarworks.uni.edu      site name for old-address checks
#   ARK_HOST=library.uni.edu             site name for ARK checks
#   SMOKE_ONLY=all                       all | dc | ark (library.uni.edu: ark)
#   SMOKE_INSECURE=0                     1 if 127.0.0.1's certificate can't be verified
#   MAX_DROP_PCT=20   KEEP=5   FORCE=0
#   ALLOWED_TARGET_HOSTS, DC_HOSTS, ARK_NAAN, PRESERVICA_ACCESS_HOST
#                                        read from the repository's settings.env unless
#                                        already set
#
# Needs: python3, httxt2dbm (httpd-tools on RHEL, apache2-utils on Debian), curl.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"

# Shared settings (plain KEY=value lines); anything already in the environment wins.
if [ -f "$REPO/settings.env" ]; then
    while IFS='=' read -r key value; do
        [ -n "${!key+set}" ] || export "$key=$value"
    done < <(grep -E '^[A-Z_][A-Z0-9_]*=' "$REPO/settings.env")
fi
REDIRECT_ROOT="${REDIRECT_ROOT:-/etc/httpd/redirects}"
SMOKE="${SMOKE:-1}"
SMOKE_SERVER="${SMOKE_SERVER:-https://127.0.0.1}"
SMOKE_HOST="${SMOKE_HOST:-scholarworks.uni.edu}"
ARK_HOST="${ARK_HOST:-library.uni.edu}"
SMOKE_ONLY="${SMOKE_ONLY:-all}"
SMOKE_INSECURE="${SMOKE_INSECURE:-0}"
SMOKE_COUNT="${SMOKE_COUNT:-5}"
MAX_DROP_PCT="${MAX_DROP_PCT:-20}"
KEEP="${KEEP:-5}"
FORCE="${FORCE:-0}"
TXT2DBM="${TXT2DBM:-$(command -v httxt2dbm || echo /usr/local/apache2/bin/httxt2dbm)}"
MAPS="dc_paths dc_article ark"

log() { echo "$(date +%FT%T) $*"; }
die() { log "ERROR: $*"; exit 1; }


[ -x "$TXT2DBM" ] || die "httxt2dbm not found (set TXT2DBM)"
command -v python3 >/dev/null || die "python3 is required"

RELEASES="$REDIRECT_ROOT/releases"
CURRENT="$REDIRECT_ROOT/current"
mkdir -p "$RELEASES"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# 1. Validate and build.
if [ "$#" -gt 0 ]; then
    LISTS=("$@")
else
    python3 "$REPO/arks/ark_manifest.py" --manifest "$REPO/arks/arks_manifest.csv" \
        redirects --out "$WORK/ark_redirects.csv" >/dev/null \
        || die "couldn't build the ARK redirects from arks/arks_manifest.csv"
    LISTS=("$REPO/redirects.csv" "$WORK/ark_redirects.csv")
fi
for f in "${LISTS[@]}"; do [ -f "$f" ] || die "no redirect list at $f"; done
mkdir -p "$WORK/maps"
python3 "$HERE/build_map.py" "${LISTS[@]}" --out "$WORK/maps" --sample 0 >"$WORK/build.log" 2>&1 \
    || { cat "$WORK/build.log"; die "the redirect lists have errors; nothing installed"; }

count() { grep -vc '^#' "$1" 2>/dev/null || true; }

# 2 and 3. Compare with what's installed.
if [ -d "$CURRENT" ]; then
    same=1
    for m in $MAPS; do cmp -s "$WORK/maps/$m.txt" "$CURRENT/$m.txt" || same=0; done
    if [ "$same" = 1 ]; then log "no changes"; exit 0; fi
    for m in $MAPS; do
        old=$(count "$CURRENT/$m.txt"); new=$(count "$WORK/maps/$m.txt")
        if [ "$old" -gt 0 ] && [ $(( (old - new) * 100 )) -gt $(( old * MAX_DROP_PCT )) ]; then
            [ "$FORCE" = 1 ] || die "$m would shrink from $old to $new (more than ${MAX_DROP_PCT}%); run with FORCE=1 if that's intended"
            log "WARNING: $m shrinks from $old to $new (FORCE=1)"
        fi
    done
fi

# 4. Install the release and switch to it.
STAMP="$(date +%Y%m%dT%H%M%S)"
REL="$RELEASES/$STAMP"
mkdir -p "$REL"
cp "$WORK"/maps/*.txt "$WORK/maps/sample.tsv" "$REL/"
for m in $MAPS; do
    "$TXT2DBM" -f default -i "$REL/$m.txt" -o "$REL/$m.db" >/dev/null || die "httxt2dbm failed for $m"
done
chmod -R a+rX "$REL"
PREVIOUS="$(readlink "$CURRENT" 2>/dev/null || true)"
ln -sfn "$REL" "$REDIRECT_ROOT/.current.new"
mv -Tf "$REDIRECT_ROOT/.current.new" "$CURRENT"
log "installed $STAMP ($(for m in $MAPS; do printf '%s=%s ' "$m" "$(count "$REL/$m.txt")"; done))"

# 5. Try a few redirects on this server; switch back on failure.
smoke() {
    local scheme host_ip port failures=0 tried=0
    scheme="${SMOKE_SERVER%%://*}"
    host_ip="${SMOKE_SERVER#*://}"; host_ip="${host_ip%%/*}"
    port="${host_ip##*:}"; [ "$port" = "$host_ip" ] && port=$([ "$scheme" = https ] && echo 443 || echo 80)
    host_ip="${host_ip%:*}"
    local k=(); [ "$SMOKE_INSECURE" = 1 ] && k=(-k)
    while IFS=$'\t' read -r role path want_status want_loc; do
        case "$role" in "#"*|"") continue ;; esac
        [ "$SMOKE_ONLY" = all ] || [ "$SMOKE_ONLY" = "$role" ] || continue
        local host="$SMOKE_HOST"; [ "$role" = ark ] && host="$ARK_HOST"
        local got
        got=$(curl -s -o /dev/null --noproxy '*' --max-time 10 ${k[@]+"${k[@]}"} \
              --resolve "$host:$port:$host_ip" -w '%{http_code} %{redirect_url}' \
              "$scheme://$host:$port$path") || got="000 "
        tried=$((tried + 1))
        if [ "$got" != "$want_status $want_loc" ]; then
            log "check failed: $host$path -> got '$got', expected '$want_status $want_loc'"
            failures=$((failures + 1))
        fi
        [ "$tried" -ge "$SMOKE_COUNT" ] && break
    done < <(shuf "$REL/sample.tsv" 2>/dev/null || cat "$REL/sample.tsv")
    log "checked $tried redirect(s), $failures failed"
    [ "$failures" -eq 0 ]
}

if [ "$SMOKE" = 1 ]; then
    sleep 1
    if ! smoke; then
        if [ -n "$PREVIOUS" ] && [ -d "$PREVIOUS" ]; then
            touch "$PREVIOUS"/*.db* 2>/dev/null || true   # make Apache re-read the old files
            ln -sfn "$PREVIOUS" "$REDIRECT_ROOT/.current.new"
            mv -Tf "$REDIRECT_ROOT/.current.new" "$CURRENT"
            mv "$REL" "$REL.failed"
            die "checks failed; switched back to $(basename "$PREVIOUS")"
        fi
        die "checks failed and there is no earlier release to switch back to"
    fi
fi

# 6. Keep the newest KEEP releases (never the current one).
ls -1d "$RELEASES"/*/ 2>/dev/null | sort -r | tail -n +"$((KEEP + 1))" | while read -r old; do
    [ "$(readlink -f "$old")" = "$(readlink -f "$CURRENT")" ] || rm -rf "$old"
done
log "done"
