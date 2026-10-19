I saved the three operations as reusable, documented, executable scripts in `/app/data/projects/servarr/qbit-hardlink/`:

## New scripts

`qbit-hardlink-batch.sh` — bulk-links a back-catalogue by running `qbit-hardlink.sh` over every torrent in a category's download dir.

- Derives the category from the dir name (root defaults to `/data/torrents`, override `-r`).
- `-d` dry run, passes through to the main script.
- `-i` skips torrents whose files are **already hardlinked anywhere under the destination** (device+inode check) — this is the "skip if already done" rule you picked, and it correctly treats Readarr's renamed imports as done.
- `-e NAME` excludes a torrent by name (repeatable); `-c` for the map.

`qbit-hardlink-merge.sh` — completes a _partially_ linked folder. The stock script skips a torrent when its destination path exists, so a folder missing some files never gets repaired; this walks the content path and hardlinks only the missing files (creating subdirs as needed). This is what filled in the partially linked audiobook folder.

- `-l CATEGORY` derives the dest from the map, or `-t DEST_DIR` for an explicit target; `-d` dry run.

`qbit-hardlink-verify.sh` — read-only audit. Reports `OK` / `NOT LINKED` (with the missing relative paths) per torrent, by inode, and exits non-zero if anything is unlinked. `-v` lists fully-linked torrents too.

## Verification

- All three pass `bash -n` and their `-h` output (correct exit codes).
- `merge` was tested end-to-end in a temp dir: it detected the pre-linked file, linked only the missing nested file, and produced the same inode (link count 2).
- Running the new `verify` against the real setup ends with **ALL TORRENT CONTENT LINKED**; `batch -d -i` reports every torrent as already linked.

So the equivalent of what I ran earlier is now reproducible with:
```sh
./qbit-hardlink-batch.sh -d -i          # preview
./qbit-hardlink-batch.sh -i             # link new items, skip done ones
./qbit-hardlink-merge.sh -l audiobooks -f '/data/torrents/audiobooks/<name>'   # repair a partial folder
./qbit-hardlink-verify.sh               # audit
```

One caveat baked into the docs: `batch/verify` assume each category's download dir is `<root>/<category>` (true here); use `-r` if that changes.