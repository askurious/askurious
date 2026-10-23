#!/usr/bin/env bash
# The participants' own PostgreSQL role (audit 2026-10-18, CFG-04; steps in BETRIEB.md, "Eigene Datenbankrolle der
# Teilnahmeseite"). By hand on the server, in ~/askurious/deploy/docker, in a quiet window; never part of a deploy.
# Needs DB_ISOLATION=1 (isolation/README.md): the migration job gives the role its rights after every migration.
# Prints no secret.
#
#   ./participant-db-role.sh          role "askurious_participant" (LOGIN, NOINHERIT, no SUPERUSER, CREATEDB,
#                                     CREATEROLE, REPLICATION, BYPASSRLS, owns nothing) with exactly the rights of
#                                     app/participant_grants.py; PARTICIPANT_DB_USER and PARTICIPANT_DB_PASSWORD go into
#                                     the .env (a fresh password, once). Running again is harmless: it gives the rights
#                                     anew. The containers take it at the next ./restart.sh --no-build, one after the
#                                     other.
#   ./participant-db-role.sh --off    the way back: the two lines leave the .env; then ./restart.sh --no-build, and
#                                     the participants run with the runtime role again
#   ./participant-db-role.sh --drop   after --off and the restart: the role and its rights go (refused while it is
#                                     still connected)
#   ./participant-db-role.sh --brand [--off|--drop]
#                                     the same for the brand with its own database (profile "brand"): role
#                                     "<database>_participant", DATABASE_URL in ~/askurious-env/.env.brand-participant,
#                                     PARTICIPANT_ROLE in .env.brand-migration
set -euo pipefail
umask 077
cd "$(dirname "$0")"
C="docker compose"

brand=""
if [ "${1:-}" = --brand ]; then brand=1; shift; fi
action=${1:-on}
case "$action" in on|--off|--drop) ;; *) echo "usage: $0 [--brand] [--off|--drop]" >&2; exit 2 ;; esac

[ -f .env ] || { echo "No .env here (a link to ~/askurious-env/.env): deploy first." >&2; exit 1; }
. ./compose-env.sh
[ "$ISOLATION_ACTIVE" = 1 ] || { echo "Needs DB_ISOLATION=1 first (isolation/README.md): its migration job gives the role its rights." >&2; exit 1; }
ENV_FILE=$(readlink -f .env)
ENV_DIR=$(dirname "$ENV_FILE")
value() { sed -n "s/^$2=//p" "$1" 2>/dev/null | tail -n 1 | tr -d '\r'; }
append() { { [ ! -s "$1" ] || [ -z "$(tail -c 1 "$1")" ] || echo; printf '%s\n' "${@:2}"; } >> "$1"; }

if [ -n "$brand" ]; then
  [ -r .env.brand ] || { echo "No .env.brand: the brand has no database of its own (brand-db.sh)." >&2; exit 1; }
  url=$(value .env.brand DATABASE_URL)
  database=${url##*/}; database=${database%%\?*}
  JOB_ENV=$ENV_DIR/.env.brand-migration
  OWN_ENV=$ENV_DIR/.env.brand-participant
  [ -r "$JOB_ENV" ] || { echo "No $JOB_ENV: the brand's isolation is not set up (isolation/README.md)." >&2; exit 1; }
  JOB=brand-migrate
else
  database=askurious
  JOB=migrate
fi
[[ "$database" =~ ^[a-z][a-z0-9_]{0,40}$ ]] || { echo "Unexpected database name." >&2; exit 1; }
ROLE=${database}_participant
psql_su() { $C exec -T db psql -U askurious -d "$database" -v ON_ERROR_STOP=1 -q -X; }

case "$action" in
  --off)
    if [ -n "$brand" ]; then
      rm -f "$OWN_ENV" .env.brand-participant
      sed -i '/^PARTICIPANT_ROLE=/d' "$JOB_ENV"
    else
      sed -i '/^PARTICIPANT_DB_USER=/d; /^PARTICIPANT_DB_PASSWORD=/d' "$ENV_FILE"
    fi
    echo "Switched off in the settings. Next: ./restart.sh --no-build (participants one after the other), then $0 ${brand:+--brand }--drop."
    exit 0 ;;
  --drop)
    if [ -n "$brand" ]; then
      [ ! -e "$OWN_ENV" ] && ! grep -q '^PARTICIPANT_ROLE=.' "$JOB_ENV" || { echo "Still switched on: $0 --brand --off first." >&2; exit 1; }
    else
      [ -z "$(value "$ENV_FILE" PARTICIPANT_DB_USER)" ] || { echo "Still in the .env: $0 --off and ./restart.sh --no-build first." >&2; exit 1; }
    fi
    running=$($C exec -T db psql -U askurious -d "$database" -AtX -c \
      "SELECT count(*) FROM pg_stat_activity WHERE usename = '$ROLE'")
    [ "$(echo "$running" | tr -d '[:space:]')" = 0 ] || { echo "$ROLE is still connected: ./restart.sh --no-build first." >&2; exit 1; }
    psql_su <<SQL
