#!/usr/bin/env bash
# A further brand with its own database (datenbank-je-marke-konzept.md, prepared 2026-10-09). By hand on the server,
# in ~/askurious/deploy/docker; never part of a deploy, and it switches nothing on: the brand keeps running on the
# Askurious containers and database until COMPOSE_PROFILES=brand and BRAND_STACK_DOMAIN are in the .env
# (BETRIEB.md, "Eigene Datenbank für eine Marke"). Prints no secret.
#
#   ./brand-db.sh polltergeist.de POLLtergeist
#
# - database "polltergeist" in the same PostgreSQL, owned by its own role "polltergeist_app" (LOGIN, no superuser),
#   which cannot connect to the database of Askurious and the other way round (CONNECT taken from PUBLIC on both)
# - ~/askurious-env/.env.brand with its own SECRET_KEY, PARTICIPANT_TOKEN_KEY, DATABASE_URL, SITE_BRAND and addresses on
#   the brand's domain, plus the sign-in and imprint lines of the .env (ORCID_*, ADMIN_ORCIDS, IMPRINT_*) without
#   secrets; never overwritten
# - ~/askurious-env/.env.brand-researcher with what only the researchers' side may hold: its own TOKEN_KEY and
#   RESEARCHER_SESSION_KEY and the ORCID client secret of the .env. The brand's participants' container does not read
#   it; further secrets of the researchers' side (mail, AI, Whisper, Zenodo, Slack) belong there too
# - BRAND_COMPUTE_SECRET in the .env (the brand's own Rechenknecht)
# The containers create the tables themselves at their first start (app/migrations.py).
set -euo pipefail
umask 077
cd "$(dirname "$0")"
C="docker compose"

domain=$(echo "${1:-}" | tr 'A-Z' 'a-z' | sed 's/\.$//')
name=${2:-}
if [ -z "$domain" ] || [ -z "$name" ]; then echo "usage: $0 <domain> <name>   e.g. $0 polltergeist.de POLLtergeist" >&2; exit 2; fi
[[ "$domain" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$ ]] || { echo "Not a domain name: $domain" >&2; exit 2; }
[[ "$name" =~ ^[A-Za-z0-9\ ._-]+$ ]] || { echo "The name may hold letters, digits, space, . _ - only: $name" >&2; exit 2; }
db=$(echo "${domain%%.*}" | tr -c 'a-z0-9\n' '_')
[ "$db" != askurious ] && [ "$db" != postgres ] || { echo "The database would be called $db: not possible." >&2; exit 2; }
role=${db}_app

[ -f .env ] || { echo "No .env here (a link to ~/askurious-env/.env): deploy first." >&2; exit 1; }
ENV_FILE=$(readlink -f .env)
ENV_DIR=$(dirname "$ENV_FILE")
BRAND_ENV=$ENV_DIR/.env.brand
RESEARCHER_ENV=$ENV_DIR/.env.brand-researcher
[ ! -e "$BRAND_ENV" ] || { echo "$BRAND_ENV exists already: nothing changed (remove it by hand to start again)." >&2; exit 1; }
[ ! -e "$RESEARCHER_ENV" ] || { echo "$RESEARCHER_ENV exists already: nothing changed (remove it by hand to start again)." >&2; exit 1; }
psql_su() { $C exec -T db psql -U askurious -d askurious -v ON_ERROR_STOP=1 -q -X; }

pw=$(openssl rand -hex 24)
[[ "$pw" =~ ^[A-Za-z0-9]+$ ]] || { echo "openssl gave no password." >&2; exit 1; }
# through stdin, so that the password shows in no process list; CREATE DATABASE cannot run inside DO
psql_su <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$role') THEN CREATE ROLE $role LOGIN; END IF;
END \$\$;
ALTER ROLE $role LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD '$pw';
SELECT 'CREATE DATABASE $db OWNER $role' WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = '$db')
\gexec
ALTER DATABASE $db OWNER TO $role;
REVOKE CONNECT ON DATABASE $db FROM PUBLIC;
REVOKE CONNECT ON DATABASE askurious FROM PUBLIC;
SQL
echo "PostgreSQL: database $db, owned by $role"

{
  echo "# $name with its own database (brand-db.sh, $(date -u +%Y-%m-%d)); compose.yaml, profile \"brand\""
  echo "SECRET_KEY=$(openssl rand -hex 32)"
  echo "PARTICIPANT_TOKEN_KEY=$(openssl rand -hex 32)"
  echo "DATABASE_URL=postgresql://$role:$pw@db/$db"
  echo "SITE_BRAND=$domain=$name"
  echo "PUBLIC_BASE_URL=https://$domain"
  echo "PARTICIPANT_BASE_URL=https://ask.$domain"
  echo "STATUS_URL=https://status.$domain/"
  echo "MAIL_FROM_NAME=$name"
  grep -E '^(ORCID_[A-Z_]+|ADMIN_ORCIDS|IMPRINT_[A-Z_]+)=' "$ENV_FILE" | grep -v '^ORCID_CLIENT_SECRET=' || true
} > "$BRAND_ENV"
chmod 600 "$BRAND_ENV"
ln -sfn "$BRAND_ENV" .env.brand
echo "$BRAND_ENV written and linked here"
{
  echo "# $name: secrets of the researchers' side only (brand-db.sh, $(date -u +%Y-%m-%d)); never read by brand-participant"
  echo "TOKEN_KEY=$(openssl rand -hex 32)"
  echo "RESEARCHER_SESSION_KEY=$(openssl rand -hex 32)"
  grep -E '^ORCID_CLIENT_SECRET=' "$ENV_FILE" || true
} > "$RESEARCHER_ENV"
chmod 600 "$RESEARCHER_ENV"
ln -sfn "$RESEARCHER_ENV" .env.brand-researcher
echo "$RESEARCHER_ENV written and linked here"

if ! grep -q '^BRAND_COMPUTE_SECRET=.' "$ENV_FILE"; then
  { [ -z "$(tail -c 1 "$ENV_FILE")" ] || echo; echo "BRAND_COMPUTE_SECRET=$(openssl rand -hex 32)"; } >> "$ENV_FILE"
  echo "BRAND_COMPUTE_SECRET added to $ENV_FILE"
fi
echo "Nothing switched on yet. Next steps: BETRIEB.md, \"Eigene Datenbank für eine Marke\"."
