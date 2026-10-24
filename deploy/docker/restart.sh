#!/usr/bin/env bash
# Starts the containers on the image just built (deploy-docker.sh, its rollback, maintenance.sh), on the server in
# ~/askurious/deploy/docker. The participants' side stays reachable (zwei-teilnahme-container-konzept.md):
#   0. a database dump (backup.sh) when the code brings migrations the database does not have yet: the way back
#      if a migration goes wrong (audit 2026-10-09, BT3; SKIP_BACKUP=1 when the caller has just made one)
#   1. the researchers' container first; it runs the migrations at its start (app/migrations.py)
#   2. participant-a out of nginx, restarted, healthy, back in; then participant-b the same way
#   3. a further brand's own containers, when the profile "brand" is on (COMPOSE_PROFILES=brand in the .env;
#      datenbank-je-marke-konzept.md): its researchers' container migrates its own database, then its one
#      participants' container follows (a short gap for that brand's participants only)
#   4. nginx restarted only when its files changed (a restart is a short gap for everyone); they go to the folder
#      nginx mounts (nginx-live, a link to ~/askurious-env/nginx) right before, written into the files already there
# For a moment old code (the participant not yet restarted) runs against the new schema. That only works for
# migrations that add (a table, a column that may be empty, an index); each declares it with ROLLING = True
# (CLAUDE.md). Incompatible migrations stop the deploy before any container restart; split them into
# compatible deploys first. Ralf, 2026-10-13: never restart both participants together, never skip health checks.
# --no-build: start on the image there is (rollback).
set -euo pipefail
cd "$(dirname "$0")"
export COMPOSE_BAKE=false
C="docker compose"
PARTICIPANTS="participant-a participant-b"
NGINX_FILES="nginx/askurious.conf.template nginx/servers.sh nginx/locations/*.conf nginx/self-signed.sh nginx/participant-upstream.sh nginx/brand-stack.sh"

# the migrations after the database's version that do not declare ROLLING = True; "?" when the version is unknown.
# A migration that drops what the code stopped using may say DROPS_UNUSED_SINCE = "<revision>": it rolls once the
# database is at that revision or later, because the code running then (the participant not yet restarted) no
# longer reads what it drops (konto-schluessel-2b-vorschlag.md, E1 A, Ralf 2026-10-18). "No longer reads" means "no
# longer maps": SQLAlchemy names every mapped column in every query of its class, so the models of <revision> must
# not map what the drop takes (it lists it in DROPS; tests/platform/test_rolling_restart.py reads those models from
# git and refuses the migration otherwise, audit 2026-10-18, FUNC-01). This script has no git history on the server
# and trusts that check.
not_rolling() {
  local current="$1" f rev down since walked="" pending=""
  declare -A file prev
  for f in ../../migrations/versions/*.py; do
    [ -f "$f" ] || continue
    rev=$(sed -n "s/^revision = ['\"]\([^'\"]*\)['\"].*/\1/p" "$f")
    down=$(sed -n "s/^down_revision = ['\"]\([^'\"]*\)['\"].*/\1/p" "$f")
    [ -n "$rev" ] || continue
    file[$rev]=$f; prev[$rev]=$down
  done
  # the newest: the one no other names as its predecessor
  local head="" r
  for r in "${!file[@]}"; do
    local named=""
    for f in "${prev[@]}"; do [ "$f" = "$r" ] && named=1 && break; done
    [ -z "$named" ] && head=$r
  done
  r=$head
  while [ -n "$r" ] && [ "$r" != "$current" ]; do
    [ -n "${file[$r]:-}" ] || { echo "?"; return; }
    walked="$walked $r "
    grep -q "^ROLLING = True" "${file[$r]}" || pending="$pending $r"
    r=${prev[$r]:-}
  done
  [ "$r" = "$current" ] || { echo "?"; return; }
  for r in $pending; do
    since=$(sed -n "s/^DROPS_UNUSED_SINCE = ['\"]\([^'\"]*\)['\"].*/\1/p" "${file[$r]}")
    if [ -n "$since" ] && [ -n "${file[$since]:-}" ]; then
      case "$walked" in *" $since "*) basename "${file[$r]}" ;; esac  # that revision comes only with this deploy
    else
      basename "${file[$r]}"
    fi
  done
}