DO \$\$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$ROLE') THEN
    EXECUTE 'DROP OWNED BY $ROLE';
    EXECUTE 'DROP ROLE $ROLE';
  END IF;
END \$\$;
SQL
    echo "$ROLE removed."
    exit 0 ;;
esac

# switch on: the password stays where it is once written (a second run gives the rights anew, nothing else)
if [ -n "$brand" ]; then
  pw=""
  [ ! -r "$OWN_ENV" ] || { pw=$(value "$OWN_ENV" DATABASE_URL); pw=${pw#*://*:}; pw=${pw%%@*}; }
else
  pw=$(value "$ENV_FILE" PARTICIPANT_DB_PASSWORD)
  user=$(value "$ENV_FILE" PARTICIPANT_DB_USER)
  if [ -n "$pw" ] && [ "$user" != "$ROLE" ]; then
    echo "The .env has PARTICIPANT_DB_PASSWORD but PARTICIPANT_DB_USER is not $ROLE: correct it by hand." >&2; exit 1
  fi
fi
fresh=""
if [ -z "$pw" ]; then pw=$(openssl rand -hex 24); fresh=1; fi
[[ "$pw" =~ ^[A-Za-z0-9]+$ ]] || { echo "The participant password is not letters and digits." >&2; exit 1; }

# through stdin, so that the password shows in no process list
psql_su <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$ROLE') THEN CREATE ROLE $ROLE LOGIN NOINHERIT; END IF;
END \$\$;
ALTER ROLE $ROLE LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD '$pw';
SQL
# the role reaches no other database: CONNECT is PUBLIC's elsewhere unless the isolation took it (isolation/README.md)
others=$($C exec -T db psql -U askurious -d "$database" -AtX -c \
  "SELECT count(*) FROM pg_database WHERE datname <> '$database' AND datallowconn AND has_database_privilege('$ROLE', datname, 'CONNECT')")
[ "$(echo "$others" | tr -d '[:space:]')" = 0 ] || echo "Note: $ROLE may still connect to $others other database(s) through PUBLIC (isolation/README.md, step 3)." >&2

if [ -n "$fresh" ]; then
  if [ -n "$brand" ]; then
    printf 'DATABASE_URL=postgresql://%s:%s@db/%s\n' "$ROLE" "$pw" "$database" > "$OWN_ENV"
    chmod 600 "$OWN_ENV"
    ln -sfn "$OWN_ENV" .env.brand-participant
    echo "$OWN_ENV written and linked here"
  else
    append "$ENV_FILE" "PARTICIPANT_DB_USER=$ROLE" "PARTICIPANT_DB_PASSWORD=$pw"
    echo "PARTICIPANT_DB_USER and PARTICIPANT_DB_PASSWORD written to $ENV_FILE"
  fi
fi
if [ -n "$brand" ] && ! grep -q "^PARTICIPANT_ROLE=$ROLE\$" "$JOB_ENV"; then
  sed -i '/^PARTICIPANT_ROLE=/d' "$JOB_ENV"
  append "$JOB_ENV" "PARTICIPANT_ROLE=$ROLE"
fi

# the rights of app/participant_grants.py, given by the migration job as after every migration
$C run --rm --no-deps "$JOB" python -m app.schema_migrate --grants-only
python3 isolation/transition.py preflight
echo "$ROLE has its rights. Next: ./restart.sh --no-build (participants one after the other), then the logs (BETRIEB.md)."
