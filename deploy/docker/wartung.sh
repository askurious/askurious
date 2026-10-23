#!/usr/bin/env bash
# The maintenance page's switch (Ralf, 2026-10-22; nginx/locations/wartung.conf). A file <side>.an in
# ~/askurious-status/wartung, which nginx reads (compose.yaml, read-only), makes every request of that side 503 with
# the page; the status page shows it for information (STATUS_INFO). The file holds its end time: it never stays on.
#   ./wartung.sh an forschende|teilnahme|alle [minutes] [reason]   on, 30 minutes by default, at most 240
#   ./wartung.sh aus forschende|teilnahme|alle [reason]            off
#   ./wartung.sh status                                            what is on, and until when
#   ./wartung.sh pruefen                                           off what has run out (crontab, every minute)
# Who switches it on, switches it off: restart.sh around the restart of the researchers' container (a trap removes
# it if the deploy stops), maintenance.sh's restart.sh around a new database image. Beyond that: the end time
# (pruefen, with a message in Slack, since then a script died), after-boot.sh (a reboot during a window), and by hand
# deploy-docker.sh wartung aus. Switching by hand says so in Slack; WARTUNG_STILL=1 (restart.sh) does not.
# Without the folder mounted (an nginx from before) the switch does nothing: the side stays open.
set -euo pipefail
cd "$(dirname "$0")"
# readable for nginx's workers and the status page (other users in their containers): 755 and 644
umask 022
DIR=${WARTUNG_DIR:-$HOME/askurious-status/wartung}
MAX=240

usage() { echo "usage: $0 an forschende|teilnahme|alle [minutes] [reason] | aus forschende|teilnahme|alle | status | pruefen" >&2; exit 2; }
sides() { case "${1:-}" in forschende|teilnahme) echo "$1" ;; alle) echo "forschende teilnahme" ;; *) usage ;; esac; }
clean() { printf '%s' "$*" | tr -cd 'A-Za-z0-9 ._,:()-' | cut -c1-100; }
tell() { [ "${WARTUNG_STILL:-}" = 1 ] || ./alarm.sh "$1" || true; }
until_text() { date -u -d "@$1" +%H:%M 2>/dev/null || echo "$1"; }

case "${1:-}" in
  an)
    targets=$(sides "${2:-}")
    minutes=${3:-30}
    [[ "$minutes" =~ ^[0-9]+$ ]] && [ "$minutes" -ge 1 ] && [ "$minutes" -le "$MAX" ] \
      || { echo "minutes: 1 to $MAX" >&2; exit 2; }
    reason=$(clean "${4:-}")
    end=$(( $(date +%s) + minutes * 60 ))
    mkdir -p "$DIR"; chmod 755 "$DIR"
    for side in $targets; do
      # written whole, then renamed: nginx never sees half a file
      printf '%s %s\n' "$end" "$reason" > "$DIR/.$side.tmp"
      mv -f "$DIR/.$side.tmp" "$DIR/$side.an"
      echo "maintenance page on: $side until $(until_text "$end") UTC${reason:+ ($reason)}"
    done
    tell "Maintenance page on ($2) until $(until_text "$end") UTC${reason:+: $reason}. Off: deploy-docker.sh wartung aus"
    ;;
  aus)
    targets=$(sides "${2:-}")
    for side in $targets; do
      if [ -e "$DIR/$side.an" ]; then rm -f "$DIR/$side.an"; echo "maintenance page off: $side"; fi
    done
    reason=$(clean "${3:-}")
    tell "Maintenance page off ($2)${reason:+: $reason}"
    ;;
  status)
    found=""
    for f in "$DIR"/*.an; do
      [ -e "$f" ] || continue
      found=1
      read -r end reason < "$f" || true
      echo "$(basename "$f" .an): on until $(until_text "${end:-0}") UTC${reason:+ ($reason)}"
    done
    [ -n "$found" ] || echo "maintenance page off"
    ;;
  pruefen)
    now=$(date +%s)
    for f in "$DIR"/*.an; do
      [ -e "$f" ] || continue
      end=""; reason=""
      read -r end reason < "$f" || true
      end=${end//[!0-9]/}   # digits only (a line end written on Windows)
      # an end time that is no number, or one beyond the longest window, counts as run out
      if ! [[ "$end" =~ ^[0-9]+$ ]] || [ "$end" -le "$now" ] || [ "$end" -gt $(( now + MAX * 60 )) ]; then
        rm -f "$f"
        ./alarm.sh "Maintenance page of $(basename "$f" .an) switched off after its end time${reason:+ ($reason)}: whoever switched it on did not (a script that stopped?). See ~/askurious-maintenance.log or the deploy's output" || true
      fi
    done
    ;;
  *) usage ;;
esac
