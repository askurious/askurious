#!/bin/sh
# The two participants' containers in nginx's upstream "participant" (askurious.conf.template includes
# /etc/nginx/upstream/participant-*.conf). At the start of nginx (docker-entrypoint.d, no arguments) both are in.
# During a deploy restart.sh calls it inside the container:
#   06-participant-upstream.sh participant-a down   no new request goes to participant-a; nginx reloads
#   06-participant-upstream.sh participant-a up     back in
#   06-participant-upstream.sh participant-a up --handoff route only to the healthy updated container
# A reload keeps every running request; only new ones see the change. One of the two always stays in.
set -e
dir=/etc/nginx/upstream
# max_fails=0: a failed connection never takes a container out of the group. nginx's default (1 failure, 10 s out)
# took out both under load (load test 2026-10-09: bursts of 502 from 500 at once on, when each had failed once
# within 10 s); with one server nginx ignores it, so it never showed before. A container that refuses a connection
# still sends that request on to the other (proxy_next_upstream); a planned restart goes through "down" above.
line() { echo "server $1:8000 resolve max_fails=0$2;"; }

if [ $# -eq 0 ]; then
  mkdir -p "$dir"
  for p in participant-a participant-b; do line "$p" "" > "$dir/$p.conf"; done
  exit 0
fi
case "$1" in participant-a) other=participant-b ;; participant-b) other=participant-a ;;
  *) echo "usage: $0 participant-a|participant-b up|down" >&2; exit 2 ;; esac
case "$2" in
  down) if grep -q " down;" "$dir/$other.conf"; then echo "$other is down already: $1 stays in" >&2; exit 1; fi
        line "$1" " down" > "$dir/$1.conf" ;;
  up) line "$1" "" > "$dir/$1.conf"
      if [ "${3:-}" = --handoff ]; then line "$other" " down" > "$dir/$other.conf"; fi ;;
  switch) line "$1" "" > "$dir/$1.conf"
          line "$other" " down" > "$dir/$other.conf" ;;
  *) echo "usage: $0 participant-a|participant-b up|down" >&2; exit 2 ;;
esac
nginx -t -q && nginx -s reload
