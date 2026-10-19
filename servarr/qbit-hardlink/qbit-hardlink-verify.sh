#!/usr/bin/env bash
#
# qbit-hardlink-verify.sh
#
# Read-only check. For every top-level torrent in each category download
# directory it reports whether all of the torrent's files are hardlinked
# somewhere under the category destination. "Hardlinked somewhere" is checked
# by device+inode, so a file already imported (and possibly renamed) by
# Readarr/Sonarr/etc. still counts as done.
#
# Torrents are read from <downloads-root>/<category>, mirroring
# qbit-hardlink-batch.sh. Destinations come from the category map.
#
# ── Usage ────────────────────────────────────────────────────────────────
#   qbit-hardlink-verify.sh [-c CONF] [-r ROOT] [-v] [category ...]
#
#   -c CONF   category map (default: categories.conf next to this script)
#   -r ROOT   downloads root (default: /data/torrents)
#   -v        also list every torrent that is fully linked
#   -h        show help
#
# Exit status is non-zero if any torrent has files that are not linked.
# With no category arguments, every category listed in the map is checked.
#
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
CONF_FILE="${SCRIPT_DIR}/categories.conf"
ROOT="/data/torrents"
VERBOSE=0
declare -a CATEGORIES=()

usage() {
    awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "${BASH_SOURCE[0]}" >&2
    exit "${1:-2}"
}

while getopts ":c:r:vh" opt; do
    case "$opt" in
        c) CONF_FILE="$OPTARG" ;;
        r) ROOT="$OPTARG" ;;
        v) VERBOSE=1 ;;
        h) usage 0 ;;
        *) usage ;;
    esac
done
shift $((OPTIND - 1))
CATEGORIES=("$@")

[[ -f "$CONF_FILE" ]] || { echo "ERROR: category map not found: $CONF_FILE" >&2; exit 1; }

declare -A DEST_OF=()
declare -a CONF_CATS=()
while IFS='=' read -r conf_cat conf_dest; do
    [[ -z "$conf_cat" || "$conf_cat" == \#* ]] && continue
    conf_cat="$(echo -n "$conf_cat" | xargs)"
    conf_dest="$(echo -n "$conf_dest" | xargs)"
    [[ -z "$conf_cat" ]] && continue
    CONF_CATS+=("$conf_cat")
    DEST_OF["$conf_cat"]="$conf_dest"
done < "$CONF_FILE"

if [[ ${#CATEGORIES[@]} -eq 0 ]]; then
    CATEGORIES=("${CONF_CATS[@]}")
fi
[[ ${#CATEGORIES[@]} -gt 0 ]] || { echo "ERROR: no categories to check" >&2; exit 1; }

declare -A LINKED=()
build_index() {
    LINKED=()
    local f st
    while IFS= read -r -d '' f; do
        st="$(stat -c '%d:%i' -- "$f" 2>/dev/null)" || continue
        LINKED["$st"]=1
    done < <(find "$1" -type f -print0 2>/dev/null)
}

result=0
shopt -s nullglob
for cat in "${CATEGORIES[@]}"; do
    src="${ROOT%/}/${cat}"
    dest="${DEST_OF[$cat]:-}"
    echo "== ${cat}: ${src} -> ${dest:-<no mapping>} =="
    if [[ ! -d "$src" ]]; then
        echo "  skip: no such download dir" >&2
        continue
    fi
    [[ -n "$dest" ]] || { echo "  skip: no mapping in $CONF_FILE" >&2; result=1; continue; }

    build_index "$dest"
    for item in "$src"/*; do
        base="$(basename -- "$item")"
        total=0
        missing=()
        while IFS= read -r -d '' f; do
            total=$((total + 1))
            st="$(stat -c '%d:%i' -- "$f" 2>/dev/null)" || continue
            [[ -n "${LINKED[$st]:-}" ]] || missing+=("${f#"${item%/}"/}")
        done < <(find "$item" -type f -print0 2>/dev/null)

        if [[ ${#missing[@]} -eq 0 ]]; then
            [[ "$VERBOSE" -eq 1 ]] && echo "  OK  (${total} file(s)): $base"
        else
            result=1
            echo "  NOT LINKED: $base  (${#missing[@]}/${total} missing)"
            for m in "${missing[@]}"; do
                echo "      $m"
            done
        fi
    done
done

if [[ "$result" -eq 0 ]]; then
    echo "ALL TORRENT CONTENT LINKED"
else
    echo "SOME CONTENT NOT LINKED" >&2
fi
exit "$result"
