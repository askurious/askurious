#!/usr/bin/env bash
# The uploads snapshots encrypted (backup.sh, after snapshot_uploads.py --staged; Ralf 2026-10-21: the snapshots held
# images, recordings and scans readable next to the encrypted dumps).
#   ./lock_uploads.sh <folder> <days> <key>     e.g. ~/backups/uploads 14 ~/askurious-env/backup.asc
# <key> is the public key of BACKUP_ENCRYPT_KEY: a GPG key, or a file of age recipients ("age1...").
# For every complete <stamp>.stage/ in <folder>: each new content goes to blobs/<sha256>.gpg (or .age) unless it is
# there already, the manifest to <stamp>.manifest.gpg, the list of its SHA-256 sums to <stamp>.hashes (no names, no
# contents: what the server needs to clean up), then the stage goes, and a former plain snapshot folder of the same
# time with it. Then snapshots older than <days> go (the newest always stays), and every blob no remaining snapshot
# lists. The server holds no key that could decrypt; restore_uploads.py brings a snapshot back where the private key
# is. A failure leaves the stage for the next run and ends with exit 1; nothing readable is removed before its
# encrypted form is complete.
set -euo pipefail
umask 077
folder=$1; days=$2; key=$3
[ -r "$key" ] || { echo "uploads: key $key is not readable" >&2; exit 1; }
if grep -q '^age1' "$key"; then tool=age; else tool=gpg; fi
command -v "$tool" > /dev/null || { echo "uploads: $tool is missing on the server (BETRIEB.md, Datensicherung)" >&2; exit 1; }
gpghome=$(mktemp -d)
trap 'rm -rf "$gpghome"' EXIT
mkdir -p "$folder/blobs" && chmod 700 "$folder" "$folder/blobs"

# encrypt <in> <out>: written to <out>.part first, so that a broken run never leaves a half file under its name
encrypt() {
  if [ "$tool" = age ]; then
    age -R "$key" -o "$2.part" "$1"
  else
    gpg --homedir "$gpghome" --batch --yes --quiet --trust-model always --recipient-file "$key" --output "$2.part" \
      --encrypt "$1"
  fi
  [ -s "$2.part" ] || { rm -f "$2.part"; return 1; }
  mv "$2.part" "$2"
}

failed=0; locked=0; blobs_new=0
for stage in "$folder"/[0-9]*.stage; do
  [ -d "$stage" ] || continue
  stamp=$(basename "$stage" .stage)
  [[ "$stamp" =~ ^[0-9]{8}-[0-9]{6}$ ]] || continue
  ok=1
  for f in "$stage"/new/*; do
    [ -f "$f" ] || continue
    sha=$(basename "$f")
    [[ "$sha" =~ ^[0-9a-f]{64}$ ]] || { ok=0; break; }
    [ -f "$folder/blobs/$sha.$tool" ] && continue
    if encrypt "$f" "$folder/blobs/$sha.$tool"; then blobs_new=$((blobs_new + 1)); else ok=0; break; fi
  done
  if [ "$ok" = 1 ] && encrypt "$stage/manifest.jsonl" "$folder/$stamp.manifest.$tool"; then
    grep -o '"sha256": "[0-9a-f]\{64\}"' "$stage/manifest.jsonl" | grep -o '[0-9a-f]\{64\}' | sort -u \
      > "$folder/$stamp.hashes.part"
    # every content the manifest names must be in the store before the stage goes
    missing=$(while read -r sha; do [ -f "$folder/blobs/$sha.$tool" ] || echo "$sha"; done < "$folder/$stamp.hashes.part")
    if [ -n "$missing" ]; then
      rm -f "$folder/$stamp.hashes.part" "$folder/$stamp.manifest.$tool"
      echo "uploads $stamp: contents missing in the store" >&2; failed=1; continue
    fi
    mv "$folder/$stamp.hashes.part" "$folder/$stamp.hashes"
    rm -rf "$stage"
    [ ! -d "$folder/$stamp" ] || rm -rf "$folder/$stamp"            # the former plain snapshot of this time
    locked=$((locked + 1))
  else
    echo "uploads $stamp: not encrypted, the stage stays for the next run" >&2; failed=1
  fi
done

# older than <days> go, the newest always stays (as snapshot_uploads.py's sweep)
newest=$(ls -1 "$folder" | grep -E '^[0-9]{8}-[0-9]{6}\.hashes$' | sort | tail -n 1 || true)
gone=0
now=$(date -u +%s)
for h in "$folder"/[0-9]*.hashes; do
  [ -f "$h" ] || continue
  name=$(basename "$h"); stamp=${name%.hashes}
  [ "$name" != "$newest" ] || continue
  t=$(date -u -d "${stamp:0:4}-${stamp:4:2}-${stamp:6:2} ${stamp:9:2}:${stamp:11:2}:${stamp:13:2}" +%s 2>/dev/null) || continue
  if [ $((now - t)) -gt $(( ${days%.*} * 86400 )) ]; then
    rm -f "$folder/$stamp".manifest.* "$h"; gone=$((gone + 1))
  fi
done
# blobs no snapshot lists any more (a file deleted in the app, once its last snapshot is older than <days>)
dropped=0
if [ -n "$newest" ]; then
  cat "$folder"/[0-9]*.hashes | sort -u > "$gpghome/keep"
  for b in "$folder"/blobs/*; do
    [ -f "$b" ] || continue
    sha=$(basename "$b"); sha=${sha%%.*}
    grep -qx "$sha" "$gpghome/keep" || { rm -f "$b"; dropped=$((dropped + 1)); }
  done
fi
echo "uploads encrypted: $locked snapshots, $blobs_new new contents, $gone snapshots and $dropped contents removed"
exit $failed
