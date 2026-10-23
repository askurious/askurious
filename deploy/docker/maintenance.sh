#!/usr/bin/env bash
# Maintenance window (crontab of the server user, Sunday 04:30; deploy-docker.sh puts it there): the base images as
# pinned by digest (python 3.12, postgres 17.x, nginx 1.30.x; audit 2026-10-18, CFG-05), images rebuilt, containers
# restarted (restart.sh; a new postgres or nginx image restarts that container, a short gap at this hour). A pinned
# image gets its security fixes when its digest is raised (tools/image_digests.py --update, a PR, a deploy): this
# window says in Slack when the registry has a newer state of a pinned tag;
# certbot keeps the version named in compose.yaml (raised by hand). A database dump comes first, and a failed one
# (backup.sh, exit 1) stops the window. Deploys never do this; Ubuntu's own updates run in the same night (03:00).
# A window that fails says so in the operators' Slack channel (alarm.sh; audit 2026-10-09, BT6), and so do known
# vulnerabilities in the packages (audit 2026-10-13, DP-10).
set -euo pipefail
cd "$(dirname "$0")"
. ./compose-env.sh
trap 'rc=$?; [ "$rc" = 0 ] || ./alarm.sh "maintenance.sh failed (exit $rc): images not updated, see ~/askurious-maintenance.log" || true' EXIT
echo "== $(date -u +%Y-%m-%dT%H:%M:%SZ) maintenance"
./backup.sh
docker compose pull --ignore-buildable --quiet
# a fixed tag that Docker no longer rebuilds (a newer patch release or branch came out) brings no fixes any more:
# say so when the nginx image is older than 45 days (audit 2026-10-09, BT1; raise the tag in nginx-image/Dockerfile)
# (the base of the own nginx image, nginx-image/Dockerfile; build --pull below fetches its newest state)
nginx_image=$(sed -n 's/^FROM \(nginx:[^ ]*\).*/\1/p' nginx-image/Dockerfile | head -n 1 || true)
if [ -n "$nginx_image" ]; then
  docker pull --quiet "$nginx_image" >/dev/null || true   # the base itself, for its age (the build below uses it)
  created=$(docker image inspect --format '{{.Created}}' "$nginx_image" 2>/dev/null || true)
  age=$(( ( $(date +%s) - $(date -d "${created:-now}" +%s 2>/dev/null || date +%s) ) / 86400 ))
  [ "$age" -le 45 ] || ./alarm.sh "$nginx_image was built $age days ago: is there a newer release? (BETRIEB.md, Images anheben)"
fi
# the WhisperX image (whisper/Dockerfile) is built by hand and validated with its models (whisper/README.md), never
# here; its base is pinned by digest, so it gets no fixes until the digest is raised: say so after 60 days
# (Abgleich 2026-10-15, T08-N-3)
whisper_base=$(sed -n 's/^FROM \([^ ]*@sha256:[0-9a-f]*\).*/\1/p' whisper/Dockerfile | head -n 1 || true)
if [ -n "$whisper_base" ]; then
  docker pull --quiet "$whisper_base" >/dev/null || true
  created=$(docker image inspect --format '{{.Created}}' "$whisper_base" 2>/dev/null || true)
  age=$(( ( $(date +%s) - $(date -d "${created:-now}" +%s 2>/dev/null || date +%s) ) / 86400 ))
  [ "$age" -le 60 ] || ./alarm.sh "The base of the WhisperX image was built $age days ago: raise its digest in whisper/Dockerfile and rebuild (whisper/README.md)"
fi
docker compose build --pull --quiet
SKIP_BACKUP=1 ISOLATION_MAINTENANCE=1 ./restart.sh --no-build # DB image changes only in this backed-up window
docker image prune -f >/dev/null
docker compose ps --format '{{.Service}} {{.Status}}'
# which base images run from now on, by digest (audit 2026-10-13, DP-11): their tags move with each security fix,
# the digests in this log say what ran when
for image in $(docker compose config --images 2>/dev/null | sort -u) $(sed -n 's/^FROM \([^ ]*\).*/\1/p' Dockerfile nginx-image/Dockerfile whisper/Dockerfile); do
  echo "image $image $(docker image inspect --format '{{join .RepoDigests " "}}' "$image" 2>/dev/null || echo '(local)')"
done
# a newer state of a pinned base image (CFG-05): asked at the registry (Docker Hub's public API, nothing pulled) by
# tools/image_digests.py in the app's image, the code folder read-only; a moved tag says so in Slack and stops nothing
if [ -f ../../tools/image_digests.py ]; then
  if ! out=$(docker run --rm --read-only --cap-drop ALL --security-opt no-new-privileges:true \
      -v "$(cd ../.. && pwd)":/src:ro --entrypoint python askurious:latest /src/tools/image_digests.py 2>&1); then
    echo "$out"
    ./alarm.sh "Pinned base images have a newer state (or the registry was not reached): $(echo "$out" | tail -n 1). Raise them: tools/image_digests.py --update, PR, deploy (BETRIEB.md, Images anheben)" || true
  else
    echo "$out" | tail -n 1
  fi
fi
# known vulnerabilities in the locked Python packages and the shipped JavaScript libraries, also between deploys
# (DP-10): the check of deploy-docker.sh, in the app's image (it has Python), the code folder read-only. A finding
# says so in Slack and stops nothing; the fix is a deploy with raised versions (tools/lock_requirements.py)
if [ -f ../../tools/check_vulnerabilities.py ]; then
  if ! out=$(docker run --rm --read-only --cap-drop ALL --security-opt no-new-privileges:true \
      -v "$(cd ../.. && pwd)":/src:ro --entrypoint python askurious:latest /src/tools/check_vulnerabilities.py \
      /src/requirements.lock /src/tools/vendor_versions.txt /src/deploy/docker/requirements-docker.lock /src/deploy/docker/requirements-static.lock 2>&1); then
    echo "$out"
    ./alarm.sh "Check for known vulnerabilities (maintenance.sh) found some or failed: $(echo "$out" | tail -n 1). See ~/askurious-maintenance.log" || true
  else
    echo "$out" | tail -n 1
  fi
  # the WhisperX image's packages (also while it is off: the image stays on the server); the ids judged in
  # whisper/README.md are listed in whisper/osv-accepted.txt and alarm no one, a new one does (T08-N-3)
  if ! out=$(docker run --rm --read-only --cap-drop ALL --security-opt no-new-privileges:true \
      -v "$(cd ../.. && pwd)":/src:ro --entrypoint python askurious:latest /src/tools/check_vulnerabilities.py \
      /src/deploy/docker/whisper/requirements.txt /src/deploy/docker/whisper/requirements-cpu.txt \
      --accepted /src/deploy/docker/whisper/osv-accepted.txt 2>&1); then
    echo "$out"
    ./alarm.sh "Check for known vulnerabilities of the WhisperX image (maintenance.sh) found new ones or failed: $(echo "$out" | tail -n 1). See ~/askurious-maintenance.log and whisper/README.md" || true
  else
    echo "$out" | tail -n 1
  fi
fi
