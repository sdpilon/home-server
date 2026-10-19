#!/usr/bin/env bash
#
# qbit-hardlink-batch.sh
#
# Runs qbit-hardlink.sh over every existing torrent in one or more category
# download directories. Handy for retroactively linking a back-catalogue of
# torrents that finished before the qBittorrent "run on finished" hook was
# wired up.
#
# Torrents are read from <downloads-root>/<category>, i.e. the qBittorrent
# category save path is assumed to equal the category name. That matches the
# default "Save to category folder" layout (Session\UseCategoryPathsInManualMode
# with per-category save_path), where category "books" downloads into
# /data/torrents/books. Override the root with -r if your layout differs.
#
# The destination for each category is still resolved by qbit-hardlink.sh from
# the category map, so there is a single source of truth for where things go.
#
# ── Usage ────────────────────────────────────────────────────────────────
#   qbit-hardlink-batch.sh [options] [category ...]
#
#   -c CONF   category map (default: categories.conf next to this script)
#   -r ROOT   downloads root (default: /data/torrents)
#   -d        dry run (passed through to qbit-hardlink.sh)
#   -i        skip a torrent when ALL of its files are already hardlinked
#             somewhere under the category destination (checked by inode/dev,
#             so renames by other importers still count as "done")
#   -e NAME   exclude a torrent by its top-level name (repeatable)
#   -h        show help
#
# With no category arguments, every category listed in the map is processed.
#
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
HARDLINK="${SCRIPT_DIR}/qbit-hardlink.sh"
CONF_FILE="${SCRIPT_DIR}/categories.conf"
ROOT="/data/torrents"
DRY_RUN=0
SKIP_LINKED=0
declare -a EXCLUDES=()
declare -a CATEGORIES=()

usage() {
    awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "${BASH_SOURCE[0]}" >&2
    exit "${1:-2}"
}

while getopts ":c:r:e:dih" opt; do
    case "$opt" in
        c) CONF_FILE="$OPTARG" ;;
        r) ROOT="$OPTARG" ;;
        e) EXCLUDES+=("$OPTARG") ;;
        d) DRY_RUN=1 ;;
        i) SKIP_LINKED=1 ;;
        h) usage 0 ;;
        *) usage ;;
    esac
done
shift $((OPTIND - 1))
CATEGORIES=("$@")

[[ -x "$HARDLINK" ]] || { echo "ERROR: qbit-hardlink.sh not found/executable: $HARDLINK" >&2; exit 1; }
[[ -f "$CONF_FILE" ]] || { echo "ERROR: category map not found: $CONF_FILE" >&2; exit 1; }

# ── parse the category map ───────────────────────────────────────────────
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
[[ ${#CATEGORIES[@]} -gt 0 ]] || { echo "ERROR: no categories to process" >&2; exit 1; }

# ── helpers ──────────────────────────────────────────────────────────────
declare -A LINKED=()
build_index() {
    # Index every inode already present under $1 so "already linked" checks
    # are a single hash lookup rather than a find per source file.
    LINKED=()
    local f st
    while IFS= read -r -d '' f; do
        st="$(stat -c '%d:%i' -- "$f" 2>/dev/null)" || continue
        LINKED["$st"]=1
    done < <(find "$1" -type f -print0 2>/dev/null)
}

content_all_linked() {
    local f st
    while IFS= read -r -d '' f; do
        st="$(stat -c '%d:%i' -- "$f" 2>/dev/null)" || continue
        [[ -n "${LINKED[$st]:-}" ]] || return 1
    done < <(find "$1" -type f -print0 2>/dev/null)
    return 0
}

is_excluded() {
    [[ ${#EXCLUDES[@]} -eq 0 ]] && return 1
    local n="$1" e
    for e in "${EXCLUDES[@]}"; do
        [[ "$e" == "$n" ]] && return 0
    done
    return 1
}

# ── main loop ────────────────────────────────────────────────────────────
shopt -s nullglob
for cat in "${CATEGORIES[@]}"; do
    src="${ROOT%/}/${cat}"
    if [[ ! -d "$src" ]]; then
        echo "skip: no such download dir: $src" >&2
        continue
    fi

    if [[ "$SKIP_LINKED" -eq 1 ]]; then
        dest="${DEST_OF[$cat]:-}"
        if [[ -z "$dest" ]]; then
            echo "skip: category '$cat' has no mapping in $CONF_FILE (needed for -i)" >&2
            continue
        fi
        build_index "$dest"
    fi

    echo "== ${cat}: ${src} =="
    for item in "$src"/*; do
        base="$(basename -- "$item")"
        if is_excluded "$base"; then
            echo "  excluded:     $base"
            continue
        fi
        if [[ "$SKIP_LINKED" -eq 1 ]] && content_all_linked "$item"; then
            echo "  already linked: $base"
            continue
        fi

        args=(-n "$base" -l "$cat" -f "$item" -c "$CONF_FILE")
        if [[ "$DRY_RUN" -eq 1 ]]; then
            args=(-d "${args[@]}")
        fi
        "$HARDLINK" "${args[@]}"
    done
done
