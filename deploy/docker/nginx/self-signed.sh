#!/bin/sh
# nginx reads each host's certificate from /etc/nginx/certs/<host>/: Let's Encrypt's, once certbot has issued it,
# until then a self-signed one, so that nginx starts and 443 works from the first day (the browser warns).
# Self-signed ones stay out of /etc/letsencrypt/live, where certbot wants to create its own. Runs at every start
# of the container (docker-entrypoint.d) and before each reload (compose.yaml), so renewed certificates come along.
# The names come from servers.sh (04-servers.sh, run before): every https name of nginx, the old ones (OLD_*, only
# redirect) and the further brands with their status names included; nginx does not start while one is missing.
# Copies at a fixed path, root's and 0600: only nginx's master (root) reads them, at the start and at each reload,
# never a worker (audit 2026-10-09b, B1). The copy keeps the path the same when certbot's replaces the self-signed one.
set -e
umask 077
names=${SERVERS_DIR:-/etc/nginx/servers}/names
[ -f "$names" ] || { echo "$names is missing (04-servers.sh writes it)" >&2; exit 1; }
for host in $(cat "$names"); do
  live=/etc/letsencrypt/live/$host
  own=/etc/letsencrypt/self-signed/$host
  if [ ! -f "$live/fullchain.pem" ] && [ ! -f "$own/fullchain.pem" ]; then
    # openssl comes with the image (nginx-image/Dockerfile), never installed at run time (audit 2026-10-10, B7)
    command -v openssl >/dev/null || { echo "no openssl in the nginx image: build it (compose.yaml, nginx-image)" >&2; exit 1; }
    mkdir -p "$own"
    openssl req -x509 -nodes -newkey rsa:2048 -days 365 -subj "/CN=$host" -addext "subjectAltName=DNS:$host" \
      -keyout "$own/privkey.pem" -out "$own/fullchain.pem" 2>/dev/null
    echo "self-signed certificate for $host"
  fi
  src=$own; [ -f "$live/fullchain.pem" ] && src=$live
  dst=/etc/nginx/certs/$host
  if [ -L "$dst" ]; then rm "$dst"; fi       # a link of the way before into the volume: never write through it
  mkdir -p "$dst"
  # written under umask 077 (never readable by others, not even for a moment) and moved into place in one step; the
  # key replaces the copy of the way before, which belonged to the user nginx
  cat "$src/fullchain.pem" > "$dst/fullchain.pem.new"
  cat "$src/privkey.pem" > "$dst/privkey.pem.new"
  chmod 644 "$dst/fullchain.pem.new"
  mv -f "$dst/fullchain.pem.new" "$dst/fullchain.pem"
  mv -f "$dst/privkey.pem.new" "$dst/privkey.pem"
done
