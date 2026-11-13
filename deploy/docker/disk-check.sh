#!/usr/bin/env bash
# A message to Slack (alarm.sh) when a file system of the server runs short of space: the one with Docker's data
# (images, the database's volume, the uploads), the one with the dumps (~/backups) and the root. A full disk stops
# PostgreSQL and the dumps at the same time. Runs every ten minutes from the crontab (deploy-docker.sh); it only
# reports, it never deletes anything.
#   DISK_MIN_FREE_PERCENT  alarm below this share free (default 20), from the environment or the .env
#   DISK_ALARM_HOURS       while still short, the alarm repeats after this many hours (default 24)
# The state (when the last alarm went out) lives in ~/askurious-status/disk-alarm; once there is enough space again,
# one message says so and the state goes.
set -uo pipefail
cd "$(dirname "$0")"
from_env() { [ -f .env ] && sed -n "s/^$1=//p" .env | tail -n 1 | tr -d '\r"'"'" || true; }
min=${DISK_MIN_FREE_PERCENT:-$(from_env DISK_MIN_FREE_PERCENT)}
min=${min:-20}
hours=${DISK_ALARM_HOURS:-$(from_env DISK_ALARM_HOURS)}
hours=${hours:-24}
case "$min$hours" in *[!0-9]*|"") echo "disk-check.sh: DISK_MIN_FREE_PERCENT and DISK_ALARM_HOURS must be whole numbers" >&2; exit 2 ;; esac
state_dir="$HOME/askurious-status"
state="$state_dir/disk-alarm"
mkdir -p "$state_dir"

paths=(/ "$HOME/backups")
root=$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || true)
[ -n "$root" ] && paths+=("$root")
short=() seen=" "
for p in "${paths[@]}"; do
  [ -e "$p" ] || continue
  # POSIX output: file system, blocks, used, available, capacity, mount point
  line=$(df -P -k "$p" 2>/dev/null | tail -n 1) || continue
  read -r _fs size _used avail _cap mount <<< "$line"
  case "$size$avail" in *[!0-9]*|"") continue ;; esac
  [ "$size" -gt 0 ] || continue
  case "$seen" in *" $mount "*) continue ;; esac   # several paths on one file system: once
  seen="$seen$mount "
  free=$(( avail * 100 / size ))
  if [ "$free" -lt "$min" ]; then
    short+=("$mount: $free % free ($(( avail / 1024 / 1024 )) GB of $(( size / 1024 / 1024 )) GB)")
  fi
done

now=$(date +%s)
if [ "${#short[@]}" -gt 0 ]; then
  last=$(cat "$state" 2>/dev/null || echo 0)
  case "$last" in *[!0-9]*|"") last=0 ;; esac
  if [ $(( now - last )) -ge $(( hours * 3600 )) ]; then
    list=$(printf '%s; ' "${short[@]}")
    msg="Disk space short (below $min % free): ${list%; }. Nothing was deleted; see docker system df and du -sh ~/backups."
    bash ./alarm.sh "$msg"
    echo "$now" > "$state"
  fi
  exit 1
fi
if [ -f "$state" ]; then
  bash ./alarm.sh "Disk space fine again: every file system has at least $min % free."
  rm -f "$state"
fi
exit 0
