"""Administration > Server, section "Container": the load the cron job deploy/docker/container-stats.sh writes
(app/containers.py; Ralf, 2026-10-09)."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from app import containers

NOW = datetime(2026, 10, 9, 15, 0, tzinfo=timezone.utc)


def _write(tmp_path, when=NOW, history=()):
    t = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    stats = [{"name": "askurious-participant-a-1", "cpu": "250.50%", "mem": "1.254GiB / 18.74GiB", "pids": "300"},
             {"name": "askurious-db-1", "cpu": "19.08%", "mem": "368.1MiB / 18.74GiB", "pids": "90"},
             {"name": "askurious-nginx-1", "cpu": "--", "mem": "-- / --", "pids": "--"}]
    info = [{"name": "/askurious-participant-a-1", "restarts": 2, "nano_cpus": 20000000000, "mem_limit": 0,
             "state": {"Status": "running", "StartedAt": "2026-10-09T14:39:30.729755059Z", "Health": {"Status": "healthy"}}},
            {"name": "/askurious-db-1", "restarts": 0, "nano_cpus": 0, "mem_limit": 0,
             "state": {"Status": "running", "StartedAt": "2026-10-09T13:13:45.6Z"}},
            {"name": "/askurious-nginx-1", "restarts": 0, "nano_cpus": 0, "mem_limit": 0,
             "state": {"Status": "exited", "StartedAt": "0001-01-01T00:00:00Z"}}]
    path = tmp_path / "containers.json"
    path.write_text(json.dumps({"time": t, "host": {"cpus": 24, "mem_total": 20119158784, "mem_available": 15374221312,
                                                    "load": [0.34, 0.41, 0.61]}, "stats": stats, "info": info}))
    lines = [json.dumps({"time": (NOW - age).strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "stats": [{"name": "askurious-participant-a-1", "cpu": cpu, "mem": "1GiB / 18GiB", "pids": "1"}]})
             for age, cpu in history]
    (tmp_path / "containers-history.jsonl").write_text("\n".join(lines + ["{cut off"]) + "\n")
    return str(path)


def test_overview_reads_cores_memory_peaks_and_order(tmp_path):
    path = _write(tmp_path, history=[(timedelta(hours=2), "800.00%"), (timedelta(hours=30), "1900.00%")])
    o = containers.overview(path, now=NOW)
    assert o["containers"] and not o["stale"]
    a, db, nginx = o["rows"]  # the order of compose.yaml, not of the file
    assert (a["service"], db["service"], nginx["service"]) == ("participant-a", "db", "nginx")
    assert a["cores"] == 2.505 and a["limit"] == 20 and a["restarts"] == 2 and a["health"] == "healthy"
    assert a["peak"] == 8.0  # only the last 24 hours
    assert a["mem"] == 1.254 * 1024 ** 3 and a["started"] == datetime(2026, 10, 9, 14, 39, 30, tzinfo=timezone.utc)
    assert db["limit"] == 24 and db["peak"] == pytest.approx(0.1908)  # no ceiling of its own: every core; no history: now
    assert nginx["cores"] is None and nginx["mem"] is None and nginx["started"] is None and nginx["status"] == "exited"


def test_old_values_are_stale_and_no_file_means_this_machine(tmp_path):
    assert containers.overview(_write(tmp_path, when=NOW - timedelta(minutes=6)), now=NOW)["stale"]
    for path in ("", str(tmp_path / "missing.json")):
        o = containers.overview(path, now=NOW)
        assert not o["containers"] and o["rows"] == [] and o["host"]["cpus"]


def test_whisper_container_uses_its_own_cpu_and_memory_limits(app, client, tmp_path):
    path = _write(tmp_path, when=datetime.now(timezone.utc))
    data = json.loads(open(path).read())
    data["stats"].append({"name": "askurious-whisper-1", "cpu": "250%", "mem": "2GiB / 4GiB", "pids": "8"})
    data["info"].append({"name": "/askurious-whisper-1", "nano_cpus": 4000000000,
                         "mem_limit": 4 * 1024 ** 3, "state": {"Status": "running"}})
    open(path, "w").write(json.dumps(data))
    row = next(row for row in containers.overview(path)["rows"] if row["service"] == "whisper")
    assert row["cores"] == 2.5 and row["limit"] == 4 and row["mem"] == 2 * 1024 ** 3
    app.config["CONTAINER_STATS"] = path
    assert "WhisperX (Transkription)" in client.get("/admin/database").get_data(as_text=True)


def test_server_tab_shows_the_section(app, client, tmp_path):
    app.config["CONTAINER_STATS"] = ""
    page = client.get("/admin/database").get_data(as_text=True)
    assert 'id="container"' in page and "kein Container-System" in page
    app.config["CONTAINER_STATS"] = _write(tmp_path, when=datetime.now(timezone.utc))
    page = client.get("/admin/database").get_data(as_text=True)
    assert "Teilnahme A" in page and "Datenbank (PostgreSQL)" in page and "2,5 / 20 Kernen" in page
    assert "exited" in page and "älter als fünf Minuten" not in page


def test_little_load_is_not_shown_as_zero(app, client, tmp_path):
    """An idle server: 0,3 % of a core is "< 0,1", not "0,0" (Ralf, 2026-10-10); a container at rest stays 0,0."""
    path = _write(tmp_path, when=datetime.now(timezone.utc))
    data = json.loads(open(path).read())
    data["stats"][0]["cpu"], data["stats"][1]["cpu"] = "0.31%", "0.00%"
    open(path, "w").write(json.dumps(data))
    app.config["CONTAINER_STATS"] = path
    page = client.get("/admin/database").get_data(as_text=True)
    assert "&lt; 0,1 / 20 Kernen" in page and "0,0 / 24 Kernen" in page
