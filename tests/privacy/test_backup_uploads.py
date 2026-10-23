"""Datensicherung 2026-10-12 (Ralf: every four hours, uploads only with changed files, the copy off the server only
prepared): snapshot_uploads.py, the uploads step and the age rule in backup.sh, offsite.sh off and on."""
import os
import runpy
import time

from tests.platform.test_audit8_b_deploy import World, _read, _write, needs_bash

SNAPSHOT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "deploy", "docker",
                        "snapshot_uploads.py")


def _snap(src, snap, days=14):
    mod = runpy.run_path(SNAPSHOT)
    mod["main"](str(src), str(snap), days)
    return sorted(n for n in os.listdir(snap))


def test_snapshot_copies_only_changed_files_and_links_the_rest(tmp_path, capsys):
    src, snap = tmp_path / "instance", tmp_path / "snap"
    _write(str(src / "uploads" / "a.png"), "A")
    _write(str(src / "uploads" / "b.png"), "B")
    _write(str(src / "blog" / "c.jpg"), "C")
    first = _snap(src, snap)
    assert len(first) == 1 and "3 files, 3 copied" in capsys.readouterr().out
    time.sleep(1.1)                                                     # a new stamp (seconds)
    _write(str(src / "uploads" / "b.png"), "B2")                        # changed: other size
    os.remove(src / "blog" / "c.jpg")                                   # deleted in the app
    _write(str(src / "uploads" / "d.png"), "D")                         # new
    second = _snap(src, snap)
    assert len(second) == 2 and "3 files, 2 copied" in capsys.readouterr().out
    old, new = (snap / n for n in second)
    assert os.path.samefile(old / "uploads" / "a.png", new / "uploads" / "a.png")   # no second copy
    assert not os.path.samefile(old / "uploads" / "b.png", new / "uploads" / "b.png")
    assert (new / "uploads" / "b.png").read_text() == "B2" and (old / "uploads" / "b.png").read_text() == "B"
    assert not (new / "blog" / "c.jpg").exists() and (old / "blog" / "c.jpg").exists()
    assert (new / "uploads" / "d.png").read_text() == "D"
    # Ralf: a file copied once and never changed must not go with the snapshot it was copied in. The newest holds
    # a link of its own; removing the old snapshot (14 days) leaves the file there.
    __import__("shutil").rmtree(old)
    assert (new / "uploads" / "a.png").read_text() == "A"


def test_snapshot_keeps_14_days_and_always_the_newest(tmp_path):
    src, snap = tmp_path / "instance", tmp_path / "snap"
    _write(str(src / "uploads" / "a.png"), "A")
    for name in ("20260901-023000", "20260920-023000"):                 # older than 14 days
        _write(str(snap / name / "uploads" / "a.png"), "A")
    young = time.strftime("%Y%m%d-%H%M%S", time.gmtime(time.time() - 3 * 86400))
    _write(str(snap / young / "uploads" / "a.png"), "A")
    _write(str(snap / "20261001-000000.part" / "x"), "broken")           # a broken run
    names = _snap(src, snap)
    assert len(names) == 2 and names[0] == young
    # a single old snapshot (backup stopped for a while) is never removed: it is the newest
    lone = tmp_path / "lone"
    _write(str(lone / "20260901-023000" / "a"), "A")
    assert runpy.run_path(SNAPSHOT)["sweep"](str(lone), 14, time.time()) == []
    assert os.listdir(lone) == ["20260901-023000"]


def _server(tmp_path):
    w = World(tmp_path, repo=False)
    folder = w.tmp / "deploy"
    for name in ("backup.sh", "compose-env.sh", "alarm.sh", "offsite.sh", "lock_uploads.sh"):
        _write(str(folder / name), _read("deploy", "docker", name), 0o755)
    _write(str(folder / "snapshot_uploads.py"), _read("deploy", "docker", "snapshot_uploads.py"))
    return w, folder


def _backup(tmp_path, **env):
    w, folder = _server(tmp_path)
    return w, folder, w.run(script=str(folder / "backup.sh"), AS_SERVER="1", **env)


