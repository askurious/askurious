#!/usr/bin/env bash
# Dump of the database every four hours (crontab 02:30, 06:30, ... 22:30; also before every maintenance window), kept
# 14 days (BACKUP_DAYS, as the privacy page says) in ~/backups; then a snapshot of the uploads (volume "instance")
# in ~/backups/uploads, where only new or changed files take space (snapshot_uploads.py, hard links; Ralf 2026-10-12),
# also 14 days; then, only when configured, an encrypted copy off the server (offsite.sh; off until a target exists).
# A failed or unreadable dump ends with exit 1 and leaves no file behind, so that maintenance.sh stops before it
# updates the images (code audit 2026-10-08, B3), and says so in the operators' Slack channel (alarm.sh; audit
# 2026-10-09, BT6). A good one leaves its time in ~/askurious-status/backup-ok, which the status page checks.
#   ./backup.sh                  the regular run: askurious-<date>.dump (14 days, at most 84), uploads, off-site copy
#   ./backup.sh before-deploy    before migrations (restart.sh, BT3): askurious-before-deploy-<date>.dump, newest 5,
#                                and none older than 30 days (the privacy page says so; audit 2026-10-10, L-12)
# Every database of the server is dumped, each into files of its own name: today only "askurious"; a further brand
# with its own database (profile "brand", datenbank-je-marke-konzept.md) gets <name>-<date>.dump and its own mark
# backup-ok-<name>, its uploads (volume "instance-brand") go to ~/backups/uploads-brand. One that fails does not stop
# the others, but the run ends with exit 1. Dumps of a database that is gone go by their age (14 days regular, 30
# before a deploy).
# Encrypted on the server too, only when configured (audit 2026-10-18, CFG-03; off until BACKUP_ENCRYPT_KEY is in the
# .env): BACKUP_ENCRYPT_KEY=~/askurious-env/backup.asc (a public GPG key) or a file of age recipients ("age1..."); each
# regular dump becomes <name>.dump.gpg or .dump.age once pg_restore has read it, and the plain one goes. The server
# holds no key that could decrypt (the private one stays offline with the operators). The dumps before a deploy stay
# readable only while the deploy runs (Ralf 2026-10-21): restart.sh calls "./backup.sh lock-before-deploy" once it went
# through, and a regular run encrypts any left readable for more than a day (a deploy that stopped). The way back
# after a migration then needs the private key (BETRIEB.md).
#   ./backup.sh lock-before-deploy   only that: every readable dump before a deploy encrypted, nothing else
# With the same key the uploads snapshots are encrypted too (Ralf 2026-10-21): snapshot_uploads.py --staged under the
# backup window, then lock_uploads.sh after it, which also converts the former readable snapshot folders.
set -euo pipefail
umask 077                                   # dumps hold every answer: readable by the server user only
cd "$(dirname "$0")"
. ./compose-env.sh
gate_release_files=(); gate_pids=(); control_dir=""
release_gates() {
  local bad=0 file pid
  for file in "${gate_release_files[@]}"; do touch "$file" || bad=1; done
  for pid in "${gate_pids[@]}"; do wait "$pid" || bad=1; done
  gate_release_files=(); gate_pids=()
  return "$bad"
}
# The list of what lies in ~/backups for Administration > Server, block "Sicherungen" (app/backups.py; Ralf
# 2026-10-18, option A): names, sizes and times only, into ~/askurious-status/backups.json, which the researchers'
# container already reads; no container ever sees a dump. Written after every run, a failed one too.
write_list() {
  local status=$HOME/askurious-status f name database kind snaps newest first=1 offsite_at
  mkdir -p "$status" || return 1
  {
    # offsite_at: the last copy off the server that went through (offsite.sh's mark), empty without one (CFG-03)
    offsite_at=""
    [ ! -f "$status/offsite-ok" ] || offsite_at=$(date -u -r "$status/offsite-ok" +%Y-%m-%dT%H:%M:%SZ)
    printf '{"time": "%s", "run": "%s", "ok": %s, "offsite": %s, "offsite_at": "%s", "encrypt": %s, "dumps": [' \
      "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$2" "$([ "$1" = 0 ] && echo true || echo false)" \
      "$([ -n "$(offsite_target)" ] && echo true || echo false)" "$offsite_at" \
      "$([ -n "$(encrypt_key)" ] && echo true || echo false)"
    for f in "$HOME"/backups/*.dump "$HOME"/backups/*.dump.gpg "$HOME"/backups/*.dump.age; do
      [ -f "$f" ] || continue
      name=$(basename "$f")
      [[ "$name" =~ ^[A-Za-z0-9_.-]+$ ]] || continue
      database=""; kind=other
      if [[ "$name" =~ ^([A-Za-z0-9_]+)(-before-deploy)?-[0-9]{8}-[0-9]{4,6}\.dump(\.gpg|\.age)?$ ]]; then
        database=${BASH_REMATCH[1]}; kind=regular; [ -z "${BASH_REMATCH[2]}" ] || kind=before-deploy
      fi
      [ -n "$first" ] || printf ','; first=""
      printf '\n {"name": "%s", "database": "%s", "kind": "%s", "size": %s, "at": "%s", "encrypted": %s}' "$name" \
        "$database" "$kind" "$(stat -c %s "$f")" "$(date -u -r "$f" +%Y-%m-%dT%H:%M:%SZ)" \
        "$(case "$name" in *.gpg|*.age) echo true ;; *) echo false ;; esac)"
    done
    printf '],\n "uploads": ['; first=1
    for f in "$HOME"/backups/uploads "$HOME"/backups/uploads-brand; do
      [ -d "$f" ] || continue
      # a glob, not -regex: the default (emacs) regex of GNU find 4.10 has no \{n\}, so it found none ("0 Stände",
      # Ralf 2026-10-18)
      # plain snapshot folders, and encrypted snapshots by their list of sums (lock_uploads.sh)
      snaps=$( { find "$f" -mindepth 1 -maxdepth 1 -type d -name '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9][0-9][0-9]';
                 find "$f" -mindepth 1 -maxdepth 1 -type f -name '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9][0-9][0-9].hashes' \
                   | sed 's/\.hashes$//'; } | sort -u)
      newest=$(basename "$(printf '%s\n' "$snaps" | tail -n 1)")
      [ -n "$first" ] || printf ','; first=""
      printf '\n {"name": "%s", "snapshots": %s, "newest": "%s", "size": %s, "encrypted": %s}' "$(basename "$f")" \
        "$(printf '%s' "$snaps" | grep -c . || true)" "$newest" "$(du -sb "$f" | cut -f1)" \
        "$( [ -n "$(find "$f" -maxdepth 1 -type d -name '[0-9]*-[0-9]*' ! -name '*.stage' -print -quit)" ] && echo false || \
            { [ -n "$(find "$f" -maxdepth 1 -name '*.hashes' -print -quit)" ] && echo true || echo false; } )"
    done
    printf ']}\n'
  } > "$status/backups.json.part" && chmod 644 "$status/backups.json.part" && mv "$status/backups.json.part" "$status/backups.json"
}
offsite_target() {  # as offsite.sh reads it: the environment first, then the last line of .env
  env_value OFFSITE_TARGET
}
env_value() {  # the environment first, then the last line of .env (as offsite.sh and restart.sh)
  local v="${!1:-}"
  [ -n "$v" ] || v=$(sed -n "s/^$1=//p" .env 2>/dev/null | tail -n 1 | tr -d '\r')
  printf '%s' "$v"
}
encrypt_key() {  # BACKUP_ENCRYPT_KEY with ~ expanded; empty: the dumps stay as they are (as before)
  local v
  v=$(env_value BACKUP_ENCRYPT_KEY)
  printf '%s' "${v/#\~/$HOME}"
}
# lock_dump <file>: <file>.age (age, when the key file names age recipients) or <file>.gpg (GPG), the plain file
# removed; on any failure the plain dump stays, so that a backup is never lost to a missing tool (exit 1 says so)
lock_dump() {
  local key tool
  key=$(encrypt_key)
  [ -r "$key" ] || { echo "BACKUP_ENCRYPT_KEY ($key) is not a readable public key" >&2; return 1; }
  if grep -q '^age1' "$key"; then tool=age; else tool=gpg; fi
  command -v "$tool" > /dev/null || { echo "BACKUP_ENCRYPT_KEY needs $tool on the server (BETRIEB.md, Datensicherung)" >&2; return 1; }
  if [ "$tool" = age ]; then
    age -R "$key" -o "$1.age.part" "$1" || { rm -f "$1.age.part"; return 1; }
    mv "$1.age.part" "$1.age"
  else
    local home
    home=$(mktemp -d)
    gpg --homedir "$home" --batch --yes --quiet --trust-model always --recipient-file "$key" --output "$1.gpg.part" \
      --encrypt "$1" || { rm -rf "$home"; rm -f "$1.gpg.part"; return 1; }
    rm -rf "$home"
    mv "$1.gpg.part" "$1.gpg"
  fi
  [ -s "$1.$tool" ] || { rm -f "$1.$tool"; return 1; }
  rm -f "$1"
  printf '%s' "$1.$tool"
}
finish() {
  local rc=$?
  release_gates || rc=1
  write_list "$rc" "$([ "${kind:-}" = -before-deploy ] && echo before-deploy || echo regular)" || echo "backups.json not written" >&2
  if [ -n "$control_dir" ]; then
    resolved=$(realpath -- "$control_dir")
    case "$resolved" in "$(realpath /tmp)"/storage-backup.????????) rm -rf -- "$resolved" ;; *) rc=1 ;; esac
  fi
  [ "$rc" = 0 ] || ./alarm.sh "backup.sh ${1:-} failed (exit $rc): see ~/askurious-backup.log" || true
  exit "$rc"
}
trap finish EXIT
case "${1:-}" in
  "") kind=""; keep=84; max_days=14 ;;       # every four hours: 6 a day for 14 days
  before-deploy) kind=-before-deploy; keep=5; max_days=30 ;;
  lock-before-deploy) kind=-before-deploy ;;
  *) echo "usage: $0 [before-deploy|lock-before-deploy]" >&2; exit 2 ;;
esac
# lock_before_deploy [<minutes>]: every readable dump before a deploy encrypted (older than <minutes> only, when given)
lock_before_deploy() {
  local f bad=0 age=()
  [ -n "$(encrypt_key)" ] || return 0
  [ -z "${1:-}" ] || age=(-mmin +"$1")
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    if locked=$(lock_dump "$f"); then echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $(basename "$locked") encrypted"
    else echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $(basename "$f") not encrypted" >&2; bad=1; fi
  done < <(find "$HOME/backups" -maxdepth 1 -name '*-before-deploy-[0-9]*.dump' ${age[@]+"${age[@]}"} 2>/dev/null | sort)
  return "$bad"
}
if [ "${1:-}" = lock-before-deploy ]; then
  lock_before_deploy && exit 0
  exit 1
fi
mkdir -p "$HOME/backups"
# also for what older runs left readable for the group (before umask 077; audit 2026-10-09, BT11)
chmod 700 "$HOME/backups"
chmod 600 "$HOME"/backups/*.dump "$HOME"/backups/*.dump.gpg "$HOME"/backups/*.dump.age 2>/dev/null || true

running=$(docker compose ps --services --status running 2>/dev/null || true)
if [ -z "$kind" ]; then
  # During a rolling rollout, older containers do not yet participate in the gate: never claim a consistent set.
  for service in researcher participant-a participant-b housekeeping compute brand-researcher brand-participant brand-housekeeping brand-compute; do
    case $'\n'"$running"$'\n' in *$'\n'"$service"$'\n'*) ;; *) continue ;; esac
    if ! docker compose exec -T "$service" test -f /app/app/storage_guard.py < /dev/null; then
      echo "Storage backup gate is not installed in running service: $service" >&2
      exit 1
    fi
  done
  # Hold the volume gates across the database dumps and their matching file snapshots.
  for service in researcher brand-researcher; do
    [ "$service" = researcher ] || case $'\n'"$running"$'\n' in *$'\n'"$service"$'\n'*) ;; *) continue ;; esac
    [ -n "$control_dir" ] || control_dir=$(mktemp -d /tmp/storage-backup.XXXXXXXX)
    signal=release-$service
    ready_file=$control_dir/ready-$service
    docker compose run --rm --no-deps -T -v "$control_dir:/backup-control" "$service" \
      python /app/tools/storage_backup_lock.py /app/instance --release-file "/backup-control/$signal" \
      < /dev/null > "$ready_file" 2> "$control_dir/error-$service" &
    gate_pid=$!
    gate_pids+=("$gate_pid")
    gate_release_files+=("$control_dir/$signal")
    deadline=$((SECONDS + 130))
    until [ "$(head -n 1 "$ready_file" 2>/dev/null || true)" = READY ]; do
      if ! kill -0 "$gate_pid" 2>/dev/null || [ "$SECONDS" -ge "$deadline" ]; then
        echo "Could not acquire consistent storage backup window: $service" >&2
        exit 1
      fi
      sleep 0.1
    done
  done
fi

# the databases: askurious first, then any other (never the templates and "postgres"); only names of letters, digits
# and _ (they become file names). No answer from the server: askurious, as before.
databases=$(docker compose exec -T db psql -U askurious -d askurious -AtX -c \
  "SELECT datname FROM pg_database WHERE NOT datistemplate AND datname <> 'postgres' ORDER BY datname <> 'askurious', datname" \
  2>/dev/null | tr -d '\r' | grep -E '^[A-Za-z0-9_]+$' || true)
listed=1
[ -n "$databases" ] || { databases=askurious; listed=""; }

failed=0
for database in $databases; do
  prefix=$database$kind
  out="$HOME/backups/$prefix-$(date +%Y%m%d-%H%M%S).dump"
  if ! docker compose exec -T db pg_dump -U askurious -Fc "$database" > "$out.part"; then
    rm -f "$out.part"; echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) dump failed: $database" >&2; failed=1; continue
  fi
  # a dump PostgreSQL can read: pg_restore lists its table of contents
  if ! docker compose exec -T db pg_restore -l < "$out.part" > /dev/null; then
    rm -f "$out.part"; echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) dump unreadable (pg_restore -l): $database" >&2; failed=1; continue
  fi
  mv "$out.part" "$out"
  # the regular dumps encrypted, when configured (CFG-03); a failure keeps the plain dump and fails the run
  if [ -z "$kind" ] && [ -n "$(encrypt_key)" ]; then
    if locked=$(lock_dump "$out"); then out=$locked; else
      echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) dump not encrypted: $database" >&2; failed=1
    fi
  fi
  # the newest dumps of this kind stay (plain or encrypted)
  { ls -1t "$HOME"/backups/"$prefix"-[0-9]*.dump "$HOME"/backups/"$prefix"-[0-9]*.dump.gpg \
      "$HOME"/backups/"$prefix"-[0-9]*.dump.age 2>/dev/null || true; } | tail -n +$((keep + 1)) | xargs -r rm -f
  # and none older than max_days: with rare deploys the newest 5 could be of any age (L-12); the newest always stays
  [ -z "$max_days" ] || find "$HOME/backups" -maxdepth 1 \( -name "$prefix-[0-9]*.dump" -o -name "$prefix-[0-9]*.dump.gpg" \
    -o -name "$prefix-[0-9]*.dump.age" \) ! -samefile "$out" -mtime +"$max_days" -delete
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $(basename "$out") $(wc -c < "$out") bytes"
done
# dumps of a database that is gone (a brand switched back and its database deleted, BETRIEB.md) are in no round of
# the loop above: by age alone, as the privacy page says, 14 days for the regular ones (BACKUP_DAYS), 30 for those
# before a deploy (audit 2026-10-10, B3). Only files this script writes; any other dump stays.
for f in "$HOME"/backups/*.dump "$HOME"/backups/*.dump.gpg "$HOME"/backups/*.dump.age; do
  [ -n "$listed" ] && [ -f "$f" ] || continue     # without the server's list nothing counts as gone
  name=$(basename "$f")
  [[ "$name" =~ ^([A-Za-z0-9_]+)(-before-deploy)?-[0-9]{8}-[0-9]{4,6}\.dump(\.gpg|\.age)?$ ]] || continue
  case " $(echo $databases) " in *" ${BASH_REMATCH[1]} "*) continue ;; esac
  days=14; [ -z "${BASH_REMATCH[2]}" ] || days=30
  if [ -n "$(find "$f" -maxdepth 0 -mtime +"$days")" ]; then
    rm -f "$f"; echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $name removed: its database is gone, older than $days days"
  fi
done
# leftovers of a broken run
rm -f "$HOME"/backups/*.dump.part "$HOME"/backups/*.dump.gpg.part "$HOME"/backups/*.dump.age.part
# a dump before a deploy that stopped, readable for more than a day: encrypted now (the regular run only)
[ -n "$kind" ] || lock_before_deploy 1440 || failed=1
[ -z "${1:-}" ] || exit $failed

# the uploads: a snapshot in an app container (it reads the volume as the app does, uid 1000 like the server user),
# only changed files copied; the same 14 days. A brand with its own containers: its volume too. With
# BACKUP_ENCRYPT_KEY only staged (snapshot_uploads.py --staged): lock_uploads.sh encrypts it after the window.
running=$(docker compose ps --services --status running 2>/dev/null | tr -d '\r' || true)
staged=(); [ -z "$(encrypt_key)" ] || staged=(--staged)
upload_pairs=()
for pair in researcher:uploads brand-researcher:uploads-brand; do
  service=${pair%%:*}; folder=$HOME/backups/${pair#*:}
  [ "$service" = researcher ] || case $'\n'"$running"$'\n' in *$'\n'"$service"$'\n'*) ;; *) continue ;; esac
  upload_pairs+=("$pair")
  mkdir -p "$folder" && chmod 700 "$folder"
  if ! docker compose run --rm --no-deps -T -v "$folder:/snap" "$service" python - /app/instance /snap 14 ${staged[@]+"${staged[@]}"} < snapshot_uploads.py; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) uploads snapshot failed: $service" >&2; failed=1
  fi
done
release_gates || failed=1

# encrypted: the staged snapshot, and every former readable snapshot folder converted (once; its time kept), outside
# the backup window, so that the app never waits for gpg
if [ -n "$(encrypt_key)" ]; then
  for pair in ${upload_pairs[@]+"${upload_pairs[@]}"}; do
    service=${pair%%:*}; folder=$HOME/backups/${pair#*:}
    for plain in "$folder"/[0-9]*; do
      name=$(basename "$plain")
      [[ "$name" =~ ^[0-9]{8}-[0-9]{6}$ ]] && [ -d "$plain" ] && [ ! -d "$folder/$name.stage" ] || continue
      if ! docker compose run --rm --no-deps -T -v "$folder:/snap" "$service" python - "/snap/$name" /snap 14 --staged \
          --stamp "$name" < snapshot_uploads.py; then
        echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) uploads $name not converted: $service" >&2; failed=1
      fi
    done
    if ! ./lock_uploads.sh "$folder" 14 "$(encrypt_key)"; then
      echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) uploads not encrypted: $service" >&2; failed=1
    fi
  done
fi

# off the server, encrypted: only when OFFSITE_TARGET is in the .env (offsite.sh; prepared, off for now)
if [ -x ./offsite.sh ] && ! ./offsite.sh; then
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) off-site copy failed" >&2; failed=1
fi
if [ "$failed" = 0 ]; then
  # A database dump alone is no complete backup: publish success only after files and the configured offsite copy.
  mkdir -p "$HOME/askurious-status" && chmod 755 "$HOME/askurious-status"
  for database in $databases; do
    mark=backup-ok; [ "$database" = askurious ] || mark=backup-ok-$database
    (umask 022; date -u +%Y-%m-%dT%H:%M:%SZ > "$HOME/askurious-status/$mark")
  done
fi
exit $failed
