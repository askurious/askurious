import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    state: Path
    models: Path
    token: str
    threads: int = 4
    max_bytes: int = 400 * 1024 * 1024
    max_seconds: int = 10800  # per recording part; 60 minutes until 2026-11-05 (interview D-01 ran 102 minutes)
    max_pending: int = 8
    job_seconds: int = 21600
    queue_seconds: int = 86400
    result_seconds: int = 3600
    min_free_mb: int = 2048
    min_host_memory_mb: int = 4096
    max_host_load: float = 12.0
    app_health: str = ""
    cache_idle_seconds: int = 300
    cache_jobs: int = 8
    cache_memory_mb: int = 3072
    # parallel jobs (2026-11-24, VM with 27 vCPU and 56 GiB): every slot is its own thread with its own resident
    # process and model; any free slot takes the next queued job, so no slot waits for a kind of recording
    workers: int = 1
    # keep the large-v3-turbo weights loaded through alignment and diarization when the memory limit allows it
    keep_weights: bool = False

    @classmethod
    def environment(cls):
        return cls(
            Path(os.environ.get("WHISPER_STATE", "/state")),
            Path(os.environ.get("WHISPER_MODELS_DIR", "/models")),
            os.environ.get("WHISPER_TOKEN", ""),
            threads=int(os.environ.get("WHISPER_THREADS", "4")),
            max_seconds=int(os.environ.get("WHISPER_MAX_SECONDS", "10800")),
            max_host_load=float(os.environ.get("WHISPER_MAX_LOAD", "12")),
            app_health=os.environ.get("WHISPER_APP_HEALTH", ""),
            max_pending=int(os.environ.get("WHISPER_MAX_PENDING", "8")),
            cache_memory_mb=int(os.environ.get("WHISPER_CACHE_MB", "3072")),
            workers=int(os.environ.get("WHISPER_WORKERS", "1")),
            keep_weights=os.environ.get("WHISPER_KEEP_WEIGHTS", "").lower() in {"1", "true"},
        )


def options(fields):
    model = fields.get("model", "small")
    if model == "whisper-1":
        model = "small"
    if model not in {"small", "large-v3-turbo"}:
        raise ValueError("unsupported_model")
    language = fields.get("language", "de")
    if language not in {"de", "en"}:
        raise ValueError("unsupported_language")
    out = {"model": model, "language": language, "prompt": str(fields.get("prompt", ""))[:600]}
    for key in ("align", "diarize"):
        value = str(fields.get(key, "true")).lower()
        if value not in {"true", "false", "1", "0"}:
            raise ValueError("invalid_options")
        out[key] = value in {"true", "1"}
    return out