@needs_bash
def test_regular_run_snapshots_the_uploads_and_keeps_dumps_14_days(tmp_path):
    w, folder = _server(tmp_path)
    backups = w.path("backups")
    now = time.time()
    for name, days in (("askurious-20260920-0230.dump", 20), ("askurious-20261009-0230.dump", 3)):
        _write(str(backups / name), "PGDMP-old\n")
        os.utime(backups / name, (now - days * 86400, now - days * 86400))
    out = w.run(script=str(folder / "backup.sh"), AS_SERVER="1")
    assert out.returncode == 0, out.stdout + out.stderr
    names = sorted(os.listdir(w.path("backups")))
    assert "askurious-20260920-0230.dump" not in names and "askurious-20261009-0230.dump" in names
    assert "uploads" in names
    log = w.text("docker.log")
    assert "compose run --rm --no-deps -T -v" in log and "/backups/uploads:/snap researcher python - /app/instance /snap 14" in log
    assert "brand-researcher" not in log.split("compose run", 1)[1]      # no brand running
    # before a deploy: only the dump, no uploads
    w.path("docker.log").unlink()
    out = w.run("before-deploy", script=str(folder / "backup.sh"), AS_SERVER="1")
    assert out.returncode == 0 and "compose run" not in w.text("docker.log")


@needs_bash
def test_offsite_is_off_without_a_target_and_encrypts_with_one(tmp_path):
    w, folder, out = _backup(tmp_path)
    assert out.returncode == 0 and "off-site" not in out.stdout                  # off: nothing, no error
    # on: without a readable key an error (and the run says so)
    _write(str(folder / ".env"), "OFFSITE_TARGET=backup@example.org:askurious\n")
    out = w.run(script=str(folder / "backup.sh"), AS_SERVER="1")
    assert out.returncode == 1 and "OFFSITE_KEY" in out.stderr and "off-site copy failed" in out.stderr
    # with a key: gpg and rsync (stand-ins) get the newest dump, encrypted, and the uploads once a day
    # from a pipe (tar | gpg ... --encrypt) the stand-in reads it all, else tar's write fails when it ends first
    _write(str(w.bin / "gpg"), '#!/usr/bin/env bash\necho "$*" >> "$SERVER/gpg.log"\n'
           '[ "${@: -1}" = --encrypt ] && cat > /dev/null\n'
           'while [ $# -gt 0 ]; do [ "$1" = --output ] && out=$2; shift; done\necho enc > "$out"\n', 0o755)
    _write(str(w.bin / "rsync"), '#!/usr/bin/env bash\necho "$*" >> "$SERVER/rsync.log"\nls "${@: -2:1}" >> "$SERVER/rsync.log"\n', 0o755)
    _write(str(w.path("askurious-env", "offsite.asc")), "PUBLIC KEY\n")
    _write(str(w.path("backups", "uploads", "20261012-063000", "uploads", "a.png")), "A")
    _write(str(folder / ".env"), "OFFSITE_TARGET=backup@example.org:askurious\nOFFSITE_KEY=~/askurious-env/offsite.asc\n")
    out = w.run(script=str(folder / "backup.sh"), AS_SERVER="1")
    assert out.returncode == 0, out.stdout + out.stderr
    log = w.text("rsync.log")
    assert "backup@example.org:askurious/" in log and ".dump.gpg" in log and "uploads-" in log
    assert "--recipient-file" in w.text("gpg.log") and "--encrypt" in w.text("gpg.log")
    assert "before-deploy" not in log
    assert not w.path("backups", "offsite").exists()                             # the staging folder goes
    # the uploads only once a day
    w.path("rsync.log").unlink()
    assert w.run(script=str(folder / "backup.sh"), AS_SERVER="1").returncode == 0
    assert "uploads-" not in w.text("rsync.log") and ".dump.gpg" in w.text("rsync.log")


def test_cron_every_four_hours_and_status_age():
    deploy = _read("deploy", "docker", "deploy-docker.sh")
    assert "echo '30 2-22/4 * * * \\$HOME/askurious/deploy/docker/backup.sh" in deploy
    assert "backup=age:/stamps/backup-ok:9," in _read("deploy", "docker", "compose.yaml")
    assert "OFFSITE_TARGET" in _read("deploy", "docker", "BETRIEB.md")


GPG_STUB = ('#!/usr/bin/env bash\necho "$*" >> "$SERVER/gpg.log"\n[ "${@: -1}" = --encrypt ] && cat > /dev/null\n'
            'while [ $# -gt 0 ]; do [ "$1" = --output ] && out=$2; shift; done\necho enc > "$out"\n')


