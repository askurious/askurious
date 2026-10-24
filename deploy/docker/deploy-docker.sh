#!/usr/bin/env bash
# Deploy to the own server with Docker (compose.yaml): the committed state of HEAD goes up, the server builds the
# image and restarts the containers. Secrets live outside the code folder, in ~/askurious-env/.env on the server;
# the code folder only links to them, so a deploy can never lose or replace them (code audit 2026-10-08, B1).
#
# Usage: deploy/docker/deploy-docker.sh           checks and tests, then code up and containers rebuilt
#        deploy/docker/deploy-docker.sh rollback  back to the image and code folder from before the last deploy
#        deploy/docker/deploy-docker.sh rollback --no-start  the same, but nothing started: after a restore over a
#                                                 migration, reapply_erasures.py runs with that code first (BETRIEB.md)
#        deploy/docker/deploy-docker.sh cert      Let's Encrypt for both names (once DNS points here and 80 is open)
#        deploy/docker/deploy-docker.sh status    containers and the last lines of their logs
#        deploy/docker/deploy-docker.sh wartung an|aus|status [forschende|teilnahme|alle] [minutes]
#                                                 the maintenance page by hand (wartung.sh; 30 minutes, at most 240)
#        deploy/docker/deploy-docker.sh rename    once, on a server from before the rename to Askurious (2026-10-09):
#                                                 ~/queryous with database "queryous" becomes ~/askurious with
#                                                 database "askurious" (dump and restore, volumes copied); the old
#                                                 folders and volumes stay as the way back (BETRIEB.md)
#        deploy/docker/deploy-docker.sh rename-back  that way back: the old stack runs again (answers given since are lost)
# Participants always restart sequentially; incompatible migrations stop the deploy (restart.sh).
# SKIP_TESTS=1 skips the tests (not the storage guard's check in the new image, restart.sh), SKIP_OSV=1 the check
# for known vulnerabilities, ALLOW_UNMERGED=1 lets a HEAD go up that is not in origin/main (an exception, e.g. a
# hotfix). Each says so loudly and stands in app/deployed_at.txt (third line "skipped=..."), which Administration >
# Server shows until a deploy with all checks (audit 2026-10-13, SV-5/DP-9). Since the audit of 2026-10-18 (CFG-06)
# each of them needs SKIP_REASON="why" as well, which goes with the commit into ~/askurious-deploys.log on the server.
# The deploy asks no CI on GitHub (none runs for now, Ralf 2026-10-19): the local tests and OSV check decide.
# INSTANCE_OVERLAY_SRC names the folder with the installation's own files (its private repository; instance_overlay/
# README.md): its templates/ and protected_nets.txt go up with the code; the templates take the place of the code's
# templates of the same name, the networks stay closed to web services and typed AI endpoints. A
# server with the file ~/askurious-env/overlay-required refuses a deploy without it.
# MODULES_SRC names add-on modules (app/plugins.py) as local checkouts, separated by spaces: the committed state of each
# is built into a wheel here and goes up under modules/, where the Dockerfile installs it. Without it no module goes up.
# NEW_ENV=1 creates ~/askurious-env/.env with fresh secrets and the database's restricted roles from the start
# (DB_ISOLATION=1: owner role askurious_app for the migration job, runtime role askurious_run for the app,
# askurious_participant for the participants' containers; restart.sh creates them on the empty database,
# db-app-role.sh --new-install; the server needs python3 and Docker Compose 2.24.4 or newer, isolation/README.md):
# only on a new server; where any .env is left (also in ~/askurious.old) the deploy stops instead.
# DOCKER_HOST_SSH names the target, an SSH alias of ~/.ssh/config or user@host (required; the private repository of the
# installation sets it in its wrapper, instance_overlay/README.md), PYTHON the Python for tests and the vulnerability check, TEST_WORKERS the
# number of test workers (default 4).
# A deploy whose code brings migrations dumps the database first (restart.sh, backup.sh before-deploy); a failed
# container start says so in Slack (alarm.sh).
set -euo pipefail

HERE="$(cd "$(dirname "$0")/../.." && pwd)"
TARGET="${DOCKER_HOST_SSH:-}"       # SSH alias or user@host of the server
[[ -n "$TARGET" ]] || { echo "Set DOCKER_HOST_SSH to the SSH alias or user@host of the server." >&2; exit 1; }
DIR="askurious"                     # in the home directory of the user on the server
OLD="queryous"                     # the names before the rename to Askurious (2026-10-09): "rename" moves from them
ENV_DIR="askurious-env"             # .env and netcup.ini, next to it; deploys only link to them
RESEARCHER_HOST="${RESEARCHER_HOST:-askurious.de}"
PARTICIPANT_HOST="${PARTICIPANT_HOST:-ask.askurious.de}"
STATUS_HOST="${STATUS_HOST:-status.askurious.de}"
SSH=(ssh -o BatchMode=yes "$TARGET")
COMPOSE="cd ~/$DIR/deploy/docker && { [ ! -f ./compose-env.sh ] || . ./compose-env.sh; } && COMPOSE_BAKE=false docker compose"

