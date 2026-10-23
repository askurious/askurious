#!/usr/bin/env bash
# The copy off the server, encrypted (called by backup.sh after a regular run; Ralf 2026-10-12: prepared, off until
# the system for it exists). Off as long as OFFSITE_TARGET is not in the .env: then it does nothing and ends with 0.
#   OFFSITE_TARGET=user@host:folder     where rsync over SSH copies to (the server user's SSH key, BatchMode)
#   OFFSITE_KEY=~/askurious-env/offsite.asc   the public GPG key of the recipient, or a file of age recipients
#                                       ("age1...", then age encrypts; audit 2026-10-18, CFG-03); the server holds no
#                                       key that could decrypt, the private key stays offline with the operators
# A dump backup.sh has already encrypted (BACKUP_ENCRYPT_KEY) goes as it is: take the same key for both.
# A copy that went through leaves its time in ~/askurious-status/offsite-ok (Administration > Server shows it).
# Each run: the newest regular dump of every database, encrypted (<name>-<date>.dump.gpg); once a day the newest
# snapshot of the uploads as an encrypted tar (uploads-<date>.tar.gpg). Nothing is deleted at the target: it must
# delete after 14 days itself, as the privacy page says (BETRIEB.md, "Datensicherung").
set -euo pipefail
umask 077
cd "$(dirname "$0")"
env_value() {  # the environment first, then the last line of .env (as restart.sh)
  local v="${!1:-}"
  [ -n "$v" ] || v=$(sed -n "s/^$1=//p" .env 2>/dev/null | tail -n 1 | tr -d '\r')
  printf '%s' "$v"
}
target=$(env_value OFFSITE_TARGET)
[ -n "$target" ] || exit 0
key=$(env_value OFFSITE_KEY); key=${key/#\~/$HOME}
[ -n "$key" ] && [ -r "$key" ] || { echo "OFFSITE_TARGET is set, but OFFSITE_KEY ($key) is not a readable public key" >&2; exit 1; }
if grep -q '^age1' "$key"; then tool=age; else tool=gpg; fi
command -v "$tool" > /dev/null || { echo "OFFSITE_KEY needs $tool on the server (BETRIEB.md, Datensicherung)" >&2; exit 1; }

stage="$HOME/backups/offsite"
rm -rf "$stage" && mkdir -p "$stage"
gpghome=$(mktemp -d)
trap 'rm -rf "$gpghome" "$stage"' EXIT
# encrypt <in> <out without suffix>: <out>.gpg or <out>.age; <in> "-" reads standard input
encrypt() {
  if [ "$tool" = age ]; then
    if [ "$1" = - ]; then age -R "$key" -o "$2.age"; else age -R "$key" -o "$2.age" "$1"; fi
  elif [ "$1" = - ]; then
    gpg --homedir "$gpghome" --batch --yes --quiet --trust-model always --recipient-file "$key" --output "$2.gpg" --encrypt
  else
    gpg --homedir "$gpghome" --batch --yes --quiet --trust-model always --recipient-file "$key" --output "$2.gpg" --encrypt "$1"
  fi
}

# the newest regular dump of each database (not the ones before a deploy); one encrypted by backup.sh as it is
for dump in $(ls -1t "$HOME"/backups/*.dump "$HOME"/backups/*.dump.gpg "$HOME"/backups/*.dump.age 2>/dev/null \
              | grep -v -- '-before-deploy-' || true); do
  file=$(basename "$dump"); name=${file%%.dump*}; db=${name%-*-*}
  [ -e "$stage/.$db" ] && continue
  touch "$stage/.$db"
  case "$file" in
    *.dump) encrypt "$dump" "$stage/$name.dump" ;;
    *) cp -p "$dump" "$stage/$file" ;;
  esac
done
rm -f "$stage"/.[A-Za-z0-9_]*

# the uploads once a day: the newest snapshot as a tar (the target cannot see hard links, so whole). An encrypted
# snapshot (lock_uploads.sh) goes as it is: its manifest and the encrypted contents it lists, in a plain tar.
today=$(date -u +%Y%m%d)
mark="$HOME/backups/offsite-uploads-$today"
if [ ! -e "$mark" ]; then
  for folder in "$HOME"/backups/uploads "$HOME"/backups/uploads-brand; do
    [ -d "$folder" ] || continue
    locked=$(ls -1 "$folder" | grep -E '^[0-9]{8}-[0-9]{6}\.hashes$' | tail -n 1 || true)
    if [ -n "$locked" ]; then
      snap=${locked%.hashes}
      manifest=$(cd "$folder" && ls -1 "$snap".manifest.* | head -n 1)
      suffix=${manifest##*.}
      (cd "$folder" && { echo "$manifest"; sed "s#^#blobs/#; s#\$#.$suffix#" "$locked"; } \
        | tar -cf "$stage/$(basename "$folder")-$snap.tar" -T -)
      continue
    fi
    snap=$(ls -1 "$folder" | grep -E '^[0-9]{8}-[0-9]{6}$' | tail -n 1 || true)
    [ -n "$snap" ] || continue
    tar -C "$folder/$snap" -cf - . | encrypt - "$stage/$(basename "$folder")-$snap.tar"
  done
fi

rsync -a -e "ssh -o BatchMode=yes" "$stage/" "$target/"
[ -e "$mark" ] || { rm -f "$HOME"/backups/offsite-uploads-*; touch "$mark"; }
# the time of the last copy that went through, for backups.json and Administration > Server (CFG-03)
mkdir -p "$HOME/askurious-status" && (umask 022; date -u +%Y-%m-%dT%H:%M:%SZ > "$HOME/askurious-status/offsite-ok")
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) off-site: $(ls "$stage" | tr '\n' ' ')"
