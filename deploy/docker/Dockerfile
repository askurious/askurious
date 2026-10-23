# Askurious as a container: the same code for the researchers' side and the participants' side (compose.yaml
# chooses by environment and command). Exact versions and hashes from requirements.lock (psycopg included),
# and for gunicorn and its packaging from requirements-docker.lock (code audit 2026-10-08, B11).
# the base pinned by digest (audit 2026-10-18, CFG-05): raised with tools/image_digests.py --update and a PR
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
# numpy's and scipy's OpenBLAS otherwise starts one thread per core of the server (24) in every gunicorn process, and
# a single request with matrix work spread over ten cores of one participants' container while the other stayed idle
# (Administration > Server, peak 10.5 cores, 2026-10-09). The processes are the parallelism: 12 workers per container,
# 6 estimations on the Rechenknecht. Whisper (CTranslate2) keeps its own threads (OMP_NUM_THREADS stays unset).
ENV OPENBLAS_NUM_THREADS=1
# the daily dumps of backup.sh on this server, named on the privacy page (audit 2026-10-10, L-12)
ENV BACKUP_DAYS=14
WORKDIR /app

COPY requirements.lock deploy/docker/requirements-docker.lock ./
RUN pip install --require-hashes --no-deps -r requirements.lock -r requirements-docker.lock

# log.md: the source of "Entwurf aus den Neuerungen" on the news page (app/blog.py)
COPY config.py passenger_wsgi.py licence.md log.md ./
COPY app ./app
COPY migrations ./migrations
# the installation's own files over the code (instance_overlay/README.md); only the README in the public code
COPY instance_overlay ./instance_overlay
# add-on modules (app/plugins.py) as wheels with their hashes in modules/requirements.txt, put there by
# deploy-docker.sh (MODULES_SRC); in the code the folder holds only its README
COPY modules ./modules
RUN [ ! -f modules/requirements.txt ] || pip install --require-hashes --no-deps -r modules/requirements.txt
COPY deploy/docker/requirements-static.lock /tmp/requirements-static.lock
COPY tools/build_static.py /tmp/build_static.py
COPY tools/storage_backup_lock.py /app/tools/storage_backup_lock.py
RUN pip install --require-hashes --no-deps --target /tmp/static-tools -r /tmp/requirements-static.lock \
    && PYTHONPATH=/tmp/static-tools python /tmp/build_static.py app/static \
    && rm -rf /tmp/static-tools /tmp/requirements-static.lock /tmp/build_static.py
RUN useradd --system --uid 1000 --home /app askurious && mkdir -p instance/uploads /status && chown -R askurious instance /status
USER askurious

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--worker-class", "gthread", "--workers", "4", "--threads", "8", "--timeout", "120", "--graceful-timeout", "30", "passenger_wsgi:application"]
