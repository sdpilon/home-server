#!/usr/bin/env bash
#
# qbit-hardlink-merge.sh
#
# Completes a partially-linked directory torrent.
#
# qbit-hardlink.sh skips a torrent whenever its destination path already
# exists, so a destination folder that is missing some of its files (a stray
# delete, an interrupted first run, an importer that moved one file away)
# never gets repaired. This walks the torrent's content path and hardlinks
# any file whose relative path is missing under the existing destination.
#
# Intended for directory torrents. A single-file torrent either links cleanly
# the first time or is already present, so just run qbit-hardlink.sh for those.
#
# ── Usage ────────────────────────────────────────────────────────────────
#   qbit-hardlink-merge.sh -f CONTENT_PATH [-l CATEGORY | -t DEST_DIR]
#                          [-n NAME] [-i INFO_HASH] [-c CONF_FILE]
#                          [-L LOG_FILE] [-d]
#
#   -f  content path (torrent folder), same as qbit-hardlink.sh
#   -l  category; destination is derived from the map as
#       <dest_root>/<basename of content path>
#   -t  explicit destination directory to merge into (overrides -l)
#   -n/-i/-c/-L/-d as in qbit-hardlink.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
CONF_FILE="${SCRIPT_DIR}/categories.conf"
LOG_FILE="${SCRIPT_DIR}/qbit-hardlink.log"
DRY_RUN=0

name=""
category=""
content_path=""
info_hash=""
dest_dir=""

usage() {
    awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "${BASH_SOURCE[0]}" >&2
    exit "${1:-2}"
}

while getopts ":n:l:f:i:c:L:t:dh" opt; do
    case "$opt" in
        n) name="$OPTARG" ;;
        l) category="$OPTARG" ;;
        f) content_path="$OPTARG" ;;
        i) info_hash="$OPTARG" ;;
        c) CONF_FILE="$OPTARG" ;;
        L) LOG_FILE="$OPTARG" ;;
        t) dest_dir="$OPTARG" ;;
        d) DRY_RUN=1 ;;
        h) usage 0 ;;
        *) usage ;;
    esac
done

log() {
    printf '%s [%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "${info_hash:0:8}" "$1" >> "$LOG_FILE" 2>/dev/null || true
}

fail() {
    log "ERROR: $1"
    echo "ERROR: $1" >&2
    exit 1
}

[[ -n "$content_path" ]] || fail "no content path given (-f)."
[[ -e "$content_path" ]] || fail "content path does not exist on disk: $content_path"
[[ -d "$content_path" ]] || fail "content path is not a directory; run qbit-hardlink.sh instead: $content_path"

content_path="${content_path%/}"

if [[ -z "$dest_dir" ]]; then
    [[ -n "$category" ]] || fail "need either -l CATEGORY or -t DEST_DIR"
    [[ -f "$CONF_FILE" ]] || fail "category map not found: $CONF_FILE"

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
    [[ -n "$dest_root" ]] || fail "category '${category}' has no mapping in ${CONF_FILE}"
    dest_dir="${dest_root%/}/$(basename -- "$content_path")"
fi

dest_dir="${dest_dir%/}"
[[ -d "$dest_dir" ]] || fail "destination dir does not exist: $dest_dir (nothing to merge; run qbit-hardlink.sh instead)"

# Hardlinks only work within one filesystem.
src_dev="$(stat -c %d -- "$content_path")"
dest_dev="$(stat -c %d -- "$dest_dir")"
if [[ "$src_dev" != "$dest_dev" ]]; then
    fail "source and destination are on different filesystems (hardlinks impossible): $content_path vs $dest_dir"
fi

linked=0
present=0
while IFS= read -r -d '' f; do
    rel="${f#"$content_path"/}"
    target="$dest_dir/$rel"
    if [[ -e "$target" ]]; then
        present=$((present + 1))
        continue
    fi
    if [[ "$DRY_RUN" -eq 1 ]]; then
        echo "Would link: $f -> $target"
        linked=$((linked + 1))
        continue
    fi
    mkdir -p -- "$(dirname -- "$target")"
    if cp -al -- "$f" "$target"; then
        echo "linked: $rel"
        linked=$((linked + 1))
    else
        fail "failed to link '$f' -> '$target'"
    fi
done < <(find "$content_path" -type f -print0 | sort -z)

if [[ "$DRY_RUN" -eq 1 ]]; then
    log "DRY RUN merge '${name:-$content_path}': would link ${linked}, ${present} already present -> ${dest_dir}"
else
    log "merged '${name:-$content_path}': linked ${linked}, ${present} already present -> ${dest_dir}"
fi
echo "merge ${name:-$content_path}: linked ${linked}, already present ${present} (dest: ${dest_dir})"
