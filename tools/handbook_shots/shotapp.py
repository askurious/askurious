"""The app for the handbook screenshots: a throwaway database and instance folder in WORK_DIR, never the
developer's own database (instance/app.db), DEV_LOGIN on, no AI key, no mail."""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PORT = int(os.environ.get("SHOTS_PORT", "5070"))


def setup_env(work_dir):
    """Environment for config.py; must run before config or app is imported."""
    work_dir = os.path.abspath(work_dir)
    os.makedirs(os.path.join(work_dir, "instance", "uploads"), exist_ok=True)
    os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(work_dir, "instance", "app.db").replace("\\", "/")
    os.environ["DEV_LOGIN"] = "1"
    os.environ["PUBLIC_BASE_URL"] = f"http://localhost:{PORT}"
    os.environ["SECRET_KEY"] = "handbook-shots-only-local"
    os.environ["OPENAI_API_KEY"] = ""  # never call paid AI from the screenshots
    os.environ["MAIL_HOST"] = ""
    os.environ["SLACK_WEBHOOK_URL"] = ""
    os.environ["COMPUTE_URL"] = ""
    os.environ["ALLOWED_ORCIDS"] = "*"
    os.environ.setdefault("HB_PARALLEL", "1")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    return work_dir


def make_app(work_dir):
    work_dir = setup_env(work_dir)
    import flask

    import app as app_pkg
    from config import Config

    class ShotConfig(Config):
        UPLOAD_FOLDER = os.path.join(work_dir, "instance", "uploads")

    instance = os.path.join(work_dir, "instance")
    original = app_pkg.Flask

    def flask_here(name, *args, **kwargs):  # the instance folder (backups, prices) in WORK_DIR too
        kwargs.setdefault("instance_path", instance)
        return original(name, *args, **kwargs)

    app_pkg.Flask = flask_here
    try:
        return app_pkg.create_app(ShotConfig)
    finally:
        app_pkg.Flask = original
