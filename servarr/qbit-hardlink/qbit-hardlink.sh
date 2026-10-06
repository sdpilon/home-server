#!/usr/bin/env bash
#
# qbit-hardlink.sh
#
# Hardlinks a finished qBittorrent torrent into a directory tree organized
# by category (the same job Sonarr/Radarr do for the categories they manage).
# Intended for categories those apps don't touch (software, books, etc.).
#
# ── qBittorrent setup ────────────────────────────────────────────────────
# Tools > Options > Downloads > Run external program on torrent finished
#
#   /path/to/qbit-hardlink.sh -n "%N" -l "%L" -f "%F" -i "%I"
#
# Flags are used (rather than positional args) so argument order in the
# qBittorrent field can never silently break the script.
#
#   %N  Torrent name
#   %L  Category
#   %F  Content path (the actual file, or the top-level folder — qBittorrent
#       resolves this correctly for both single-file and multi-file torrents)
#   %I  Info hash (only used for log correlation)
#
# ── Category → destination mapping ──────────────────────────────────────
# Edit categories.conf (same directory as this script by default, override
# with -c). One "category=/absolute/dest/dir" pair per line. Blank lines
# and lines starting with # are ignored.
#
# Categories not listed in the map are skipped by default (safe default —
# won't scatter files you didn't plan for). Pass -u to instead fall back to
# UNMATCHED_ROOT/<category>/ for anything unmapped.
#
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
CONF_FILE="${SCRIPT_DIR}/categories.conf"
LOG_FILE="${SCRIPT_DIR}/qbit-hardlink.log"
UNMATCHED_ROOT=""
DRY_RUN=0
FALLBACK_UNMATCHED=0

name=""
category=""
content_path=""
info_hash=""

usage() {
    echo "Usage: $0 -n NAME -l CATEGORY -f CONTENT_PATH [-i INFO_HASH] [-c CONF_FILE] [-L LOG_FILE] [-u UNMATCHED_ROOT] [-d]" >&2
    exit 2
}

while getopts ":n:l:f:i:c:L:u:d" opt; do
    case "$opt" in
        n) name="$OPTARG" ;;
        l) category="$OPTARG" ;;
        f) content_path="$OPTARG" ;;
        i) info_hash="$OPTARG" ;;
        c) CONF_FILE="$OPTARG" ;;
        L) LOG_FILE="$OPTARG" ;;
        u) FALLBACK_UNMATCHED=1; UNMATCHED_ROOT="$OPTARG" ;;
        d) DRY_RUN=1 ;;
        *) usage ;;
    esac
done

# A logging failure (e.g. an unwritable log path) must never take down the
# actual hardlink operation, so this is deliberately allowed to fail quietly.
log() {
    printf '%s [%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "${info_hash:0:8}" "$1" >> "$LOG_FILE" 2>/dev/null || true
}

fail() {
    log "ERROR: $1"
    echo "ERROR: $1" >&2
    exit 1
}

[[ -n "$content_path" ]] || fail "no content path given (-f). Check the qBittorrent command line."
[[ -e "$content_path" ]] || fail "content path does not exist on disk: $content_path"

if [[ -z "$category" ]]; then
    log "skip: '${name}' has no category set"
    exit 0
fi

[[ -f "$CONF_FILE" ]] || fail "category map not found: $CONF_FILE"

# Look up the destination root for this category.
dest_root=""
while IFS='=' read -r conf_cat conf_dest; do
    [[ -z "$conf_cat" || "$conf_cat" == \#* ]] && continue
    conf_cat="$(echo -n "$conf_cat" | xargs)"
    conf_dest="$(echo -n "$conf_dest" | xargs)"
    if [[ "$conf_cat" == "$category" ]]; then
        dest_root="$conf_dest"
        break
    fi
done < "$CONF_FILE"

if [[ -z "$dest_root" ]]; then
    if [[ "$FALLBACK_UNMATCHED" -eq 1 && -n "$UNMATCHED_ROOT" ]]; then
        dest_root="${UNMATCHED_ROOT%/}/${category}"
        log "category '${category}' not in map, falling back to ${dest_root}"
    else
        log "skip: category '${category}' has no mapping in ${CONF_FILE}"
        exit 0
    fi
fi

dest_path="${dest_root%/}/$(basename -- "$content_path")"

if [[ -e "$dest_path" ]]; then
    log "skip: already present at ${dest_path} (looks like this torrent was already processed)"
    exit 0
fi

# Hardlinks only work within the same filesystem. Fail loudly and early
# rather than silently falling back to a slow copy.
src_dev="$(stat -c %d -- "$content_path")"
mkdir -p -- "$dest_root"
dest_dev="$(stat -c %d -- "$dest_root")"
if [[ "$src_dev" != "$dest_dev" ]]; then
    fail "source and destination are on different filesystems (hardlinks impossible): $(dirname -- "$content_path") vs $dest_root"
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
    log "DRY RUN: would hardlink '${content_path}' -> '${dest_path}'"
    echo "Would hardlink: ${content_path} -> ${dest_path}"
    exit 0
fi

# cp -al: recursive, preserves attributes, hardlinks file contents instead
# of copying bytes. Works uniformly whether content_path is a single file
# or a directory of files.
if cp -al -- "$content_path" "$dest_path"; then
    log "linked '${name}' [${category}] -> ${dest_path}"
else
    fail "cp -al failed for '${content_path}' -> '${dest_path}'"
fi