# the newest migration of this code: the one no other names as its predecessor
newest() {
  local f rev down revs="" downs=""
  for f in ../../migrations/versions/*.py; do
    [ -f "$f" ] || continue
    rev=$(sed -n "s/^revision = ['\"]\([^'\"]*\)['\"].*/\1/p" "$f")
    down=$(sed -n "s/^down_revision = ['\"]\([^'\"]*\)['\"].*/\1/p" "$f")
    if [ -n "$rev" ]; then revs="$revs $rev"; fi
    if [ -n "$down" ]; then downs="$downs $down"; fi
  done
  for rev in $revs; do
    case " $downs " in *" $rev "*) ;; *) echo "$rev"; return ;; esac
  done
}

check_migrations() {
  local current="$1" pending
  [ -n "$current" ] || return 0
  pending=$(not_rolling "$current")
  if [ -n "$pending" ]; then
    echo "Deploy refused: database $current cannot roll to this code ($pending). Split incompatible migrations into compatible deploys; participants stay running." >&2
    return 1
  fi
}
# deploy-docker.sh runs this locally against the live database version before uploading or swapping code.
if [ "${1:-}" = --check-migrations ]; then check_migrations "${2:-}"; exit $?; fi
. ./compose-env.sh
TARGET_COMPOSE_FILE=${COMPOSE_FILE:-}
if [ "$ISOLATION_ACTIVE" = 1 ]; then
  command -v python3 >/dev/null || { echo 'DB_ISOLATION requires host python3.' >&2; exit 1; }
fi
# compose.yaml no longer falls back to the database superuser: a .env without DB_APP_USER/DB_APP_PASSWORD (or, with
# DB_ISOLATION=1, without DB_RUN_USER/DB_RUN_PASSWORD) stops here with compose's own message, before anything changes
$C config --quiet || { echo "The compose configuration is incomplete (above). Nothing restarted." >&2; exit 1; }

healthy() {  # waits until the service answers /healthz inside its container (at most 120 s)
  local i
  for i in $(seq 1 120); do
    if $C exec -T "$1" python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)" \
        >/dev/null 2>&1; then return 0; fi
    sleep 1
  done
  echo "$1 did not answer within 120 s: stopped here, the other participant keeps the state from before" >&2
  return 1
}

housekeeping_ready() {
  local i
  for i in $(seq 1 90); do
    if $C exec -T "$1" python -c "import os,time; assert time.time()-os.stat('/tmp/housekeeping-heartbeat').st_mtime < 180" >/dev/null 2>&1; then return 0; fi
    sleep 2
  done
  echo "$1 did not complete housekeeping within 180 s" >&2
  return 1
}

upstream() { $C exec -T nginx /docker-entrypoint.d/06-participant-upstream.sh "$@" >/dev/null; }

nginx_sum() { cat $NGINX_FILES | sha256sum | cut -d' ' -f1; }

# the folder nginx mounts its files from (compose.yaml): deploy-docker.sh links it, here only when it is missing
LIVE=nginx-live
if [ ! -e "$LIVE" ]; then
  (umask 022; mkdir -p "$HOME/askurious-env/nginx")
  ln -sfn "$HOME/askurious-env/nginx" "$LIVE"
fi

# this code's nginx/ into that folder. Written into the files already there (cp onto a file keeps it): a mount holds
# the file it found at nginx's start, a new file in its place would stay unseen until nginx is created anew
sync_nginx() {
  local live f
  live=$(cd "$LIVE" && pwd -P)
  cp -pR nginx/. "$live/"
  (cd "$live" && find . -type f ! -name .mount-check) | while read -r f; do [ -e "nginx/$f" ] || rm -f "$live/$f"; done
}

nginx_ready() {  # nginx answers again after its start: the entrypoint scripts have run, the master is up
  local i
  for i in $(seq 1 30); do
    $C exec -T nginx sh -c 'test -s /run/nginx.pid && test -x /docker-entrypoint.d/06-participant-upstream.sh' \
      >/dev/null 2>&1 && return 0
    sleep 1
  done
  echo "nginx did not come up within 30 s" >&2
  return 1
}

