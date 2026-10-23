import fcntl
import json
import os
import threading

from waitress import serve

from .api import create_app
from .queue import Queue
from .settings import Settings
from .worker import run


def main():
    os.umask(0o077)
    settings = Settings.environment()
    if len(settings.token) < 32 or not 1 <= settings.threads <= 8:
        raise SystemExit("WHISPER_TOKEN must have at least 32 characters; WHISPER_THREADS must be 1..8.")
    manifest = settings.models / "manifest.json"
    if not manifest.is_file():
        raise SystemExit("Prepare the local model volume before starting the service.")
    data = json.loads(manifest.read_text())
    if not all((settings.models / data["models"][name] / "model.bin").is_file() for name in ("small", "large-v3-turbo")):
        raise SystemExit("The local model volume is incomplete.")
    required = ("pyannote/config-runtime.yaml", "pyannote/segmentation/pytorch_model.bin",
                "pyannote/embedding/pytorch_model.bin", "pyannote/plda/plda.npz", "pyannote/plda/xvec_transform.npz")
    if not all((settings.models / name).is_file() for name in required) or not (settings.models / data["vad"]).is_dir():
        raise SystemExit("The local diarization or VAD assets are incomplete.")
    queue = Queue(settings)
    lock = (settings.state / "worker.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    stop_event = threading.Event()
    worker = threading.Thread(target=run, args=(queue, stop_event), daemon=True, name="transcription-worker")
    worker.start()
    try:
        serve(create_app(queue, ready=worker.is_alive), host="0.0.0.0", port=8091, threads=4,
              connection_limit=32, max_request_body_size=settings.max_bytes + 1024 * 1024,
              channel_timeout=30, ident="")
    finally:
        stop_event.set()
        queue.wake.set()
        lock.close()


if __name__ == "__main__":
    main()
