"""The bars of the status page (app/status_node.py) count minutes on the quarter hours of the clock: a bar of 15
minutes never shows more than 15 checks, also when a restart of the service (a deploy) ran a second round within a
minute (Ralf, 2026-10-17: "1 von 16" in a bar of 15 minutes)."""
import time
from datetime import datetime

from flask import Flask

from app import status_node


def _summary(tmp_path, monkeypatch, rows, now):
    store = status_node.Store(str(tmp_path / "status.db"))
    store.add(rows)
    monkeypatch.setattr(status_node, "current", lambda: "de")
    with Flask(__name__).test_request_context("/"):
        return status_node.summary(store, "web", now)


def test_a_second_round_in_a_minute_counts_once(tmp_path, monkeypatch):
    slot = status_node.SLOT
    quarter = (int(time.time()) // slot - 3) * slot  # a whole quarter hour three bars back
    # a round every minute at :20, the old process's last round at 11:05:20, the restarted one's first at 11:05:50
    # (failed: the participants' container was just restarting), then on the full minutes from 11:06
    rows = [(quarter + m * 60 + 20, "web", 1, 20, "") for m in range(6)]
    rows += [(quarter + 5 * 60 + 50, "web", 0, 0, "unreachable")]
    rows += [(quarter + m * 60, "web", 1, 20, "") for m in range(6, 15)]
    got = _summary(tmp_path, monkeypatch, rows, quarter + 3 * slot + 30)
    bar = next(b for b in got["bars"] if b["at"] == datetime.fromtimestamp(quarter, status_node.BERLIN).strftime("%H:%M"))
    assert (bar["n"], bar["bad"], bar["state"]) == (15, 1, "minor")
    assert bar["to"] == datetime.fromtimestamp(quarter + slot, status_node.BERLIN).strftime("%H:%M")


def test_bars_stand_on_the_quarter_hours(tmp_path, monkeypatch):
    now = int(time.time())
    got = _summary(tmp_path, monkeypatch, [(now, "web", 1, 20, "")], now)
    assert len(got["bars"]) == status_node.SLOTS
    assert all(b["at"][-2:] in ("00", "15", "30", "45") for b in got["bars"])
    assert got["bars"][-1]["n"] == 1  # the quarter hour running now is the last bar


def test_page_names_minutes_not_a_fraction(tmp_path, monkeypatch):
    slot = status_node.SLOT
    quarter = (int(time.time()) // slot - 2) * slot
    monkeypatch.setenv("STATUS_CHECKS", "web=http:http://127.0.0.1:9/healthz")
    monkeypatch.setenv("STATUS_DB", str(tmp_path / "status.db"))
    monkeypatch.setattr(status_node.threading, "Thread", lambda *a, **k: type("T", (), {"start": lambda self: None})())
    status_node.Store(str(tmp_path / "status.db")).add(
        [(quarter + m * 60, "web", int(m != 3), 20, "") for m in range(15)])
    html = status_node.create().test_client().get("/", headers={"Accept-Language": "de"}).get_data(as_text=True)
    start = datetime.fromtimestamp(quarter, status_node.BERLIN).strftime("%H:%M")
    end = datetime.fromtimestamp(quarter + slot, status_node.BERLIN).strftime("%H:%M")
    assert f"Viertelstunden mit Fehlern: {start}–{end} (1 von 15 Min.)" in html
    assert '<p class="visually-hidden">Viertelstunden mit Fehlern:' in html  # only for screen readers (2026-10-17)
    assert f'title="{start}–{end}: in 1 von 15 geprüften Minuten gestört"' in html
    assert "1/15" not in html


def test_colour_by_how_long_a_bar_was_disrupted():
    """One or two minutes are a short drop (yellow), more is orange, every minute red (Ralf, 2026-10-17)."""
    assert [status_node.bar_state(15, bad) for bad in (0, 1, 2, 3, 14, 15)] ==         ["ok", "minor", "minor", "partial", "partial", "down"]
    assert status_node.bar_state(0, 0) == "none" and status_node.bar_state(1, 1) == "down"


def test_a_bar_gets_lower_and_redder_with_each_disrupted_minute():
    """Ralf, 2026-10-18: full height without a fault, one step lower per disrupted minute of 15, yellow at one minute
    and gradually red at 14; every minute down leaves an empty place (before a red X)."""
    assert [status_node.bar_level(15, bad) for bad in (0, 1, 2, 7, 14, 15)] == [0, 1, 2, 7, 14, 15]
    assert status_node.bar_level(0, 0) == 0 and status_node.bar_level(3, 3) == 15
    assert status_node.bar_level(5, 1) == 3 and status_node.bar_level(30, 1) == 1 and status_node.bar_level(30, 29) == 14
    import os

    css = open(os.path.join(os.path.dirname(status_node.__file__), "static", "status.css"), encoding="utf-8").read()
    heights = [float(css.split(f".status-bars li.status-l{k} {{ height: ", 1)[1].split("%", 1)[0]) for k in range(1, 15)]
    assert heights == sorted(heights, reverse=True) and heights[0] < 100 and heights[-1] > 0
    assert "var(--danger) 0%, var(--warn-bar)" in css and "var(--danger) 100%, var(--warn-bar)" in css
    rule = css.split(".status-bars li.status-down {", 1)[1].split("}", 1)[0]
    assert "transparent" in rule and "mask" not in css and "data:" not in css  # the page's CSP refuses data: addresses


def test_the_page_marks_each_bar_with_its_level(tmp_path, monkeypatch):
    slot = status_node.SLOT
    quarter = (int(time.time()) // slot - 2) * slot
    monkeypatch.setenv("STATUS_CHECKS", "web=http:http://127.0.0.1:9/healthz")
    monkeypatch.setenv("STATUS_DB", str(tmp_path / "status.db"))
    monkeypatch.setattr(status_node.threading, "Thread", lambda *a, **k: type("T", (), {"start": lambda self: None})())
    rows = [(quarter + m * 60, "web", int(m >= 4), 20, "") for m in range(15)]
    rows += [(quarter - slot + m * 60, "web", 0, 0, "unreachable") for m in range(15)]
    status_node.Store(str(tmp_path / "status.db")).add(rows)
    html = status_node.create().test_client().get("/", headers={"Accept-Language": "de"}).get_data(as_text=True)
    assert 'class="status-partial status-l4"' in html
    assert 'class="status-down status-l15"' in html and "in 15 von 15 geprüften Minuten gestört" in html  # title stays
