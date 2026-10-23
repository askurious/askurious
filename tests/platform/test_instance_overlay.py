"""The files of one installation over the public code (instance_overlay/README.md): templates in
INSTANCE_OVERLAY/templates take the place of the code's own, everything else stays the code's."""
import os
import subprocess

from flask import Flask

from app import _overlay_templates

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))


def _read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        return f.read()


def _app(folder):
    app = Flask("app")
    app.config["INSTANCE_OVERLAY"] = folder
    _overlay_templates(app)
    return app


def _source(app, name):
    return app.jinja_loader.get_source(app.jinja_env, name)[0]


def test_overlay_template_takes_the_place_of_the_codes(tmp_path):
    (tmp_path / "templates" / "public").mkdir(parents=True)
    (tmp_path / "templates" / "public" / "_owner.html").write_text("Betreiber der Instanz", encoding="utf-8")
    app = _app(str(tmp_path))
    assert _source(app, "public/_owner.html") == "Betreiber der Instanz"
    # a template the installation does not bring stays the code's
    assert _source(app, "public/privacy.html") == _read("app/templates/public/privacy.html")
    with app.test_request_context():
        assert app.jinja_env.get_template("public/_owner.html").render() == "Betreiber der Instanz"


def test_no_overlay_or_empty_folder_keeps_the_code(tmp_path):
    own = _read("app/templates/public/_owner.html")
    for folder in ("", str(tmp_path), str(tmp_path / "missing")):
        assert _source(_app(folder), "public/_owner.html") == own


def test_tests_never_take_an_installations_templates(app):
    assert app.config["INSTANCE_OVERLAY"] == ""


def test_overlay_goes_up_with_every_deploy():
    """The Docker image copies the folder, the deploy sends it, compose passes the setting on."""
    assert "COPY instance_overlay ./instance_overlay" in _read("deploy/docker/Dockerfile")
    docker = _read("deploy/docker/deploy-docker.sh")
    assert "storage_guard_probe.py instance_overlay " in docker and "INSTANCE_OVERLAY_SRC" in docker
    assert "\n  INSTANCE_OVERLAY:\n" in _read("deploy/docker/compose.yaml")


def test_only_the_readme_of_the_overlay_is_code():
    tracked = subprocess.run(["git", "-C", ROOT, "ls-files", "instance_overlay"], capture_output=True, text=True).stdout
    assert tracked.split() in ([], ["instance_overlay/README.md"])  # [] before the commit
    ignore = _read(".gitignore")
    assert "instance_overlay/*\n!instance_overlay/README.md" in ignore
