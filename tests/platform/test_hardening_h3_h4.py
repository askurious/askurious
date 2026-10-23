"""Hardening of the security audit 2026-10-04: H3 (estimations compute a few at a time), H4 (every package locked
with version and hash)."""
import json
import os
import re
import threading
import time

from app import maxdiff_hb

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_estimations_wait_for_a_free_place(app, tmp_path):
    app.config["HB_PARALLEL"] = 1
    go, started = threading.Event(), []

    def slow(inp, progress):
        started.append(inp["key"])
        go.wait(10)
        return {"key": inp["key"]}

    paths = [(str(tmp_path / f"r{n}.json"), str(tmp_path / f"m{n}.json")) for n in range(2)]
    for _, marker in paths:  # as start() writes it before the thread begins
        with open(marker, "w", encoding="utf-8") as f:
            json.dump({"state": "running"}, f)
    threads = [threading.Thread(target=maxdiff_hb._estimate_and_store, args=(app, p, {"key": f"k{n}"}, {}, slow))
               for n, p in enumerate(paths)]
    with app.app_context():
        threads[0].start()
        for _ in range(100):
            if started:
                break
            time.sleep(0.05)
        threads[1].start()
        time.sleep(0.5)
        assert started == ["k0"]                                                # only one computes
        with open(paths[1][1], encoding="utf-8") as f:
            assert json.load(f)["queued"] is True                               # the other one says that it waits
        go.set()
        for t in threads:
            t.join(15)
    assert started == ["k0", "k1"]
    for result, marker in paths:
        with open(result, encoding="utf-8") as f:
            assert json.load(f)["key"] in ("k0", "k1")
        assert not os.path.exists(marker)                                       # done, nothing left behind


def _lock():
    pins = {}
    with open(os.path.join(HERE, "requirements.lock"), encoding="utf-8") as f:
        text = f.read()
    for block in re.split(r"\n(?=\S)", text):
        m = re.match(r"([A-Za-z0-9_.\-\[\]]+)==(\S+)", block)
        if m:
            pins[m.group(1).lower()] = (m.group(2), re.findall(r"--hash=sha256:([0-9a-f]{64})", block))
    return pins


def test_every_requirement_is_locked_with_its_version_and_a_hash():
    pins = _lock()
    assert len(pins) > 30 and all(hashes for _, hashes in pins.values())   # indirect packages too, each with a hash
    for source in ("requirements.txt", "requirements-postgres.txt"):
        with open(os.path.join(HERE, source), encoding="utf-8") as f:
            for line in f:
                m = re.match(r"([A-Za-z0-9_.\-]+)(\[[^\]]*\])?==(\S+)", line.strip())
                if m:
                    name = m.group(1).lower().replace("_", "-")
                    assert name in pins, f"{name} from {source} is not in requirements.lock: run tools/lock_requirements.py"
                    assert pins[name][0] == m.group(3), f"{name}: {m.group(3)} in {source}, {pins[name][0]} in the lock"
    for indirect in ("werkzeug", "jinja2", "itsdangerous", "urllib3", "markupsafe", "sqlalchemy"):
        assert indirect in pins
