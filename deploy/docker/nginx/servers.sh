#!/bin/sh
# nginx's https names, one server block per name with its certificate at a fixed path (audit 2026-10-09b, B1). Runs at
# every start of nginx (docker-entrypoint.d, before 05-self-signed.sh) and writes /etc/nginx/servers/:
#   servers.conf  the blocks (askurious.conf.template includes them); what a side answers stands in
#                 /etc/nginx/locations/<side>.conf (nginx/locations/), the same for all of its names
#   names         one name per line: self-signed.sh makes sure each has a certificate, else nginx would not start
# A fixed path, not one picked by $ssl_server_name: only the master (root) reads the keys, at the start and at each
# reload, never a worker and not at every handshake. nginx compares the name the client asked for in lower case and
# without a dot at the end; the names here are written the same way.
# The old names (OLD_*, only redirect) are left out when empty (B4): then nginx starts without them.
set -e
dir=${SERVERS_DIR:-/etc/nginx/servers}      # another folder only for the tests
mkdir -p "$dir"
conf="$dir/servers.conf.new"
list="$dir/names.new"
: > "$conf"
: > "$list"
# HSTS for every name; with HSTS_SUBDOMAINS=1 in the .env also includeSubDomains (audit 2026-10-18, CFG-10): every
# name this nginx serves answers on 443 only (port 80 redirects), but a sub-domain elsewhere in the DNS zone might
# not, and a browser keeps the rule a year. Off until the zones are checked (BETRIEB.md). No preload.
if [ "${HSTS_SUBDOMAINS:-0}" = "1" ]; then hsts="max-age=31536000; includeSubDomains"; else hsts="max-age=31536000"; fi
echo "map \"\" \$askurious_hsts { default \"$hsts\"; }" >> "$conf"

clean() { echo "$1" | tr 'A-Z' 'a-z' | sed 's/\.$//'; }

# server NAME LINE...: a block for NAME with the given lines; a name twice or not a host name is left out
server() {
  host=$(clean "$1")
  shift
  [ -n "$host" ] || return 0                 # a name not set (an old one switched off, B4)
  case "$host" in
    *[!a-z0-9.-]*|.*|-*|*..*) echo "not a host name: $host (left out)" >&2; return 0 ;;
  esac
  if grep -qxF "$host" "$list"; then return 0; fi
  echo "$host" >> "$list"
  {
    echo "server {"
    echo "    listen 443 ssl;"
    echo "    listen [::]:443 ssl;"
    echo "    http2 on;"
    echo "    server_name $host;"
    echo "    ssl_certificate     /etc/nginx/certs/$host/fullchain.pem;"
    echo "    ssl_certificate_key /etc/nginx/certs/$host/privkey.pem;"
    echo '    add_header Strict-Transport-Security $askurious_hsts always;'
    # the protective headers on nginx's own answers, where the app sent none (askurious.conf.template, DP-13)
    echo '    add_header X-Content-Type-Options $own_nosniff always;'
    echo '    add_header X-Frame-Options $own_frame_options always;'
    echo '    add_header Referrer-Policy $own_referrer_policy always;'
    for line in "$@"; do echo "    $line"; done
    echo "}"
  } >> "$conf"
}

# the name the maintenance page carries under a host (locations/wartung.conf): the brand of BRANDS ("a.de=A,b.de=B",
# app/brand.py) whose domain the host is or lies under, else Askurious; only letters, digits, spaces, dots, dashes
brand_name() {
  h=$(clean "$1")
  echo "${BRANDS:-}" | tr ',' '\n' | while IFS='=' read -r d n; do
    d=$(clean "$(echo "$d" | tr -d ' ')")
    [ -n "$d" ] || continue
    case "$h" in "$d"|*."$d") echo "$n" | tr -cd 'A-Za-z0-9 .-' | sed 's/^ *//; s/ *$//'; break ;; esac
  done | head -n 1 | grep . || echo Askurious
}

# the researchers' side: askurious.de and each further brand with a name of its own (BRANDS, app/brand.py)
for h in "$RESEARCHER_HOST" $BRAND_RESEARCHER_HOSTS; do
  server "$h" "set \$wartung_name \"$(brand_name "$h")\";" "include /etc/nginx/locations/researcher.conf;"
done
# the participants' side: ask.askurious.de and e.g. ask.polltergeist.de
for h in "$PARTICIPANT_HOST" $BRAND_PARTICIPANT_HOSTS; do
  server "$h" "set \$wartung_name \"$(brand_name "$h")\";" "include /etc/nginx/locations/participant.conf;"
done
# the status page (app/status_node.py): status.askurious.de and status.<brand> of each further brand
for h in "$STATUS_HOST" $(for b in $BRAND_RESEARCHER_HOSTS; do echo "status.$(clean "$b")"; done); do
  server "$h" "include /etc/nginx/locations/status.conf;"
done

# The names from before the rename to Askurious (2026-10-09): each one redirects to its new name, path and query
# kept, so that links already handed out (invitations, QR codes, papers, bookmarks) keep working. 308, not 301: a
# page of answers sent to the old name in the moment of the switch stays a POST (the browser sends it again to the
# new name; there the session cookie is missing, so the person starts the page again instead of losing it silently).
for h in $OLD_RESEARCHER_HOST; do
  server "$h" "location / { return 308 https://$(clean "$RESEARCHER_HOST")\$request_uri; }"
done
for h in $OLD_PARTICIPANT_HOST; do
  server "$h" "access_log off;             # no log of the participants' requests (B2)" \
    "location / { return 308 https://$(clean "$PARTICIPANT_HOST")\$request_uri; }"
done
for h in $OLD_STATUS_HOST; do
  server "$h" "location / { return 308 https://$(clean "$STATUS_HOST")\$request_uri; }"
done

# www.<researchers' name> of Askurious and of each further brand: only a redirect to the name without www (Ralf,
# 2026-10-10, S-B1), with a certificate of its own (deploy-docker.sh cert). WWW_REDIRECT=0 leaves them out, e.g. while
# a www name is not in the DNS (until then they get a self-signed certificate, so nginx starts either way).
if [ "${WWW_REDIRECT:-1}" != "0" ]; then
  for h in "$RESEARCHER_HOST" $BRAND_RESEARCHER_HOSTS; do
    h=$(clean "$h")
    [ -n "$h" ] || continue
    server "www.$h" "location / { return 301 https://$h\$request_uri; }"
  done
fi

mv -f "$conf" "$dir/servers.conf"
mv -f "$list" "$dir/names"
echo "https names: $(tr '\n' ' ' < "$dir/names")"