# the server answers first: a missing alias or key otherwise showed up later as another fault ("still runs as
# ~/queryous"; deploy of 2026-10-13)
if ! reach=$(ssh -o BatchMode=yes -o ConnectTimeout=15 "$TARGET" true 2>&1); then
  echo "No SSH connection to $TARGET: ${reach:-no answer}" >&2
  if [[ "$TARGET" != *@* ]]; then
    echo "$TARGET is an SSH alias. Add it to ~/.ssh/config, e.g.
  Host $TARGET
    HostName <address of the server>
    User <login on the server>
or set DOCKER_HOST_SSH=user@host." >&2
  fi
  exit 1
fi

case "${1:-}" in
  status)
    "${SSH[@]}" "$COMPOSE ps; $COMPOSE logs --tail 15"
    exit 0 ;;
  wartung)
    # by hand (a restore, a longer piece of work): wartung.sh on the server; only words and numbers go there
    for a in "${2:-}" "${3:-}" "${4:-}"; do
      [[ "$a" =~ ^[a-z0-9]*$ ]] || { echo "wartung: only an|aus|status, forschende|teilnahme|alle and minutes" >&2; exit 2; }
    done
    case "${2:-}" in
      an) "${SSH[@]}" "bash ~/$DIR/deploy/docker/wartung.sh an ${3:-alle} ${4:-30} 'von Hand'" ;;
      aus) "${SSH[@]}" "bash ~/$DIR/deploy/docker/wartung.sh aus ${3:-alle} 'von Hand'" ;;
      status) "${SSH[@]}" "bash ~/$DIR/deploy/docker/wartung.sh status" ;;
      *) echo "usage: deploy-docker.sh wartung an|aus|status [forschende|teilnahme|alle] [minutes]" >&2; exit 2 ;;
    esac
    exit 0 ;;
  cert)
    # and the names of further brands (BRAND_*_HOSTS in the server's .env, app/brand.py)
    brands=$("${SSH[@]}" "sed -n 's/^BRAND_\(RESEARCHER\|PARTICIPANT\)_HOSTS=//p' ~/$ENV_DIR/.env" | tr -d '\r')
    # each brand's status page too: status.polltergeist.de next to polltergeist.de (nginx, the status server)
    statuses=$("${SSH[@]}" "sed -n 's/^BRAND_RESEARCHER_HOSTS=//p' ~/$ENV_DIR/.env" | tr -d '\r' | xargs -rn1 printf 'status.%s ')
    # www.<name> of Askurious and of each brand (only a redirect, nginx/servers.sh), unless WWW_REDIRECT=0 in the .env
    www=""
    if ! "${SSH[@]}" "grep -qE '^WWW_REDIRECT=0[[:space:]]*$' ~/$ENV_DIR/.env"; then
      www=$("${SSH[@]}" "sed -n 's/^BRAND_RESEARCHER_HOSTS=//p' ~/$ENV_DIR/.env" | tr -d '\r' | xargs -rn1 printf 'www.%s ')
      www="www.$RESEARCHER_HOST $www"
    fi
    for host in "$RESEARCHER_HOST" "$PARTICIPANT_HOST" "$STATUS_HOST" $brands $statuses $www; do
      "${SSH[@]}" "$COMPOSE run --rm --entrypoint certbot certbot certonly --webroot -w /var/www/acme \
        -d $host --cert-name $host --non-interactive --agree-tos --register-unsafely-without-email --keep-until-expiring"
    done
    "${SSH[@]}" "$COMPOSE restart nginx"
    exit 0 ;;
  rollback)
    case "${2:-}" in ""|--no-start) ;; *) echo "unknown: $2"; exit 2 ;; esac
    # the image of the deploy before (askurious:previous) with its code folder (~/askurious.old: compose.yaml, nginx);
    # both carry app/deployed_at.txt, and only a matching pair is put back (a second rollback stops here).
    # Not over a migration: the code from before does not know the database's version and would not start
    # (SchemaMismatch); the way back then is the dump restart.sh made before it (BETRIEB.md; audit 2026-10-09, BT3)
    "${SSH[@]}" "set -eu; cd ~
      [ -d $DIR.old ] || { echo 'No ~/$DIR.old: nothing to roll back to.' >&2; exit 1; }
      if [ -f $DIR/deploy/docker/compose-env.sh ]; then
        (cd $DIR/deploy/docker; . ./compose-env.sh
          if [ \"\$ISOLATION_ACTIVE\" = 1 ] && [ ! -f \"\$HOME/$DIR.old/app/schema_migrate.py\" ]; then
            echo 'Rollback refused: old image/code lacks isolated runtime schema support. Participants unchanged.' >&2; exit 1
          fi)
      fi
      cur=\$(cd $DIR/deploy/docker && docker compose exec -T db psql -U askurious -d askurious -Atc 'SELECT version_num FROM alembic_version' 2>/dev/null | tr -d '[:space:]' || true)
      if [ -n \"\$cur\" ] && ! grep -qsE \"^revision = .\$cur.\" $DIR.old/migrations/versions/*.py; then
        echo \"The database is at version \$cur, which the code from before does not know (a migration ran): it would not start. Nothing changed. Way back: the dump before the deploy (deploy/docker/BETRIEB.md, Rueckweg nach einer Migration).\" >&2; exit 1
      fi
      have=\$(docker run --rm --entrypoint cat askurious:previous /app/app/deployed_at.txt) \
        || { echo 'No image askurious:previous.' >&2; exit 1; }
      [ \"\$have\" = \"\$(cat $DIR.old/app/deployed_at.txt)\" ] \
        || { echo 'askurious:previous does not belong to ~/$DIR.old (rolled back already?): nothing changed.' >&2; exit 1; }
      mv $DIR $DIR.rollback && mv $DIR.old $DIR && mv $DIR.rollback $DIR.old
      # Roll back application/image, retain today's restart policy: historical scripts could stop both participants.
      cp $DIR.old/deploy/docker/restart.sh $DIR/deploy/docker/restart.sh
      if [ -f $DIR.old/deploy/docker/compose-env.sh ]; then
        cp $DIR.old/deploy/docker/compose-env.sh $DIR/deploy/docker/compose-env.sh
        cp -pR $DIR.old/deploy/docker/isolation $DIR/deploy/docker/
      fi
      cp $DIR.old/deploy/docker/nginx/participant-upstream.sh $DIR/deploy/docker/nginx/participant-upstream.sh
      docker tag askurious:previous askurious:latest
      if [ '${2:-}' = --no-start ]; then
        echo \"Rolled back to \$(sed -n 2p $DIR/app/deployed_at.txt), nothing started. Then: bash ~/$DIR/deploy/docker/restart.sh --no-build\"
        exit 0
      fi
      # the participants one by one (restart.sh); code from before it came restarts everything together
      if [ -f $DIR/deploy/docker/restart.sh ]; then bash $DIR/deploy/docker/restart.sh --no-build
      else $COMPOSE up -d --no-build --remove-orphans && $COMPOSE restart nginx >/dev/null; fi
      echo \"Rolled back to \$(sed -n 2p $DIR/app/deployed_at.txt)\""
    exit 0 ;;
  rename-back)
    # the stack from before the rename runs again on its own volumes and folders; whatever was answered since the
    # rename is only in the new database (dump it first if it matters: ./backup.sh in ~/$DIR/deploy/docker)
    "${SSH[@]}" "set -eu; cd ~
      [ -d $OLD/deploy/docker ] && docker volume inspect ${OLD}_pgdata >/dev/null 2>&1         || { echo 'No ~/$OLD or no volume ${OLD}_pgdata: nothing to go back to.' >&2; exit 1; }
      if [ -f $DIR/deploy/docker/compose-env.sh ]; then
        (cd $DIR/deploy/docker; . ./compose-env.sh; [ \"\$ISOLATION_ACTIVE\" != 1 ]) \
          || { echo 'rename-back refused on isolated roles/networks; use the documented restore path.' >&2; exit 1; }
      fi
      if [ -d $DIR/deploy/docker ]; then (cd $DIR/deploy/docker && docker compose down); fi
      (crontab -l 2>/dev/null | grep -v -e '$DIR/deploy/docker/' -e '$OLD/deploy/docker/'
       echo '30 2 * * * \$HOME/$OLD/deploy/docker/backup.sh >> \$HOME/$OLD-backup.log 2>&1'
       echo '30 4 * * 0 \$HOME/$OLD/deploy/docker/maintenance.sh >> \$HOME/$OLD-maintenance.log 2>&1') | crontab -
      cd $OLD/deploy/docker && COMPOSE_BAKE=false docker compose up -d --no-build && docker compose ps"
    echo "Back on ~/$OLD. ~/$DIR, ~/$ENV_DIR and the volumes ${DIR}_* stay; deploy again only after deleting them (BETRIEB.md)."
    exit 0 ;;
  rename) RENAME=1 ;;
  "") ;;
  *) echo "unknown: $1"; exit 2 ;;
esac

# the checks this deploy leaves out: a warning now and a line in app/deployed_at.txt (SV-5/DP-9)
SKIPPED=""
[[ "${SKIP_TESTS:-}" == "1" ]] && SKIPPED="${SKIPPED:+$SKIPPED,}tests"
[[ "${SKIP_OSV:-}" == "1" ]] && SKIPPED="${SKIPPED:+$SKIPPED,}osv"
[[ "${ALLOW_UNMERGED:-}" == "1" ]] && SKIPPED="${SKIPPED:+$SKIPPED,}unmerged"
if [[ -n "$SKIPPED" ]]; then
  # a reason for every exception, written down on the server with the commit (CFG-06)
  [[ -n "${SKIP_REASON:-}" ]] || { echo "This deploy would skip checks ($SKIPPED): say why with SKIP_REASON=\"...\" (BETRIEB.md)."; exit 1; }
  echo "WARNING: this deploy skips checks ($SKIPPED). It is noted in app/deployed_at.txt and shown on Administration > Server"
  echo "         until a deploy with all checks. Reason: $SKIP_REASON"
fi

if [[ -n "$(git -C "$HERE" status --porcelain -- app migrations config.py passenger_wsgi.py requirements.lock deploy tools)" ]]; then
  echo "Uncommitted changes: only the committed state of HEAD goes up. Commit first."; exit 1
fi
# only what is on main goes up (B12): a local branch or an unpushed commit stops here
if [[ "${ALLOW_UNMERGED:-}" != "1" ]]; then
  git -C "$HERE" fetch --quiet origin main || { echo "Could not fetch origin/main; ALLOW_UNMERGED=1 deploys anyway."; exit 1; }
  git -C "$HERE" merge-base --is-ancestor HEAD origin/main \
    || { echo "HEAD ($(git -C "$HERE" rev-parse --short HEAD)) is not in origin/main: merge it first (ALLOW_UNMERGED=1 for an exception)."; exit 1; }
  # and all of it: a HEAD behind origin/main would take back what others merged and deployed, their migrations
  # included, and the new code would not start (deploy of 2026-10-13)
  [[ "$(git -C "$HERE" rev-parse HEAD)" == "$(git -C "$HERE" rev-parse origin/main)" ]] \
    || { echo "HEAD ($(git -C "$HERE" rev-parse --short HEAD)) is behind origin/main ($(git -C "$HERE" rev-parse --short origin/main)): git pull first."; exit 1; }
fi
# what the server runs must be in HEAD, whoever deployed it and from where (rollback goes back on purpose)
running=$("${SSH[@]}" "sed -n 2p ~/$DIR/app/deployed_at.txt 2>/dev/null" | tr -d '[:space:]' || true)
if [[ -n "$running" && "${ALLOW_UNMERGED:-}" != "1" ]] && ! git -C "$HERE" merge-base --is-ancestor "$running" HEAD 2>/dev/null; then
  echo "The server runs $running, which HEAD ($(git -C "$HERE" rev-parse --short HEAD)) does not contain: git pull first," \
       "or use 'rollback' to go back on purpose (ALLOW_UNMERGED=1 for an exception)."; exit 1
fi
# the reason in plain words only, for the log on the server (it travels inside a remote command)
REASON=$(printf '%s' "${SKIP_REASON:-}" | tr -cd 'A-Za-z0-9 ._,:()-' | cut -c1-200)
# restart.sh --check-migrations runs here too, and needs bash 4 (macOS brings 3.2: brew install bash)
bash -c '[ "${BASH_VERSINFO[0]}" -ge 4 ]' \
  || { echo "The bash in PATH is $(bash -c 'echo $BASH_VERSION'); restart.sh needs bash 4 or newer (macOS: brew install bash)."; exit 1; }

# .venv/bin/python on Linux and macOS, .venv/Scripts/python.exe in a Windows checkout
PY="${PYTHON:-$HERE/.venv/bin/python}"
[[ -x "$PY" ]] || PY="$HERE/.venv/Scripts/python.exe"
need_python() { [[ -x "$PY" ]] || { echo "No Python in $HERE/.venv (bin/python or Scripts/python.exe); set PYTHON=..."; exit 1; }; }

echo "==> Running tests"
if [[ "${SKIP_TESTS:-}" != "1" ]]; then
  need_python
  # the same state of the code (git tree, no uncommitted or untracked files) already ran green with --full on this
  # machine: green_run.py says so and the run is skipped (tests/helpers/green_runs.py; test suite concept 2026-10-18,
  # F). FULL_TESTS=1 runs them anyway.
  if [[ "${FULL_TESTS:-}" != "1" ]] && "$PY" "$HERE/tools/green_run.py" "$HERE"; then
    echo "    not run again (FULL_TESTS=1 runs them)"
  else
    # --full: also the slow tests and those of the deploy scripts (restart.sh, the rolling restart); without it they
    # ran only when a deploy file differed from origin/main, which this script never lets through (code review
    # 2026-10-15, L1). Four workers by default, as on the shared machine (CLAUDE.md); TEST_WORKERS=0: one process
    "$PY" -m pytest -q -p no:warnings -n "${TEST_WORKERS:-4}" --full "$HERE/tests" \
      || { echo "Tests failed, not deploying."; exit 1; }
  fi
fi

if [[ "${SKIP_OSV:-}" != "1" ]]; then
  echo "==> Checking the locked packages for known vulnerabilities (OSV)"
  need_python
  "$PY" "$HERE/tools/check_vulnerabilities.py" "$HERE/requirements.lock" "$HERE/tools/vendor_versions.txt" \
    "$HERE/deploy/docker/requirements-docker.lock" "$HERE/deploy/docker/requirements-static.lock" \
    "$HERE/requirements-dev.txt" \
    || { echo "Known vulnerabilities: raise the versions, run tools/lock_requirements.py, deploy again."; exit 1; }
fi

COMMIT="$(git -C "$HERE" rev-parse --short=8 HEAD)"

if [[ "${RENAME:-}" == 1 ]]; then
  echo "==> Rename: ~/$OLD -> ~/$DIR. From here participants see an error page until the end (a few minutes)."
  # nothing is deleted: the old folders, volumes and image stay as the way back (rename-back)
  "${SSH[@]}" "set -eu; cd ~; umask 077
    [ -d $OLD/deploy/docker ] || { echo 'No ~/$OLD/deploy/docker: nothing to rename.' >&2; exit 1; }
    for p in $DIR $ENV_DIR $DIR-status; do [ ! -e \$p ] || { echo \"~/\$p exists already (renamed before?): nothing changed.\" >&2; exit 1; }; done
    for v in pgdata instance certs acme status; do
      ! docker volume inspect ${DIR}_\$v >/dev/null 2>&1 || { echo \"Volume ${DIR}_\$v exists already: nothing changed.\" >&2; exit 1; }
    done
    cd $OLD/deploy/docker
    # no new answers from here on; nginx stays and shows its error page
    docker compose stop participant-a participant-b researcher compute
    docker compose exec -T db psql -U $OLD -d $OLD -AtX -v ON_ERROR_STOP=1 -c \"$(cat <<'SQL'
SELECT table_name || '=' || (xpath('/row/c/text()', query_to_xml(format('SELECT count(*) AS c FROM %I', table_name), false, true, '')))[1]::text
FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name
SQL
)\" > ~/rename-counts-before.txt
    mkdir -p ~/backups; dump=~/backups/rename-$OLD-\$(date -u +%Y%m%d-%H%M%S).dump
    docker compose exec -T db pg_dump -U $OLD -Fc $OLD > \$dump.part && mv \$dump.part \$dump && chmod 600 \$dump
    ln -sfn \$dump ~/rename.dump
    echo \"    dump \$dump (\$(du -h \$dump | cut -f1)), \$(wc -l < ~/rename-counts-before.txt) tables counted\"
    docker compose down
    (crontab -l 2>/dev/null | grep -v '$OLD/deploy/docker/') | crontab - || true
    cd ~; cp -a $OLD-env $ENV_DIR
    sed -i 's/^DB_APP_USER=${OLD}_app\$/DB_APP_USER=${DIR}_app/' $ENV_DIR/.env
    if [ -d $OLD-status ]; then cp -a $OLD-status $DIR-status; fi
    docker tag $OLD:latest $DIR:latest"
else
  # a server still running under the old names must be renamed once first: a plain deploy would start an empty
  # database next to the old one
  "${SSH[@]}" "[ -d ~/$DIR ] || [ ! -d ~/$OLD/deploy/docker ]" \
    || { echo "The server still runs as ~/$OLD: rename it once with 'deploy-docker.sh rename' (BETRIEB.md)."; exit 1; }
fi
# a server whose installation brings its own legal texts (the file ~/askurious-env/overlay-required) gets no deploy
# without them: the public code's neutral imprint and privacy texts would take their place
if [[ -z "${INSTANCE_OVERLAY_SRC:-}" ]] && "${SSH[@]}" "test -f ~/$ENV_DIR/overlay-required"; then
  echo "$TARGET needs the installation's own files: set INSTANCE_OVERLAY_SRC to the overlay folder of its private repository (instance_overlay/README.md)." >&2
  exit 1
fi
echo "==> Uploading $COMMIT to $TARGET:~/$DIR"
# Refuse incompatible migrations before changing the live code folder. No force switch overrides this policy.
[ "${ALL_AT_ONCE:-}" != 1 ] || { echo "ALL_AT_ONCE is no longer supported." >&2; exit 1; }
current=$("${SSH[@]}" "$COMPOSE exec -T db psql -U askurious -d askurious -Atc 'SELECT version_num FROM alembic_version'" 2>/dev/null || true)
current=$(echo "$current" | tr -d '[:space:]')
bash "$HERE/deploy/docker/restart.sh" --check-migrations "$current"

# the archive goes up whole and checked (SHA-256); the live folder is only swapped for a complete one
ARCHIVE="$(mktemp)"
trap 'rm -f "$ARCHIVE"' EXIT
# tools/check_vulnerabilities.py with its list of JavaScript libraries: the weekly check of maintenance.sh (DP-10);
# tests/privacy/storage_guard_probe.py: restart.sh runs it in the new image (the real flock, code review 2026-10-15, L4)
# modules/ (its README; the wheels of MODULES_SRC come below) where the checkout has it
MODULES_DIR=(); ! git -C "$HERE" ls-files --error-unmatch modules/README.md >/dev/null 2>&1 || MODULES_DIR=(modules)
git -C "$HERE" archive --format=tar HEAD .dockerignore config.py passenger_wsgi.py licence.md log.md requirements.lock requirements-postgres.txt \
    app migrations deploy/docker tools/check_vulnerabilities.py tools/vendor_versions.txt tools/build_static.py tools/storage_backup_lock.py \
    tests/privacy/storage_guard_probe.py instance_overlay "${MODULES_DIR[@]}" > "$ARCHIVE"
if [[ -n "${INSTANCE_OVERLAY_SRC:-}" ]]; then
  [ -d "$INSTANCE_OVERLAY_SRC/templates" ] || { echo "INSTANCE_OVERLAY_SRC ($INSTANCE_OVERLAY_SRC) has no folder templates/." >&2; exit 1; }
  # from inside the folder: a Windows path such as C:\... would read to tar as host:path
  # templates/ and protected_nets.txt (instance_overlay/README.md)
  parts=(templates); [ ! -f "$INSTANCE_OVERLAY_SRC/protected_nets.txt" ] || parts+=(protected_nets.txt)
  [ ! -f "$INSTANCE_OVERLAY_SRC/privacy_concept.json" ] || parts+=(privacy_concept.json)  # app/privacy_concept.py overlay()
  [ ! -f "$INSTANCE_OVERLAY_SRC/instruments.json" ] || parts+=(instruments.json)  # app/constructs.py INSTRUMENTS
  (cd "$INSTANCE_OVERLAY_SRC" && tar rf "$ARCHIVE" --transform 's,^,instance_overlay/,' "${parts[@]}")
  echo "    with the installation's files from $INSTANCE_OVERLAY_SRC: ${parts[*]}"
fi
if [[ -n "${MODULES_SRC:-}" ]]; then
  need_python
  WHEELS="$(mktemp -d)"
  for mod in $MODULES_SRC; do
    [ -f "$mod/pyproject.toml" ] || { echo "MODULES_SRC: $mod has no pyproject.toml." >&2; exit 1; }
    [ -z "$(git -C "$mod" status --porcelain)" ] || { echo "MODULES_SRC: $mod has uncommitted changes." >&2; exit 1; }
    SRC="$(mktemp -d)"; git -C "$mod" archive --format=tar HEAD | tar xf - -C "$SRC"  # the committed state only
    "$PY" -m pip wheel --quiet --no-deps --wheel-dir "$WHEELS" "$SRC" || { echo "MODULES_SRC: $mod did not build." >&2; exit 1; }
    rm -rf "$SRC"
    echo "    with the add-on module $(basename "$mod") at $(git -C "$mod" rev-parse --short HEAD)"
  done
  # installed like every other package: only with the hash of exactly this wheel (--require-hashes)
  (cd "$WHEELS" && for w in *.whl; do echo "./modules/$w --hash=sha256:$(sha256sum "$w" | cut -d' ' -f1)"; done > requirements.txt      && tar rf "$ARCHIVE" --transform 's,^,modules/,' *.whl requirements.txt)
  rm -rf "$WHEELS"
fi
SUM="$(sha256sum "$ARCHIVE" | cut -d' ' -f1)"
"${SSH[@]}" "set -eu; cd ~
  rm -rf $DIR.new $DIR.new.tar; mkdir -p $DIR.new
  cat > $DIR.new.tar
  echo '$SUM  $DIR.new.tar' | sha256sum -c --quiet - || { echo 'Upload damaged (checksum): live code untouched.' >&2; exit 1; }
  tar xf $DIR.new.tar -C $DIR.new; rm -f $DIR.new.tar
  [ -f $DIR.new/deploy/docker/compose.yaml ] || { echo 'Upload incomplete (no compose.yaml): live code untouched.' >&2; exit 1; }
  { date -u +%Y-%m-%dT%H:%M:%SZ; echo $COMMIT; [ -z '$SKIPPED' ] || echo 'skipped=$SKIPPED'; } > $DIR.new/app/deployed_at.txt
  [ -z '$SKIPPED' ] || echo \"\$(date -u +%Y-%m-%dT%H:%M:%SZ) $COMMIT skipped=$SKIPPED reason=$REASON\" >> ~/askurious-deploys.log" < "$ARCHIVE"

echo "==> .env in ~/$ENV_DIR, then the new code folder goes live"
"${SSH[@]}" "set -eu; cd ~; umask 077; mkdir -p $ENV_DIR
  # once, on a server from before: the files that lived in the code folder move to ~/$ENV_DIR (copied; the old
  # folder keeps its copy until the next deploy)
  for f in $DIR/deploy/docker/.env* $DIR/deploy/docker/netcup.ini; do
    { [ -f \"\$f\" ] && [ ! -L \"\$f\" ]; } || continue
    t=$ENV_DIR/\$(basename \"\$f\")
    if [ ! -e \"\$t\" ]; then cp -p \"\$f\" \"\$t\"; echo \"    \$f copied to \$t\"
    elif ! cmp -s \"\$f\" \"\$t\"; then echo \"\$f differs from ~/\$t: keep one of them by hand, then deploy again.\" >&2; exit 1; fi
  done
  if [ ! -f $ENV_DIR/.env ]; then
    for f in $DIR/deploy/docker/.env $DIR.old/deploy/docker/.env; do
      if [ -e \"\$f\" ] || [ -L \"\$f\" ]; then
        echo \"No ~/$ENV_DIR/.env, but there is ~/\$f: check it, copy it to ~/$ENV_DIR/.env (chmod 600) and deploy again. No new secrets created.\" >&2; exit 1
      fi
    done
    if [ '${NEW_ENV:-}' != 1 ]; then
      echo 'No .env on the server (~/$ENV_DIR/.env). Only on a new server: NEW_ENV=1 deploy/docker/deploy-docker.sh creates it with fresh secrets.' >&2; exit 1
    fi
    cat > $ENV_DIR/.env <<EOF
SECRET_KEY=\$(openssl rand -hex 32)
TOKEN_KEY=\$(openssl rand -hex 32)
RESEARCHER_SESSION_KEY=\$(openssl rand -hex 32)
POSTGRES_PASSWORD=\$(openssl rand -hex 24)
DB_ISOLATION=1
COMPOSE_FILE=compose.yaml:isolation/roles.yaml:isolation/networks.yaml
DB_APP_USER=askurious_app
DB_APP_PASSWORD=\$(openssl rand -hex 24)
DB_RUN_USER=askurious_run
DB_RUN_PASSWORD=\$(openssl rand -hex 24)
PARTICIPANT_DB_USER=askurious_participant
PARTICIPANT_DB_PASSWORD=\$(openssl rand -hex 24)
PUBLIC_BASE_URL=https://$RESEARCHER_HOST
RESEARCHER_HOST=$RESEARCHER_HOST
PARTICIPANT_HOST=$PARTICIPANT_HOST
EOF
    echo '    created with fresh secrets and restricted database roles (DB_ISOLATION=1; restart.sh sets them up)'
  fi
  grep -q '^COMPUTE_SECRET=.' $ENV_DIR/.env || { echo COMPUTE_SECRET=\$(openssl rand -hex 32) >> $ENV_DIR/.env; echo '    COMPUTE_SECRET added'; }
  # the box the participants' containers open (app/secrets_box.py; audit 2026-10-22, E-01): safe to add, TOKEN_KEY
  # still opens it; once the new code runs for good, python -m app.key_rotation locks the survey keys with it (BETRIEB.md)
  grep -q '^PARTICIPANT_TOKEN_KEY=.' $ENV_DIR/.env || { echo PARTICIPANT_TOKEN_KEY=\$(openssl rand -hex 32) >> $ENV_DIR/.env; echo '    PARTICIPANT_TOKEN_KEY added'; }
  for f in $ENV_DIR/.env* $ENV_DIR/netcup.ini; do [ -f \"\$f\" ] && ln -sfn \"\$HOME/\$f\" $DIR.new/deploy/docker/; done
  # nginx mounts its files from there (compose.yaml, nginx-live): a folder no deploy moves or deletes, restart.sh fills it
  # (filled here only the first time, so that compose never meets it empty and makes folders of the files it mounts)
  (umask 022; mkdir -p $ENV_DIR/nginx; [ -e $ENV_DIR/nginx/askurious.conf.template ] || cp -pR $DIR.new/deploy/docker/nginx/. $ENV_DIR/nginx/)
  ln -sfn \"\$HOME/$ENV_DIR/nginx\" $DIR.new/deploy/docker/nginx-live
  rm -rf $DIR.old; if [ -d $DIR ]; then mv $DIR $DIR.old; fi; mv $DIR.new $DIR"

echo "==> Building and starting the containers"
# only the code: base images (python, postgres, nginx) stay as they are and are updated in the maintenance window
# (maintenance.sh, Sunday night), never by a deploy
# the running image stays as askurious:previous for "deploy-docker.sh rollback" (prune removes only untagged ones).
# restart.sh: the researchers' side first (migrations), then the two participants' containers one after the other,
# so that participants see no error page; nginx only when its files changed
if [[ "${RENAME:-}" == 1 ]]; then
  # the new stack's volumes (created by compose, so they carry its labels) get the old ones' files; the database
  # comes from the dump, without owners and grants of the old roles, then the app role owns it again
  "${SSH[@]}" "set -eu; cd ~/$DIR/deploy/docker; export COMPOSE_BAKE=false
    docker compose build --quiet
    docker compose create --no-recreate
    for v in instance certs acme status; do
      docker run --rm --user root --entrypoint sh -v ${OLD}_\$v:/from:ro -v ${DIR}_\$v:/to $DIR:latest -c 'cp -a /from/. /to/'
    done
    docker compose up -d --wait db
    docker compose exec -T db pg_restore -U $DIR -d $DIR --no-owner --no-privileges --exit-on-error < ~/rename.dump
    if grep -q '^DB_APP_USER=${DIR}_app\$' .env; then ./db-app-role.sh; fi
    docker compose exec -T db psql -U $DIR -d $DIR -AtX -v ON_ERROR_STOP=1 -c \"$(cat <<'SQL'
SELECT table_name || '=' || (xpath('/row/c/text()', query_to_xml(format('SELECT count(*) AS c FROM %I', table_name), false, true, '')))[1]::text
FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name
SQL
)\" > ~/rename-counts-after.txt
    if ! cmp -s ~/rename-counts-before.txt ~/rename-counts-after.txt; then
      diff ~/rename-counts-before.txt ~/rename-counts-after.txt >&2 || true
      docker compose stop db
      echo 'The restored database differs from the old one (above): stopped. Way back: deploy-docker.sh rename-back.' >&2; exit 1
    fi
    echo \"    restored: \$(wc -l < ~/rename-counts-after.txt) tables, every count equal\""
fi
"${SSH[@]}" "set -e; if docker image inspect askurious:latest >/dev/null 2>&1; then docker tag askurious:latest askurious:previous; fi
  FORCE_ROLLING='${FORCE_ROLLING:-}' ALL_AT_ONCE='${ALL_AT_ONCE:-}' bash ~/$DIR/deploy/docker/restart.sh && docker image prune -f >/dev/null"

echo "==> Crontab: database dump and uploads every four hours from 02:30 (backup.sh), maintenance window Sunday 04:30 (maintenance.sh),"
echo "    the containers' load every minute (container-stats.sh; when it stops, Administration > Server says so),"
echo "    after each boot a message to Slack on which containers came back (after-boot.sh),"
echo "    the maintenance page off once its end time has passed, every minute (wartung.sh pruefen)"
"${SSH[@]}" "(crontab -l 2>/dev/null | grep -v -e 'askurious/deploy/docker/' -e '$OLD/deploy/docker/'; \
  echo '30 2-22/4 * * * \$HOME/askurious/deploy/docker/backup.sh >> \$HOME/askurious-backup.log 2>&1'; \
  echo '* * * * * \$HOME/askurious/deploy/docker/container-stats.sh > /dev/null 2>&1'; \
  echo '* * * * * \$HOME/askurious/deploy/docker/wartung.sh pruefen >> \$HOME/askurious-maintenance.log 2>&1'; \
  echo '30 4 * * 0 \$HOME/askurious/deploy/docker/maintenance.sh >> \$HOME/askurious-maintenance.log 2>&1'; \
  echo '@reboot \$HOME/askurious/deploy/docker/after-boot.sh >> \$HOME/askurious-boot.log 2>&1') | crontab -"
"${SSH[@]}" "$COMPOSE ps"
echo "==> Done."
