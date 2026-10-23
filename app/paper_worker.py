"""Paper scans run outside gunicorn at low priority, with no credentials written to disk (SP13 PF-10)."""
import json
import os
import subprocess
import sys
import threading

# A child reading scans ends at the latest after this much CPU time or this long a run (POSIX only; Windows has no
# resource module): a file that makes it hang must not keep a heavy place for ever (code review 2026-10-15, J5).
# 400 pages take a few minutes.
CPU_SECONDS = 30 * 60
WALL_SECONDS = 60 * 60


def start(app, survey_id, names):
    from flask import g
    from app import heavy
    from app.lang import de_en

    # Reserve before importing another whole application/numpy in a child. A queue of waiting children
    # would consume memory despite the limit on the actual scans.
    if not heavy.hold(wait=0):
        raise ValueError(de_en("Die Verarbeitung ist gerade ausgelastet. Bitte laden Sie die Scans in einer Minute erneut hoch.",
                               "Processing is busy right now. Please upload the scans again in a minute."))
    config = {}
    for key, value in app.config.items():
        try:
            json.dumps(value)
        except (TypeError, ValueError):
            continue
        config[key] = value
    options = {"stdin": subprocess.PIPE, "stdout": subprocess.DEVNULL, "stderr": None,
               "cwd": os.path.dirname(os.path.dirname(__file__))}
    payload = {"config": config, "survey_id": survey_id, "names": names}
    try:
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS
        else:
            options["start_new_session"] = True
            place = g.get("_heavy_place")
            if place is not None:
                payload["slot_fd"] = place.file.fileno()
                options["pass_fds"] = (place.file.fileno(),)
        child = subprocess.Popen([sys.executable, "-m", "app.paper_worker"], **options)
        threading.Thread(target=child.wait, daemon=True, name="paper-reaper").start()
        try:
            child.stdin.write(json.dumps(payload).encode())
            child.stdin.close()
        except (OSError, BrokenPipeError):
            child.terminate()
            raise
        return child
    finally:
        if os.name != "nt":
            heavy.release()  # the inherited descriptor keeps the child's shared place occupied


def limit():
    """CPU time (SIGXCPU) and run time (SIGALRM) of this process limited; both end it. Nothing on Windows."""
    try:
        import resource
    except ImportError:
        return
    import signal

    soft, hard = resource.getrlimit(resource.RLIMIT_CPU)
    if hard == resource.RLIM_INFINITY or hard > CPU_SECONDS:
        resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS + 60))
    signal.alarm(WALL_SECONDS)


def main(payload):
    from app import create_app, heavy, paper
    from config import Config

    if hasattr(os, "nice"):
        os.nice(10)
    limit()
    slot = os.fdopen(payload["slot_fd"], "ab") if payload.get("slot_fd") is not None else None
    try:
        app = create_app(type("PaperConfig", (Config,), payload["config"]))
        with app.test_request_context():
            if slot is None and not heavy.hold(wait=300):
                raise RuntimeError("No paper processing slot")
            try:
                paper.process(app, payload["survey_id"], payload["names"])
            finally:
                if slot is None:
                    heavy.release()
    finally:
        if slot is not None:
            slot.close()


if __name__ == "__main__":
    main(json.load(sys.stdin))
