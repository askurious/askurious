"""Paper uploads leave the web process (SP13 PF-10)."""
import io

from PIL import Image
from werkzeug.datastructures import FileStorage

from app import paper, paper_worker
from app.extensions import db
from app.models import Survey, User


def test_upload_dispatches_to_worker_outside_testing(app, client, survey, monkeypatch):
    image = io.BytesIO()
    Image.new("RGB", (30, 30), "white").save(image, format="PNG")
    image.seek(0)
    starts = []
    monkeypatch.setattr(paper_worker, "start", lambda app, sid, names: starts.append((sid, names)))
    monkeypatch.setattr(paper, "process", lambda *a: (_ for _ in ()).throw(AssertionError("web process did the scan")))
    app.config.update(TESTING=False, PAPER_SYNC=False)
    with app.app_context():
        study = db.session.get(Survey, 1)
        user = db.session.scalar(db.select(User))
        assert paper.accept_upload(study, [FileStorage(image, filename="scan.png")], user) == 1
    assert starts and starts[0][0] == 1 and starts[0][1][0].endswith(".png")


def test_busy_scans_spawn_no_waiting_child(app, monkeypatch):
    from app import heavy

    monkeypatch.setattr(heavy, "hold", lambda wait: False)
    monkeypatch.setattr(paper_worker.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("child started")))
    import pytest

    with app.test_request_context(), pytest.raises(ValueError, match="ausgelastet"):
        paper_worker.start(app, 1, ["scan.png"])


def test_files_waiting_behind_a_long_file_are_kept_alive(app, client, survey, monkeypatch, tmp_path):
    """Code review 2026-10-15, J2: while the first file of an upload is read, the next ones got no beat and the hourly
    cleaning took them after STALE_MINUTES."""
    import json
    import os
    from datetime import timedelta

    from app import paper_scan
    from app.models import utcnow

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "a.png").write_bytes(b"x")
    (inbox / "b.png").write_bytes(b"x")
    (inbox / "b.png.json").write_text(json.dumps({"file": "b", "beat": "2000-01-01T00:00:00"}))
    old = (utcnow() - timedelta(hours=2)).timestamp()
    os.utime(inbox / "b.png", (old, old))
    monkeypatch.setattr(paper_scan, "pages_from_upload", lambda name, data, left: iter([(None, "file")] * 3))
    monkeypatch.setattr(paper, "_store_page", lambda *a: None)
    clock = iter(range(0, 10_000, 100))
    monkeypatch.setattr(paper.time, "monotonic", lambda: next(clock))
    with app.test_request_context():
        assert paper._process_file(1, str(inbox), "a.png", 10, set(), waiting=["b.png"]) == 3
    assert not (inbox / "a.png").exists()
    assert utcnow() - paper._mtime(str(inbox / "b.png")) < timedelta(minutes=1)
    assert json.loads((inbox / "b.png.json").read_text())["beat"] > "2026"


def test_the_paper_child_limits_its_cpu_and_run_time(monkeypatch):
    """Code review 2026-10-15, J5: on POSIX the child sets RLIMIT_CPU and an alarm; Windows has no resource module."""
    import signal
    import sys
    import types

    calls = []
    fake = types.SimpleNamespace(RLIMIT_CPU=0, RLIM_INFINITY=-1, getrlimit=lambda what: (-1, -1),
                                 setrlimit=lambda what, pair: calls.append(("cpu", pair)))
    monkeypatch.setitem(sys.modules, "resource", fake)
    monkeypatch.setattr(signal, "alarm", lambda s: calls.append(("alarm", s)), raising=False)
    paper_worker.limit()
    assert calls == [("cpu", (paper_worker.CPU_SECONDS, paper_worker.CPU_SECONDS + 60)),
                     ("alarm", paper_worker.WALL_SECONDS)]
    monkeypatch.setitem(sys.modules, "resource", None)  # import fails as on Windows
    calls.clear()
    paper_worker.limit()
    assert calls == []


def test_a_form_shared_by_every_page_is_walked_once():
    """J5: the forms of a PDF walked once per file, not again on every page (seen was per page)."""
    from app import paper_scan

    import types

    walked = []

    class Obj(dict):
        def __init__(self, data, idnum=None):
            super().__init__(data)
            self.indirect_reference = types.SimpleNamespace(idnum=idnum) if idnum else None

        def get_object(self):
            return self

        def keys(self):
            walked.append(self.get("/Name"))
            return [k for k in super().keys() if k != "/Name"]

    image = Obj({"/Subtype": "/Image", "/Width": 10, "/Height": 10}, 3)
    form = Obj({"/Subtype": "/Form", "/Resources": Obj({"/XObject": Obj({"/Name": "inner", "im": image})})}, 2)
    forms = {}
    for _ in range(5):
        page = Obj({"/Resources": Obj({"/XObject": Obj({"/Name": "page", "f": form})})})
        assert paper_scan._largest_image(page, forms=forms) is image
    assert walked.count("inner") == 1 and walked.count("page") == 5