# A brand's own database half switched on (audit 2026-10-10, B2) stops here, before anything is built or started:
#  - BRAND_STACK_DOMAIN without the profile "brand": nginx sends the brand's names to containers that do not run (502)
#  - the profile without BRAND_STACK_DOMAIN: the brand's containers run, but nginx sends nothing to them
#  - the profile without a readable .env.brand with SECRET_KEY and DATABASE_URL: they would start with the
#    development key on SQLite; without BRAND_COMPUTE_SECRET its Rechenknecht restarts for ever
#  - BRAND_STACK_DOMAIN that is (or holds, or lies under) a name of Askurious, or is not one of BRAND_RESEARCHER_HOSTS
#    (a typo): nginx would send Askurious' traffic, or a stranger's, to the brand's empty database
env_value() {  # the value compose sees: the environment first, then the last line of .env
  local v
  v=$(printenv "$1" || true)
  [ -n "$v" ] || v=$(sed -n "s/^$1=//p" .env 2>/dev/null | tail -n 1 | tr -d '\r')
  v=${v%\"}; v=${v#\"}; v=${v%\'}; v=${v#\'}
  echo "$v"
}
brand_refuse() { echo "Brand with its own database half switched on: $1. Nothing started (BETRIEB.md, \"Eigene Datenbank für eine Marke\")." >&2; exit 1; }
under() { [ "$1" = "$2" ] || [ "${1%."$2"}" != "$1" ]; }  # $1 is $2 or a name under it
check_brand() {
  local stack profile main host listed=""
  stack=$(env_value BRAND_STACK_DOMAIN | tr 'A-Z' 'a-z'); stack=${stack%.}
  profile=""
  if $C config --services 2>/dev/null | grep -qx brand-researcher; then profile=1; fi
  if [ -n "$stack" ] && [ -z "$profile" ]; then
    brand_refuse "BRAND_STACK_DOMAIN=$stack is set, but the profile \"brand\" is off (COMPOSE_PROFILES=brand in .env); either both or neither"
  fi
  [ -n "$profile" ] || return 0
  [ -n "$stack" ] || brand_refuse "the profile \"brand\" is on, but BRAND_STACK_DOMAIN is empty; either both or neither"
  { [ -r .env.brand ] && grep -q '^SECRET_KEY=.' .env.brand && grep -q '^DATABASE_URL=.' .env.brand; } \
    || brand_refuse "the profile \"brand\" is on, but .env.brand is missing or lacks SECRET_KEY or DATABASE_URL (brand-db.sh writes it); its containers would run with the development key on SQLite"
  [ -n "$(env_value BRAND_COMPUTE_SECRET)" ] || brand_refuse "the profile \"brand\" is on, but BRAND_COMPUTE_SECRET is empty (brand-db.sh writes it)"
  main=$(env_value RESEARCHER_HOST); main=${main:-askurious.de}
  for host in "$main" "$(env_value PARTICIPANT_HOST)" "$(env_value STATUS_HOST)"; do
    host=$(echo "$host" | tr 'A-Z' 'a-z'); [ -n "$host" ] || continue
    if under "$host" "$stack" || under "$stack" "$host"; then
      brand_refuse "BRAND_STACK_DOMAIN=$stack is the main domain or one of its names ($host); nginx would send Askurious to the brand's database"
    fi
  done
  for host in $(env_value BRAND_RESEARCHER_HOSTS | tr 'A-Z' 'a-z'); do [ "${host%.}" = "$stack" ] && listed=1; done
  [ -n "$listed" ] || brand_refuse "BRAND_STACK_DOMAIN=$stack is not in BRAND_RESEARCHER_HOSTS (a typo?)"
}
check_brand

# the database container that "up" will recreate: it runs another image than the one named (DB_IMAGE). Unknown
# counts as recreated (the page a few seconds too many rather than errors); none running is a first start
db_recreated() {
  local id running want image
  id=$($C ps -q db 2>/dev/null || true)
  [ -n "$id" ] || return 1
  image=$(env_value DB_IMAGE); image=${image:-postgres:17}
  running=$(docker inspect -f '{{.Image}}' "$id" 2>/dev/null || true)
  want=$(docker image inspect -f '{{.Id}}' "$image" 2>/dev/null || true)
  [ -z "$running" ] || [ -z "$want" ] || [ "$running" != "$want" ]
}

# The participants' own database role (participant-db-role.sh, audit 2026-10-18, CFG-04) half switched on stops here
# as well: both lines or neither (a user without its password would sign in with the runtime's and fail), and only
# with DB_ISOLATION=1, whose migration job gives the role its rights after every migration.
check_participant_role() {
  local user pass
  user=$(env_value PARTICIPANT_DB_USER); pass=$(env_value PARTICIPANT_DB_PASSWORD)
  [ -n "$user$pass" ] || return 0
  if [ -z "$user" ] || [ -z "$pass" ]; then
    echo "PARTICIPANT_DB_USER and PARTICIPANT_DB_PASSWORD: both or neither in .env. Nothing started (BETRIEB.md, \"Eigene Datenbankrolle der Teilnahmeseite\")." >&2; exit 1
  fi
  [ "$ISOLATION_ACTIVE" = 1 ] || { echo "PARTICIPANT_DB_USER needs DB_ISOLATION=1 (its rights come from the migration job). Nothing started." >&2; exit 1; }
}
check_participant_role

# No app container may sign in to the database as a role that may hand out rights or step past them (superuser,
# CREATEROLE, CREATEDB, REPLICATION, BYPASSRLS): every role named in a DATABASE_URL of the configuration (Askurious,
# participants, housekeeping, a brand) must exist and have none of these. Checked once the database runs (and, with
# DB_ISOLATION=1, after the migration job), before the first app container restarts; a refusal leaves the app
# containers running as they were. The configuration holds passwords: it is
# read into this function only, never printed. compose.yaml no longer falls back to the superuser "askurious"; a local
# test setup that wants it names it in the .env (DB_APP_USER=askurious) and does not use this script.
check_db_roles() {
  local users user out i
  users=$($C config --format json | grep -o '"DATABASE_URL": *"[a-z+]*://[^:@/"]*' | sed 's|.*://||' | sort -u \
    | tr '\n' ' ' || true)
  users=${users% }
  [ -n "$users" ] || { echo "No DATABASE_URL in the compose configuration: database roles not checked. Nothing restarted." >&2; exit 1; }
  for user in $users; do
    [[ "$user" =~ ^[a-z_][a-z0-9_]{0,62}$ ]] || { echo "Unexpected database role name in a DATABASE_URL. Nothing restarted." >&2; exit 1; }
  done
  out=""
  for i in $(seq 1 30); do
    out=$($C exec -T db psql -U askurious -d askurious -AtX -c "SELECT 'roles-checked:' || coalesce(string_agg(u, ' '), '')
      FROM unnest(string_to_array('$users', ' ')) AS u WHERE NOT EXISTS (SELECT 1 FROM pg_roles r WHERE r.rolname = u
        AND NOT r.rolsuper AND NOT r.rolcreaterole AND NOT r.rolcreatedb AND NOT r.rolreplication AND NOT r.rolbypassrls)" \
      2>/dev/null) && break
    sleep 2
  done
  out=$(echo "$out" | tr -d '\r' | tail -n 1)
  case "$out" in
    roles-checked:) ;;
    roles-checked:*)
      echo "Refused: the app containers would sign in to the database as ${out#roles-checked:}, a role that is missing or a superuser or may create roles or databases (DB_APP_USER, DB_RUN_USER, PARTICIPANT_DB_USER; db-app-role.sh, isolation/README.md). Nothing restarted." >&2
      exit 1 ;;
    *) echo "The database roles could not be checked (database not answering). Nothing restarted." >&2; exit 1 ;;
  esac
}