@needs_bash
def test_regular_dumps_encrypted_on_the_server_when_configured(tmp_path):
    """Audit 2026-10-18, CFG-03: the dumps lay readable next to the live database. With BACKUP_ENCRYPT_KEY each regular
    dump becomes <name>.dump.gpg once pg_restore read it, the plain one goes; the dumps before a deploy stay readable
    (the way back without the private key); without the line everything stays as before; a missing key fails the run
    and keeps the plain dump."""
    import json

    w, folder = _server(tmp_path)
    out = w.run(script=str(folder / "backup.sh"), AS_SERVER="1")
    assert out.returncode == 0 and all(n.endswith(".dump") for n in os.listdir(w.path("backups")) if "dump" in n)
    raw = json.loads(w.path("askurious-status", "backups.json").read_text())
    assert raw["encrypt"] is False and raw["offsite"] is False and raw["offsite_at"] == ""
    _write(str(folder / ".env"), "BACKUP_ENCRYPT_KEY=~/askurious-env/backup.asc\n")
    out = w.run(script=str(folder / "backup.sh"), AS_SERVER="1")                    # key missing
    assert out.returncode == 1 and "BACKUP_ENCRYPT_KEY" in out.stderr and "dump not encrypted" in out.stderr
    _write(str(w.bin / "gpg"), GPG_STUB, 0o755)
    _write(str(w.path("askurious-env", "backup.asc")), "PUBLIC KEY\n")
    out = w.run(script=str(folder / "backup.sh"), AS_SERVER="1")
    assert out.returncode == 0, out.stdout + out.stderr
    names = os.listdir(w.path("backups"))
    newest = sorted(n for n in names if n.endswith(".dump.gpg"))
    assert newest and "--recipient-file" in w.text("gpg.log")
    assert not [n for n in names if n.endswith(".dump") and n[:-5] + ".dump.gpg" in names]   # no plain twin
    raw = json.loads(w.path("askurious-status", "backups.json").read_text())
    assert raw["encrypt"] is True and any(d["encrypted"] and d["kind"] == "regular" for d in raw["dumps"])
    out = w.run("before-deploy", script=str(folder / "backup.sh"), AS_SERVER="1")
    assert out.returncode == 0 and [n for n in os.listdir(w.path("backups")) if "-before-deploy-" in n and n.endswith(".dump")]


@needs_bash
def test_offsite_takes_encrypted_dumps_as_they_are_and_marks_its_success(tmp_path):
    """CFG-03: a dump backup.sh encrypted goes off as it is; age works as well as GPG; a copy that went through leaves
    its time in ~/askurious-status/offsite-ok, which backups.json carries for Administration > Server."""
    import json

    w, folder = _server(tmp_path)
    _write(str(w.bin / "gpg"), GPG_STUB, 0o755)
    _write(str(w.bin / "age"), '#!/usr/bin/env bash\necho "$*" >> "$SERVER/age.log"\n'
           'while [ $# -gt 0 ]; do [ "$1" = -o ] && out=$2; shift; done\ncat > /dev/null 2>&1 || true\necho enc > "$out"\n', 0o755)
    _write(str(w.bin / "rsync"), '#!/usr/bin/env bash\nls "${@: -2:1}" >> "$SERVER/rsync.log"\n', 0o755)
    _write(str(w.path("askurious-env", "age.txt")), "age1qqqqexamplerecipient\n")
    _write(str(folder / ".env"), "OFFSITE_TARGET=backup@example.org:askurious\nOFFSITE_KEY=~/askurious-env/age.txt\n"
                                 "BACKUP_ENCRYPT_KEY=~/askurious-env/age.txt\n")
    out = w.run(script=str(folder / "backup.sh"), AS_SERVER="1")
    assert out.returncode == 0, out.stdout + out.stderr
    assert ".dump.age" in w.text("rsync.log") and "-R" in w.text("age.log") and not w.path("gpg.log").exists()
    assert w.path("askurious-status", "offsite-ok").exists()
    raw = json.loads(w.path("askurious-status", "backups.json").read_text())
    assert raw["offsite"] is True and raw["offsite_at"]
