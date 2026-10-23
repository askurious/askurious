#!/usr/bin/env bash
# The app's own PostgreSQL role without superuser rights (audit 2026-10-09, BT4; steps in BETRIEB.md). By hand on
# the server, in ~/askurious/deploy/docker; never part of a deploy. Prints no secret.
#
#   ./db-app-role.sh           role "askurious_app" (LOGIN, no SUPERUSER, CREATEDB, CREATEROLE, REPLICATION,
#                              BYPASSRLS) becomes the owner of the database and of everything in it, so that it
#                              can run the migrations; DB_APP_USER and DB_APP_PASSWORD go into the .env (once, a
#                              fresh password). The next deploy (restart.sh) starts the app with this role.
#   ./db-app-role.sh --lock    later, when the app runs as askurious_app: the superuser "askurious" loses its
#                              password and can no longer sign in over the network, only with
#                              "docker compose exec db psql -U askurious" inside the container (backup.sh, restart.sh)
#   ./db-app-role.sh --unlock  the superuser gets POSTGRES_PASSWORD back: before a rollback to code from before this
#                              script (its compose.yaml signs in as "askurious")
#
# The official postgres image makes POSTGRES_USER ("askurious") the cluster's first superuser, which PostgreSQL never
# lets lose that right; so the app gets a role of its own instead.
set -euo pipefail
umask 077
cd "$(dirname "$0")"
C="docker compose"
ROLE=askurious_app

[ -f .env ] || { echo "No .env here (a link to ~/askurious-env/.env): deploy first." >&2; exit 1; }
ENV_FILE=$(readlink -f .env)
value() { sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1 | tr -d '\r'; }
psql_su() { $C exec -T db psql -U askurious -d askurious -v ON_ERROR_STOP=1 -q -X; }

case "${1:-}" in
  --lock)
    [ "$(value DB_APP_USER)" = "$ROLE" ] || { echo "DB_APP_USER is not $ROLE in the .env: run ./db-app-role.sh first." >&2; exit 1; }
    running=$($C exec -T db psql -U askurious -d askurious -AtX -c \
      "SELECT count(*) FROM pg_stat_activity WHERE usename = '$ROLE'")
    [ "${running:-0}" -gt 0 ] || { echo "No connection of $ROLE yet: deploy (restart.sh) first, then --lock." >&2; exit 1; }
    echo "ALTER ROLE askurious PASSWORD NULL;" | psql_su
    echo "The superuser askurious signs in only inside the db container now (docker compose exec db psql -U askurious)."
    exit 0 ;;
  --unlock)
    pw=$(value POSTGRES_PASSWORD)
    [[ "$pw" =~ ^[A-Za-z0-9]+$ ]] || { echo "POSTGRES_PASSWORD in the .env is empty or not letters and digits." >&2; exit 1; }
    echo "ALTER ROLE askurious PASSWORD '$pw';" | psql_su
    echo "The superuser askurious has its password (POSTGRES_PASSWORD) again."
    exit 0 ;;
  "") ;;
  *) echo "usage: $0 [--lock|--unlock]" >&2; exit 2 ;;
esac

pw=$(value DB_APP_PASSWORD)
if [ -z "$pw" ]; then
  pw=$(openssl rand -hex 24)
  { [ -z "$(tail -c 1 "$ENV_FILE")" ] || echo; echo "DB_APP_USER=$ROLE"; echo "DB_APP_PASSWORD=$pw"; } >> "$ENV_FILE"
  echo "DB_APP_USER and DB_APP_PASSWORD written to $ENV_FILE"
elif [ "$(value DB_APP_USER)" != "$ROLE" ]; then
  echo "The .env has DB_APP_PASSWORD but DB_APP_USER is not $ROLE: correct it by hand." >&2; exit 1
fi
[[ "$pw" =~ ^[A-Za-z0-9]+$ ]] || { echo "DB_APP_PASSWORD in the .env is not letters and digits." >&2; exit 1; }

# through stdin, so that the password shows in no process list
psql_su <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$ROLE') THEN CREATE ROLE $ROLE LOGIN; END IF;
END \$\$;
ALTER ROLE $ROLE LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD '$pw';
ALTER DATABASE askurious OWNER TO $ROLE;
ALTER SCHEMA public OWNER TO $ROLE;
DO \$\$
DECLARE r record;
BEGIN
  -- tables, views, sequences of their own (a serial column's sequence moves with its table)
  FOR r IN
    SELECT c.oid::regclass AS name, c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
      AND pg_get_userbyid(c.relowner) <> '$ROLE'
      AND NOT (c.relkind = 'S' AND EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_class'::regclass
                                             AND d.objid = c.oid AND d.deptype IN ('a', 'i')))
  LOOP
    EXECUTE format('ALTER %s %s OWNER TO $ROLE',
                   CASE r.relkind WHEN 'v' THEN 'VIEW' WHEN 'm' THEN 'MATERIALIZED VIEW' WHEN 'f' THEN 'FOREIGN TABLE'
                                  WHEN 'S' THEN 'SEQUENCE' ELSE 'TABLE' END, r.name);
  END LOOP;
  -- types of their own (enums, domains, ranges, composite types), not the row and array types of tables
  FOR r IN
    SELECT t.oid::regtype AS name, t.typtype FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
    WHERE n.nspname = 'public' AND t.typtype IN ('e', 'd', 'r', 'c') AND t.typelem = 0
      AND (t.typtype <> 'c' OR (SELECT relkind FROM pg_class WHERE oid = t.typrelid) = 'c')
      AND pg_get_userbyid(t.typowner) <> '$ROLE'
  LOOP
    EXECUTE format('ALTER %s %s OWNER TO $ROLE', CASE r.typtype WHEN 'd' THEN 'DOMAIN' ELSE 'TYPE' END, r.name);
  END LOOP;
  FOR r IN
    SELECT p.oid::regprocedure AS name FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'public' AND pg_get_userbyid(p.proowner) <> '$ROLE'
  LOOP
    EXECUTE format('ALTER ROUTINE %s OWNER TO $ROLE', r.name);
  END LOOP;
END \$\$;
SQL
left=$($C exec -T db psql -U askurious -d askurious -AtX -c \
  "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
   WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm') AND pg_get_userbyid(c.relowner) <> '$ROLE'")
[ "$(echo "$left" | tr -d '[:space:]')" = 0 ] || { echo "Still $left tables or views not owned by $ROLE." >&2; exit 1; }
echo "$ROLE owns the database. Next: deploy (or ./restart.sh --no-build), then ./db-app-role.sh --lock (BETRIEB.md)."
