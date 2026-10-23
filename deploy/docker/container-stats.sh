#!/usr/bin/env bash
# Load of the containers for Administration > Server, section "Container" (app/containers.py; Ralf, 2026-10-09).
# Runs every minute from the crontab (deploy-docker.sh) on the host and writes what `docker stats` and
# `docker inspect` say into ~/askurious-status, the folder the status container already reads; the researchers'
# container sees it read-only. No container gets the Docker socket for this: a container that can talk to Docker
# could start any other container on the server.
#   containers.json          the newest minute: host, every container's CPU, memory, processes, start and restarts
#   containers-history.jsonl one line per minute, the last 24 hours (the peaks on the page)
set -euo pipefail
cd "$(dirname "$0")"
. ./compose-env.sh
out="$HOME/askurious-status"
mkdir -p "$out" && chmod 755 "$out"
umask 022
now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
ids=$(docker compose ps -q)
[ -n "$ids" ] || exit 0
# CPU as docker prints it: 100 % is one core
stats=$(docker stats --no-stream --format '{"name":"{{.Name}}","cpu":"{{.CPUPerc}}","mem":"{{.MemUsage}}","pids":"{{.PIDs}}"}' $ids | paste -sd, -)
info=$(docker inspect --format '{"name":{{json .Name}},"service":{{json (index .Config.Labels "com.docker.compose.service")}},"state":{{json .State}},"restarts":{{.RestartCount}},"nano_cpus":{{.HostConfig.NanoCpus}},"mem_limit":{{.HostConfig.Memory}}}' $ids | paste -sd, -)
mem=$(awk '/^MemTotal:/{t=$2} /^MemAvailable:/{a=$2} END{printf "%d,%d", t*1024, a*1024}' /proc/meminfo)
load=$(cut -d' ' -f1-3 /proc/loadavg | tr ' ' ,)
line="{\"time\":\"$now\",\"stats\":[$stats]}"
printf '{"time":"%s","host":{"cpus":%d,"mem_total":%s,"mem_available":%s,"load":[%s]},"stats":[%s],"info":[%s]}\n' \
  "$now" "$(nproc)" "${mem%,*}" "${mem#*,}" "$load" "$stats" "$info" > "$out/containers.json.part"
mv "$out/containers.json.part" "$out/containers.json"
# the history: one line per minute, the newest 1440 stay
{ tail -n 1439 "$out/containers-history.jsonl" 2>/dev/null || true; echo "$line"; } > "$out/containers-history.jsonl.part"
mv "$out/containers-history.jsonl.part" "$out/containers-history.jsonl"
