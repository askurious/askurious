"""Audit 2026-10-05, L-5: copies of the database outside it. Copies before migrations go after 30 days (on SQLite
the newest stays), snapshots a hard-ended move left in the temp folder go, and Administration > Datenbank names
them, with a button to delete the old SQLite file after the move to PostgreSQL."""
import os
import tempfile
import time
from datetime import datetime, timedelta

from app import dbmove, migrations
from app.extensions import db
from app.models import utcnow


def _backup(folder, at):
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"app-{at:%Y%m%d-%H%M%S}-vor-migration.db")
    with open(path, "wb") as f:
        f.write(b"SQLite format 3\0 Geheimwort")
    return os.path.basename(path)


def test_copies_before_migrations_go_after_30_days(app, tmp_path, monkeypatch):
    monkeypatch.setattr(app, "instance_path", str(tmp_path / "instance"))
    folder = tmp_path / "instance" / "backups"
    now = datetime(2026, 10, 5, 12, 0)
    old1, old2 = _backup(folder, now - timedelta(days=90)), _backup(folder, now - timedelta(days=40))
    fresh = _backup(folder, now - timedelta(days=3))
    (folder / "notizen.txt").write_text("bleibt")
    with app.app_context():
        listed = migrations.backups(app)
        assert [b["name"] for b in listed] == [fresh, old2, old1]
        assert migrations.sweep_backups(app, now) == [old2, old1]
        assert sorted(os.listdir(folder)) == sorted([fresh, "notizen.txt"])
        # the newest stays on SQLite whatever its age (the way back after the last migration) ...
        later = now + timedelta(days=100)
        if db.engine.dialect.name == "sqlite":
            assert migrations.sweep_backups(app, later) == [] and migrations.backups(app)[0]["until"] is None
        else:  # ... on PostgreSQL every copy is of a database no longer in use
            assert migrations.sweep_backups(app, later) == [fresh]


def test_snapshots_left_in_the_temp_folder_go(app):
    with app.app_context():
        handle, stale = tempfile.mkstemp(suffix=".db", prefix=dbmove.COPY_PREFIX)
        os.close(handle)
        handle, running = tempfile.mkstemp(suffix=".db", prefix=dbmove.COPY_PREFIX)
        os.close(handle)
        day_ago = time.time() - dbmove.STALE_COPY_SECONDS - 60
        os.utime(stale, (day_ago, day_ago))
        try:
            assert stale in [c["path"] for c in dbmove.stale_copies()] and running not in [c["path"] for c in dbmove.stale_copies()]
            dbmove.sweep_copies()
            assert not os.path.exists(stale) and os.path.exists(running)  # a copy running right now stays
        finally:
            for path in (stale, running):
                if os.path.exists(path):
                    os.remove(path)


def test_the_page_names_the_copies(app, client, tmp_path, monkeypatch):
    monkeypatch.setattr(app, "instance_path", str(tmp_path / "instance"))
    page = client.get("/admin/database").get_data(as_text=True)
    assert 'id="sicherungen"' in page  # one block "Sicherungen" (Ralf, 2026-10-18)
    name = _backup(tmp_path / "instance" / "backups", utcnow() - timedelta(days=2))
    page = client.get("/admin/database").get_data(as_text=True)
    assert name in page and "Alte SQLite-Datei löschen" not in page


def test_the_old_sqlite_file_after_the_move(app, client, tmp_path, monkeypatch):
    monkeypatch.setattr(app, "instance_path", str(tmp_path / "instance"))
    os.makedirs(tmp_path / "instance")
    old = tmp_path / "instance" / "app.db"
    old.write_bytes(b"SQLite format 3\0 Geheimwort")
    (tmp_path / "instance" / "app.db-wal").write_bytes(b"wal")
    with app.app_context():
        on_sqlite = db.engine.dialect.name == "sqlite"
        if on_sqlite:  # the file the app works on is never offered for deletion
            assert dbmove.old_sqlite(app) is None
            monkeypatch.setattr(dbmove, "_on_sqlite", lambda: False)  # as after the move
        assert dbmove.old_sqlite(app)["size"] == len(b"SQLite format 3\0 Geheimwort") + 3
    page = client.get("/admin/database").get_data(as_text=True)
    assert "Die alte SQLite-Datei liegt noch" in page and 'value="remove_old"' in page
    assert 'data-confirm-type="löschen"' in page  # type "löschen" in the dialog (Ralf, 2026-10-05)
    r = client.post("/admin/database", data={"action": "remove_old"}, follow_redirects=True).get_data(as_text=True)
    assert "Die alte SQLite-Datei ist gelöscht." in r
    assert not old.exists() and not (tmp_path / "instance" / "app.db-wal").exists()
    r = client.post("/admin/database", data={"action": "remove_old"}, follow_redirects=True).get_data(as_text=True)
    assert "Es liegt keine alte SQLite-Datei vor" in r
