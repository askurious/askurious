#!/bin/sh
# A further brand with its own containers and database (profile "brand" in compose.yaml, datenbank-je-marke-konzept.md,
# prepared 2026-10-09). At every start of nginx (docker-entrypoint.d) it writes /etc/nginx/brand/: with
# BRAND_STACK_DOMAIN (e.g. polltergeist.de) the upstreams of brand-researcher and brand-participant and the lines that
# send that domain and its sub-domains (ask.polltergeist.de) there (askurious.conf.template, maps $researcher_pool and
# $participant_pool). Without it the files stay empty and every name goes to the Askurious containers, as before.
# The names themselves, their certificates and the paths let through stay those of BRAND_*_HOSTS.
set -e
dir=${BRAND_NGINX_DIR:-/etc/nginx/brand}      # another folder only for the tests
mkdir -p "$dir"
: > "$dir/upstreams.conf"
: > "$dir/researcher-map.conf"
: > "$dir/participant-map.conf"
domain=$(echo "${BRAND_STACK_DOMAIN:-}" | tr 'A-Z' 'a-z' | sed 's/\.$//')
[ -n "$domain" ] || exit 0
case "$domain" in
  *[!a-z0-9.-]*|.*|*..*) echo "BRAND_STACK_DOMAIN is not a domain name: $domain (left out)" >&2; exit 0 ;;
esac
# never the names of Askurious, nor a domain that is not one of the brands (a typo would send all of Askurious, or a
# stranger's traffic, to the brand's empty database; audit 2026-10-10, B2): left out, every name stays with Askurious.
# restart.sh refuses the same .env before it starts anything.
for host in "${RESEARCHER_HOST:-askurious.de}" "${PARTICIPANT_HOST:-}" "${STATUS_HOST:-}"; do
  host=$(echo "$host" | tr 'A-Z' 'a-z')
  [ -n "$host" ] || continue
  case "$host" in "$domain"|*."$domain") echo "BRAND_STACK_DOMAIN=$domain covers $host of Askurious (left out)" >&2; exit 0 ;; esac
  case "$domain" in *."$host") echo "BRAND_STACK_DOMAIN=$domain lies under $host of Askurious (left out)" >&2; exit 0 ;; esac
done
listed=""
for host in $(echo "${BRAND_RESEARCHER_HOSTS:-}" | tr 'A-Z' 'a-z'); do [ "${host%.}" = "$domain" ] && listed=1; done
[ -n "$listed" ] || { echo "BRAND_STACK_DOMAIN=$domain is not in BRAND_RESEARCHER_HOSTS (left out)" >&2; exit 0; }
pattern=$(echo "$domain" | sed 's/[.]/\\./g')
cat > "$dir/upstreams.conf" <<CONF
upstream brand_researcher  { zone brand_researcher 64k;  server brand-researcher:8000 resolve;  keepalive 16; }
upstream brand_participant { zone brand_participant 64k; server brand-participant:8000 resolve max_fails=0; keepalive 32; }
CONF
echo "~(^|\.)$pattern\$ brand_researcher;" > "$dir/researcher-map.conf"
echo "~(^|\.)$pattern\$ brand_participant;" > "$dir/participant-map.conf"
echo "$domain goes to the brand's own containers"
