# The base by digest (Abgleich 2026-10-15, T08-N-3), pinned 2026-10-15: the same base in every build of this image.
# maintenance.sh says in Slack when it is older than 60 days; then raise it (whisper/README.md, "Basis anheben").
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 OMP_NUM_THREADS=8 MKL_NUM_THREADS=8 \
    OPENBLAS_NUM_THREADS=8 NUMEXPR_NUM_THREADS=8 TOKENIZERS_PARALLELISM=false \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_XET=1 \
    HF_HUB_DISABLE_TELEMETRY=1 PYANNOTE_METRICS_ENABLED=0 \
    TORCH_HOME=/models/torch NLTK_DATA=/models/nltk HF_HOME=/tmp/huggingface HOME=/tmp TMPDIR=/state/tmp
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /service
COPY requirements-cpu.txt requirements.txt ./
# every package with its hash (--require-hashes), as the app's image does: a changed file on either index fails the
# build; torch's dependencies are in requirements.txt, so the first step needs none of its own (pip check proves it)
RUN pip install --require-hashes --no-deps --index-url https://download.pytorch.org/whl/cpu -r requirements-cpu.txt \
    && pip install --require-hashes --no-deps -r requirements.txt && pip check
COPY whisper_service ./whisper_service
# licence texts of the wheels that ship none (licenses/README.txt)
COPY licenses ./licenses
RUN useradd --system --uid 1000 --home /tmp whisper \
    && mkdir -p /state/tmp /models && chown -R whisper:whisper /state
USER whisper
EXPOSE 8091
CMD ["nice", "-n", "15", "python", "-m", "whisper_service"]