# Serialise manual deploys, rollback and maintenance against each other as well. A stale lock blocks safely;
# remove it only after checking that no restart is running (BETRIEB.md). One from before the server's last boot is
# the exception: no process of before the boot runs any more (a reboot during a deploy or the maintenance window
# left it, 2026-10-17), so it goes by itself.
mkdir -p "$HOME/askurious-env"
RESTART_LOCK="$HOME/askurious-env/restart.lock"
if [ -d "$RESTART_LOCK" ]; then
  booted=$(awk '/^btime /{print $2}' /proc/stat 2>/dev/null || true)
  locked=$(stat -c %Y "$RESTART_LOCK" 2>/dev/null || true)
  if [ -n "$booted" ] && [ -n "$locked" ] && [ "$locked" -lt "$booted" ]; then
    echo "restart.lock is from before the server's last boot: removed." >&2
    rmdir "$RESTART_LOCK" 2>/dev/null || true
  fi
fi
mkdir "$RESTART_LOCK" 2>/dev/null || { echo "Another restart is running or restart.lock needs checking. Nothing restarted." >&2; exit 1; }

# The maintenance page (wartung.sh, nginx/locations/wartung.conf; Ralf, 2026-10-22): on for the researchers' side
# while their one container restarts, for both sides while a new database image starts (maintenance window). What
# this script switched on, it switches off, also when it stops (the trap below, also on a signal: a lost SSH
# connection); wartung.sh's end time is the last net. A switch that fails stops nothing: without it a restarting
# container shows the same page (502 becomes 503 in nginx).
WARTUNG_ON=""
wartung_an() {
  if WARTUNG_STILL=1 ./wartung.sh an "$1" "$2" "$3" >/dev/null; then WARTUNG_ON="$WARTUNG_ON $1"
  else echo "    maintenance page not switched on ($1): going on without it" >&2; fi
}
wartung_aus() {
  WARTUNG_STILL=1 ./wartung.sh aus "$1" >/dev/null || echo "    maintenance page not switched off ($1): its end time does" >&2
  WARTUNG_ON=${WARTUNG_ON// $1/}
}
finish() {
  local side
  for side in $WARTUNG_ON; do WARTUNG_STILL=1 ./wartung.sh aus "$side" >/dev/null 2>&1 || true; done
  rmdir "$RESTART_LOCK" 2>/dev/null || true
}
trap finish EXIT
trap 'exit 130' INT TERM HUP
mkdir -p "$HOME/askurious-status/wartung"  # nginx mounts it (compose.yaml): created by us, not by Docker as root

[ "${ALL_AT_ONCE:-}" != 1 ] || { echo "ALL_AT_ONCE is no longer supported: participants must restart sequentially." >&2; exit 1; }
current=$($C exec -T db psql -U askurious -d askurious -Atc "SELECT version_num FROM alembic_version" 2>/dev/null || true)
current=$(echo "$current" | tr -d '[:space:]')
fresh=0
if [ -z "$($C ps -q participant-a participant-b 2>/dev/null || true)" ]; then fresh=1; fi
if [ -z "$current" ] && [ "$fresh" != 1 ]; then
  echo "Deploy refused: running participants but no database version to compare. Nothing restarted." >&2; exit 1
fi
check_migrations "$current"
# A new server (deploy-docker.sh NEW_ENV=1) is isolated from its first start: DB_ISOLATION=1, but no database, roles or
# isolation state yet. Its database starts as on a server from before the isolation (compose.yaml alone), so that
# isolation/transition.py adopts it the way it adopted askurious.de's; on the still empty database it gets its
# restricted roles (db-app-role.sh --new-install), and the deploy goes on as every isolated one: the migration job builds
# the schema as the owner role, the containers sign in with the runtime role. Never on a server with participants'
# containers, a database version or an isolation state (~/askurious-env/isolation-db-*.json).
if [ "$ISOLATION_ACTIVE" = 1 ] && [ "$fresh" = 1 ] && [ -z "$current" ] \
    && ! compgen -G "$HOME/askurious-env/isolation-db-*.json" >/dev/null; then
  echo "    new installation: the database and its restricted roles first"
  COMPOSE_FILE="$PWD/compose.yaml" $C up -d --no-deps --no-recreate --wait db
  bash ./db-app-role.sh --new-install  # by bash: the code of a public checkout may lack the executable bit
fi
if [ "$ISOLATION_ACTIVE" = 1 ]; then
  python3 isolation/transition.py preflight
  if $C config --services | grep -qx brand-researcher; then
    url=$(sed -n 's/^DATABASE_URL=//p' .env.brand | tail -n 1 | tr -d '\r')
    brand_db=${url##*/}; brand_db=${brand_db%%\?*}
    [[ "$brand_db" =~ ^[a-z][a-z0-9_]*$ ]] || { echo 'Invalid brand database.' >&2; exit 1; }
    brand_current=$($C exec -T db psql -U askurious -d "$brand_db" -Atc 'SELECT version_num FROM alembic_version')
    check_migrations "$brand_current"
  fi
  export COMPOSE_FILE="$TARGET_COMPOSE_FILE:$PWD/isolation/transition.yaml"
fi
if [ "$fresh" != 1 ] && ! $C exec -T nginx test -x /docker-entrypoint.d/06-participant-upstream.sh >/dev/null 2>&1; then
  echo "Deploy refused: nginx lacks sequential participant routing. Nothing restarted." >&2; exit 1
fi
if [ "${1:-}" != "--no-build" ]; then
  # Exact old public bytes, never uploads/env: old and new HTML coexist during the sequential restart.
  previous=nginx-image/previous-static
  mkdir -p "$previous"
  find "$previous" -type f ! -name .keep -delete
  if docker image inspect askurious:latest >/dev/null 2>&1; then
    docker run --rm --entrypoint tar askurious:latest -C /app/app/static -cf - . | tar xf - -C "$previous"
  fi
  before=$(docker image inspect -f '{{.Id}}' askurious:latest 2>/dev/null || true)
  $C build --quiet
  # the backup window's real flock (app/storage_guard.py, backup.sh) in the image just built, before anything
  # restarts: Windows skips tests/privacy/test_storage_guard.py, so no deploy ran it (code review 2026-10-15, L4).
  # Even with SKIP_TESTS=1 (a few seconds); no network, at most two minutes
  root=$(cd ../.. && pwd)
  if ! proof=$(docker run --rm --network none --entrypoint sh \
      -v "$root/tests/privacy/storage_guard_probe.py:/proof/proof.py:ro" \
      -v "$root/deploy/docker/backup.sh:/proof/backup.sh:ro" \
      -v "$root/deploy/docker/snapshot_uploads.py:/proof/snapshot_uploads.py:ro" \
      -v "$root/deploy/docker/compose-env.sh:/proof/compose-env.sh:ro" \
      askurious:latest -c 'set -e; d=$(mktemp -d); cp /proof/* /app/app/storage_guard.py /app/tools/storage_backup_lock.py "$d"/
        exec timeout 120 python "$d/proof.py"' 2>&1) \
      || [[ "$proof" != *'"bash_backup_protocol_passed": true'* ]]; then
    echo "$proof" >&2
    # the image from before stays askurious:latest: nothing that starts later picks up the untested one
    if [ -n "$before" ]; then docker tag "$before" askurious:latest; fi
    echo "Deploy refused: the backup window's lock (storage guard) fails in the new image. Nothing restarted; the containers run the image from before. The code folder is the new one: deploy-docker.sh rollback puts the old one back." >&2
    exit 1
  fi
  echo "    storage guard checked in the new image (real flock)"
fi

# Back up BEFORE network mutations, the migration job, or any application restart.
head=$(newest || true)
if [ -n "$current" ] && [ -n "$head" ] && { [ "$current" != "$head" ] || [ "$ISOLATION_ACTIVE" = 1 ]; } \
    && [ "${SKIP_BACKUP:-}" != 1 ]; then
  echo "    database dump first: the database is at $current, the code at $head"
  ./backup.sh before-deploy
fi
if [ "$ISOLATION_ACTIVE" = 1 ]; then python3 isolation/transition.py prepare; fi

# nginx must see the folder above. One that mounted its files from a code folder (before 2026-10-10) holds that
# folder, renamed by the next deploy and deleted by the one after: the reload of the rolling restart then fails (as
# on 2026-10-10). A mark written here and read inside nginx tells; if it differs, nginx is created anew first (a
# short gap for everyone, once)
mark="$(date +%s)-$RANDOM$RANDOM"
mkdir -p "$LIVE/locations"
echo "$mark" > "$LIVE/locations/.mount-check"
if [ -n "$($C ps -q nginx 2>/dev/null || true)" ] \
    && [ "$($C exec -T nginx cat /etc/nginx/locations/.mount-check 2>/dev/null | tr -d '[:space:]')" != "$mark" ]; then
  echo "    nginx created anew: it mounted its files from a folder other than $LIVE"
  sync_nginx
  $C up -d --no-deps --force-recreate nginx
  nginx_ready
  $C exec -T nginx sh -c "echo $(nginx_sum) > /tmp/askurious-files.sum"
fi

mkdir -p "$HOME/askurious-status"          # backup.sh's mark for the status page (compose.yaml, status)
if [ "$ISOLATION_ACTIVE" = 1 ]; then
  if [ "${ISOLATION_MAINTENANCE:-}" = 1 ]; then
    if db_recreated; then wartung_an alle 15 "neues Datenbank-Image"; fi
    $C up -d --no-deps --wait db  # backed-up maintenance window; old and new endpoints remain
    wartung_aus alle
  else
    $C up -d --no-deps --no-recreate --wait db
  fi
  $C run --rm --no-deps migrate || { ./alarm.sh 'Deploy stopped: migration job failed; participants unchanged' || true; exit 1; }
  if $C config --services | grep -qx brand-researcher; then
    $C run --rm --no-deps brand-migrate || { ./alarm.sh 'Deploy stopped: brand migration job failed; participants unchanged' || true; exit 1; }
  fi
  python3 isolation/transition.py preflight # new objects must also have the restricted runtime grants
elif [ "${ISOLATION_MAINTENANCE:-}" = 1 ]; then
  # the backed-up maintenance window: a new image of the database here only; the page while it starts
  if db_recreated; then wartung_an alle 15 "neues Datenbank-Image"; fi
  $C up -d --no-deps --wait db
  wartung_aus alle
else
  # a deploy never restarts the database for a new image or setting (CFG-05: its image line names a digest now);
  # created if missing, otherwise the next maintenance window (maintenance.sh, after its dump) takes it over
  $C up -d --no-deps --no-recreate db
fi
check_db_roles
# the researchers' side has one container: the maintenance page from its stop until it answers again (the trap
# switches it off if the deploy stops; then nginx shows the page only while the container does not answer)
wartung_an forschende 15 deploy
$C up -d --no-deps researcher compute status certbot
healthy researcher || { ./alarm.sh "Deploy stopped: researcher did not start (migration?)" || true; exit 1; }
wartung_aus forschende
# Every active service must run before isolation/transition.py finalize verifies its networks.
# WhisperX uses a separately prepared image and needs the healthy app for its pressure gate.
if $C config --services | grep -qx whisper; then
  $C up -d --no-deps --no-build --wait --wait-timeout 120 whisper
fi
if $C config --services | grep -qx housekeeping; then
  $C up -d --no-deps housekeeping
  housekeeping_ready housekeeping || { ./alarm.sh "Deploy stopped: housekeeping did not start" || true; exit 1; }
fi
start_brand() {
  # a further brand with its own database (profile "brand"): compose names its services only when the profile is on
  if $C config --services 2>/dev/null | grep -qx brand-researcher; then
    echo "    the brand with its own database"
    # its participants' container waits while its researchers' container migrates, when the code brings a migration
    # its database does not have yet (one container: no rolling there); the dump before it came from backup.sh above
    url=$(sed -n 's/^DATABASE_URL=//p' .env.brand 2>/dev/null | tail -n 1 | tr -d '\r' || true)
    brand_db=${url##*/}; brand_db=${brand_db%%\?*}
    [[ "$brand_db" =~ ^[A-Za-z0-9_]+$ ]] || brand_db=""
    brand_current=""
    if [ -n "$brand_db" ]; then
      brand_current=$($C exec -T db psql -U askurious -d "$brand_db" -Atc "SELECT version_num FROM alembic_version" 2>/dev/null || true)
      brand_current=$(echo "$brand_current" | tr -d '[:space:]')
    fi
    if [ -n "$brand_current" ] && [ "$brand_current" != "$head" ]; then $C stop brand-participant || true; fi
    $C up -d --no-deps brand-researcher brand-compute
    healthy brand-researcher || { ./alarm.sh "Deploy stopped: brand-researcher did not start (migration?)" || true; exit 1; }
    if $C config --services | grep -qx brand-housekeeping; then
      $C up -d --no-deps brand-housekeeping
      housekeeping_ready brand-housekeeping || { ./alarm.sh "Deploy stopped: brand-housekeeping did not start" || true; exit 1; }
    fi
    $C up -d --no-deps brand-participant
    healthy brand-participant || { ./alarm.sh "Deploy stopped: brand-participant did not start" || true; exit 1; }
  fi

}

# First activation: nginx's brand upstreams must resolve before nginx is recreated.
if $C config --services | grep -qx brand-researcher; then
  if [ -z "$($C ps -q brand-researcher)" ] || [ -z "$($C ps -q brand-participant)" ]; then start_brand; fi
fi
# Publish current + previous hashed assets before either participant starts emitting new asset links.
sync_nginx
want=$(nginx_sum)
before=$($C ps -q nginx 2>/dev/null || true)
$C up -d --no-deps nginx
if [ -n "$before" ] && [ "$($C ps -q nginx 2>/dev/null || true)" = "$before" ]; then
  have=$($C exec -T nginx cat /tmp/askurious-files.sum 2>/dev/null || true)
  if [ "$(echo "$have" | tr -d '[:space:]')" != "$want" ]; then $C restart nginx >/dev/null; fi
fi
nginx_ready
$C exec -T nginx sh -c "echo $want > /tmp/askurious-files.sum"

echo "    participants one after the other"
for p in $PARTICIPANTS; do
  if [ "$fresh" != 1 ]; then
    other=participant-a; [ "$p" != participant-a ] || other=participant-b
    healthy "$other" || { ./alarm.sh "Deploy stopped: $other is not healthy; $p stays running" || true; exit 1; }
    upstream "$p" down
    sleep 3
  fi
  $C up -d --no-deps "$p"
  healthy "$p" || { ./alarm.sh "Deploy stopped: $p did not start; the other participant stays running" || true; exit 1; }
  if [ "$ISOLATION_ACTIVE" = 1 ]; then
    $C exec -T nginx wget -q -T 5 -O /dev/null "http://$p:8000/healthz" \
      || { ./alarm.sh "Deploy stopped: nginx cannot reach $p; the other participant stays running" || true; exit 1; }
  fi
  if [ "$fresh" != 1 ]; then
    if [ "$p" = participant-a ]; then
      # Switch in one nginx reload: new offline receipt tokens must never reach the older second container.
      upstream "$p" up --handoff
    else
      upstream "$p" up
    fi
  fi
 done

start_brand

# nginx reads its configuration at its start; the sum of its files, kept in the container, tells whether they changed
# since. A container that compose recreates (its settings in compose.yaml changed) starts with the new files anyway.
if [ "$ISOLATION_ACTIVE" = 1 ]; then export COMPOSE_FILE="$TARGET_COMPOSE_FILE"; fi
sync_nginx
want=$(nginx_sum)
before=$($C ps -q nginx 2>/dev/null || true)
$C up -d --no-deps nginx
if [ "$($C ps -q nginx 2>/dev/null || true)" = "$before" ]; then
  have=$($C exec -T nginx cat /tmp/askurious-files.sum 2>/dev/null || true)
  if [ "$(echo "$have" | tr -d '[:space:]')" != "$want" ]; then
    echo "    nginx restarted (its configuration changed)"
    $C restart nginx >/dev/null
  fi
fi
nginx_ready
$C exec -T nginx sh -c "echo $want > /tmp/askurious-files.sum"
if [ "$ISOLATION_ACTIVE" = 1 ]; then python3 isolation/transition.py finalize; fi
# what compose.yaml no longer names (the one participant container before 2026-10-08) goes now, after nginx moved on
$C up -d --no-recreate --remove-orphans >/dev/null
# the deploy went through: its dump before the migrations encrypted now, with BACKUP_ENCRYPT_KEY (Ralf 2026-10-21); a
# deploy that stopped above keeps it readable for the way back, and the next regular backup encrypts it after a day
./backup.sh lock-before-deploy || ./alarm.sh "Deploy done, but the dump before it is not encrypted (backup.sh lock-before-deploy)" || true
