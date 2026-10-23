#!/usr/bin/env bash
# After the server boots (crontab "@reboot", deploy-docker.sh puts it there; Ralf, 2026-10-17): the containers come
# back by themselves (restart: unless-stopped, Docker started by systemd). This waits until they have settled and
# tells the operators' Slack channel (alarm.sh) that the server restarted and which containers are not running or
# not healthy, so that nobody has to notice a reboot on the status page. A restart.lock left by a reboot during a
# deploy or the maintenance window is from before the boot; restart.sh removes such a lock itself, this only says so.
# Switches the maintenance page off (wartung.sh); changes nothing else and starts nothing.
set -uo pipefail
cd "$(dirname "$0")"
. ./compose-env.sh || exit 1
WAIT=${AFTER_BOOT_WAIT:-600}           # seconds at most until every container runs and is healthy
STEP=${AFTER_BOOT_STEP:-15}

# the containers of this project that are not (yet) running and healthy; a container without a healthcheck counts
# when it runs. Profiles never started have no container and are not listed.
not_ready() {
  docker compose ps -a --format '{{.Service}} {{.State}} {{.Health}}' 2>/dev/null \
    | awk '$2 != "running" || ($3 != "" && $3 != "healthy") {print $1 " (" $2 ($3 != "" ? ", " $3 : "") ")"}'
}

# a maintenance page left on by a reboot during a deploy or a window (wartung.sh): its script runs no more
if ls "$HOME/askurious-status/wartung/"*.an >/dev/null 2>&1; then ./wartung.sh aus alle "Neustart des Servers" || true; fi
waited=0
until docker info >/dev/null 2>&1; do
  [ "$waited" -lt "$WAIT" ] || { ./alarm.sh "Server restarted, but Docker does not answer after ${WAIT} s: is the docker service enabled (systemctl is-enabled docker)?"; exit 1; }
  sleep "$STEP"; waited=$((waited + STEP))
done
while :; do
  missing=$(not_ready)
  [ -n "$missing" ] && [ "$waited" -lt "$WAIT" ] || break
  sleep "$STEP"; waited=$((waited + STEP))
done
count=$(docker compose ps -q 2>/dev/null | wc -l | tr -d ' ')
note=""
[ ! -d "$HOME/askurious-env/restart.lock" ] \
  || note=" restart.lock from before the boot is still there; the next restart.sh removes it."
if [ -n "$missing" ]; then
  ./alarm.sh "Server restarted. Not running or not healthy after ${waited} s: $(echo "$missing" | awk 'NR > 1 {printf ", "} {printf "%s", $0}').$note"
  exit 1
fi
./alarm.sh "Server restarted: all $count containers run and are healthy (after ${waited} s).$note"
