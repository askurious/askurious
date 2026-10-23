# nginx for compose.yaml: the stable branch with a fixed patch release (audit 2026-10-09, BT1; raised here by hand,
# BETRIEB.md "Images anheben") plus openssl, which nginx/self-signed.sh needs for a name that has no certificate yet.
# Built into the image, not installed at run time (audit 2026-10-10, B7): the container stays as built and starts
# without the network. maintenance.sh rebuilds it each week on the newest base image (build --pull).
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 AS assets
WORKDIR /build
COPY deploy/docker/requirements-static.lock ./requirements-static.lock
RUN pip install --require-hashes --no-deps -r requirements-static.lock
COPY tools/build_static.py ./build_static.py
COPY app/static /srv/static
COPY deploy/docker/nginx-image/previous-static /srv/previous/static
RUN python build_static.py /srv/static --previous /srv/previous/static

FROM nginx:1.30.5-alpine@sha256:0985e772fb9f729e6fa0980da05fca5d9c468e870eed43071545afa9d2e27d94
RUN apk add --no-cache openssl

COPY --from=assets /srv /srv
# the maintenance page (nginx/locations/wartung.conf)
COPY deploy/docker/nginx-image/wartung /srv/wartung
