"""Administration > Server, block "Sicherungen" (Ralf, 2026-10-18, option A): backup.sh writes the list of ~/backups
into ~/askurious-status/backups.json, the page shows it; no container sees, offers or deletes a dump."""
import json
from datetime import datetime, timedelta, timezone

from app import backups
from tests.platform.test_audit8_b_deploy import _read, needs_bash
from tests.privacy.test_backup_uploads import _server

NOW = datetime(2026, 10, 18, 9, 0, tzinfo=timezone.utc)


def _list(tmp_path, **extra):
    folder = tmp_path / "stamps"
    folder.mkdir(exist_ok=True)
    data = {"time": "2026-10-18T06:30:05Z", "run": "regular", "ok": True, "offsite": False,
            "dumps": [{"name": "askurious-20261018-063000.dump", "database": "askurious", "kind": "regular", "size": 2048, "at": "2026-10-18T06:30:04Z"},
                      {"name": "askurious-20261017-223000.dump", "database": "askurious", "kind": "regular", "size": 1024, "at": "2026-10-17T22:30:04Z"},
                      {"name": "askurious-before-deploy-20261016-101500.dump", "database": "askurious", "kind": "before-deploy", "size": 512, "at": "2026-10-16T10:15:00Z"},
                      {"name": "polltergeist-20261018-063000.dump", "database": "polltergeist", "kind": "regular", "size": 99, "at": "2026-10-18T06:30:04Z"}],
            "uploads": [{"name": "uploads", "snapshots": 84, "newest": "20261018-063010", "size": 5000},
                        {"name": "uploads-brand", "snapshots": 3, "newest": "20261018-063010", "size": 7}]}
    data.update(extra)
    (folder / "backups.json").write_text(json.dumps(data), encoding="utf-8")
    return str(folder / "containers.json")


def test_overview_shows_own_database_only_with_deletion_dates(tmp_path):
    stats = _list(tmp_path)
    b = backups.overview(stats, "askurious", now=NOW)
    assert [d["name"] for d in b["dumps"]] == ["askurious-20261018-063000.dump", "askurious-20261017-223000.dump",
                                                "askurious-before-deploy-20261016-101500.dump"]
    assert b["size"] == 2048 + 1024 + 512 and len(b["regular"]) == 2 and not b["stale"] and b["ok"]
    assert b["dumps"][0]["until"] == datetime(2026, 11, 1, 6, 30, 4, tzinfo=timezone.utc)      # 14 days
    assert b["dumps"][2]["until"] == datetime(2026, 11, 15, 10, 15, tzinfo=timezone.utc)       # 30 before a deploy
    assert b["uploads"]["snapshots"] == 84
    brand = backups.overview(stats, "polltergeist", now=NOW)                    # a brand sees only its own
    assert [d["name"] for d in brand["dumps"]] == ["polltergeist-20261018-063000.dump"] and brand["uploads"]["size"] == 7
    assert backups.overview(stats, "askurious", now=NOW + timedelta(hours=10))["stale"]
    assert backups.overview(None, "askurious") is None and backups.overview(str(tmp_path / "x" / "c.json"), None) is None


def test_page_lists_the_backups_without_download_or_delete(app, client, tmp_path, monkeypatch):
    monkeypatch.setitem(app.config, "CONTAINER_STATS", _list(tmp_path, ok=False))
    page = client.get("/admin/database").get_data(as_text=True)
    assert 'id="sicherungen"' in page and "askurious-20261018-063000.dump" in page
    assert "polltergeist-20261018-063000.dump" not in page                      # the test database is no brand
    assert "Der letzte Lauf von backup.sh ist fehlgeschlagen" in page or "älter als neun Stunden" in page
    assert "aus: Die Sicherungen liegen nur auf diesem Server" in page
    section = page.split('id="sicherungen"', 1)[1].split("</section>", 1)[0]
    assert "<form" not in section and "download" not in section.lower() and "href" not in section
    assert "Zurück zu SQLite" not in page and "Kopien der Datenbank" not in page


def test_dumps_never_reach_a_container():
    compose = _read("deploy", "docker", "compose.yaml")
    assert "/backups:" not in compose.replace("/backups:/snap", "")             # only backup.sh's snapshot run


@needs_bash
def test_backup_sh_writes_the_list(tmp_path):
    w, folder = _server(tmp_path)
    for stamp in ("20261017-223010", "20261018-063010", "20261018-063010.part"):  # two snapshots, one broken run
        w.path("backups", "uploads", stamp).mkdir(parents=True)
    out = w.run(script=str(folder / "backup.sh"), AS_SERVER="1")
    assert out.returncode == 0, out.stdout + out.stderr
    raw = json.loads(w.path("askurious-status", "backups.json").read_text())
    # counted with a glob: GNU find 4.10 read the former -regex with \{8\} as no match, the page said "0 Stände" (Ralf
    # 2026-10-18)
    assert raw["uploads"][0]["snapshots"] == 2 and raw["uploads"][0]["newest"] == "20261018-063010"
    assert raw["ok"] is True and raw["run"] == "regular" and raw["offsite"] is False
    assert [d["kind"] for d in raw["dumps"]] == ["regular"] and raw["dumps"][0]["database"] == "askurious"
    assert raw["dumps"][0]["size"] > 0 and raw["uploads"][0]["name"] == "uploads"
    out = w.run("before-deploy", script=str(folder / "backup.sh"), AS_SERVER="1")
    raw = json.loads(w.path("askurious-status", "backups.json").read_text())
    assert raw["run"] == "before-deploy" and sorted(d["kind"] for d in raw["dumps"]) == ["before-deploy", "regular"]


def test_overview_says_when_the_copy_off_the_server_last_went_through(tmp_path):
    """Audit 2026-10-18, CFG-03: "offsite" only said whether a target was set; now also when a copy last went through
    (offsite.sh's mark), a copy set up but silent for over nine hours is a warning, and whether the dumps on the server
    are encrypted."""
    stats = _list(tmp_path, offsite=True, offsite_at="2026-10-18T06:31:00Z", encrypt=True)
    b = backups.overview(stats, "askurious", now=NOW)
    assert b["offsite_at"] == datetime(2026, 10, 18, 6, 31, tzinfo=timezone.utc) and not b["offsite_stale"] and b["encrypt"]
    assert backups.overview(stats, "askurious", now=NOW + timedelta(hours=10))["offsite_stale"]
    never = backups.overview(_list(tmp_path, offsite=True, offsite_at=""), "askurious", now=NOW)
    assert never["offsite_at"] is None and never["offsite_stale"]
    off = backups.overview(_list(tmp_path), "askurious", now=NOW)
    assert not off["offsite_stale"] and not off["encrypt"]
