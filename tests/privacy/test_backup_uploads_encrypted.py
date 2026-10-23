"""The uploads snapshots encrypted (Ralf 2026-10-21: they held images, recordings and scans readable next to the
encrypted dumps): snapshot_uploads.py --staged, lock_uploads.sh, restore_uploads.py, and their place in backup.sh
and offsite.sh."""
import hashlib
import json
import os
import runpy
import tarfile
import time

from tests.platform.test_audit8_b_deploy import World, _read, _write, needs_bash

DEPLOY = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "deploy", "docker")
# a stand-in for gpg: "encrypts" by writing ENC: before the content, so that the tests can read what went in
GPG_COPY = ('#!/usr/bin/env bash\necho "$*" >> "$SERVER/gpg.log"\n'
            'while [ $# -gt 0 ]; do case "$1" in --output) out=$2; shift ;; --encrypt) in=$2; shift ;; esac; shift; done\n'
            '{ printf "ENC:"; cat "$in"; } > "$out"\n')


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _stage(src, snap, stamp=None):
    runpy.run_path(os.path.join(DEPLOY, "snapshot_uploads.py"))["main"](str(src), str(snap), 14, staged=True, stamp=stamp)


def _manifest(stage):
    return [json.loads(line) for line in (stage / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]


def test_staged_snapshot_holds_a_manifest_and_only_new_contents(tmp_path):
    src, snap = tmp_path / "instance", tmp_path / "snap"
    _write(str(src / "uploads" / "a.png"), "A")
    _write(str(src / "uploads" / "b.png"), "B")
    _write(str(src / "voice" / "c.webm"), "A")                                  # same content as a.png: once
    _write(str(src / ".storage-backup.lock"), "")                               # the live lock is no data
    _write(str(snap / "blobs" / (_sha("B") + ".gpg")), "ENC:B")                 # B is in the store already
    _stage(src, snap, "20261021-063000")
    stage = snap / "20261021-063000.stage"
    entries = _manifest(stage)
    assert sorted(e["path"] for e in entries) == ["uploads/a.png", "uploads/b.png", "voice/c.webm"]
    assert {e["path"]: e["sha256"] for e in entries}["uploads/b.png"] == _sha("B")
    assert sorted(os.listdir(stage / "new")) == [_sha("A")]                     # only what the store lacks, once
    assert not list(snap.glob("*.part")) and not (snap / "20261021-063000").exists()   # nothing readable as a folder


def test_a_former_plain_snapshot_is_staged_with_its_time(tmp_path):
    snap = tmp_path / "snap"
    _write(str(snap / "20261012-081339" / "uploads" / "a.png"), "A")
    _stage(snap / "20261012-081339", snap, "20261012-081339")
    entries = _manifest(snap / "20261012-081339.stage")
    assert [e["path"] for e in entries] == ["uploads/a.png"]


def _locked(tmp_path):
    w = World(tmp_path, repo=False)
    _write(str(w.bin / "gpg"), GPG_COPY, 0o755)
    folder = w.path("backups", "uploads")
    key = w.path("askurious-env", "backup.asc")
    _write(str(key), "PUBLIC KEY\n")
    _write(str(w.tmp / "lock_uploads.sh"), _read("deploy", "docker", "lock_uploads.sh"), 0o755)
    return w, folder, key


def _run_lock(w, folder, key):
    return w.run(folder.as_posix(), "14", key.as_posix(), script=str(w.tmp / "lock_uploads.sh"), AS_SERVER="1")


@needs_bash
def test_lock_encrypts_removes_the_readable_and_cleans_up_after_14_days(tmp_path):
    w, folder, key = _locked(tmp_path)
    old = time.strftime("%Y%m%d-%H%M%S", time.gmtime(time.time() - 20 * 86400))
    young = time.strftime("%Y%m%d-%H%M%S", time.gmtime(time.time() - 3600))
    src = w.tmp / "instance"
    _write(str(src / "uploads" / "gone.png"), "GONE")                             # deleted in the app later
    _write(str(src / "uploads" / "a.png"), "A")
    _write(str(folder / old / "uploads" / "gone.png"), "GONE")                    # the former plain snapshot
    _stage(folder / old, folder, old)
    os.remove(src / "uploads" / "gone.png")
    _stage(src, folder, young)
    out = _run_lock(w, folder, key)
    assert out.returncode == 0, out.stdout + out.stderr
    names = sorted(os.listdir(folder))
    assert not [n for n in names if n.endswith(".stage") or n in (old, young)]   # no readable stage or folder
    assert f"{young}.manifest.gpg" in names and f"{young}.hashes" in names
    # the old snapshot is older than 14 days and not the newest: it went, and the content only it listed with it
    assert f"{old}.hashes" not in names and not (folder / "blobs" / (_sha("GONE") + ".gpg")).exists()
    blob = folder / "blobs" / (_sha("A") + ".gpg")
    assert blob.read_text() == "ENC:A"
    manifest = (folder / f"{young}.manifest.gpg").read_text(encoding="utf-8")
    assert manifest.startswith("ENC:") and "uploads/a.png" in manifest
    assert (folder / f"{young}.hashes").read_text().split() == [_sha("A")]
    assert "--recipient-file" in w.text("gpg.log")
    # a second run without a new stage changes nothing and keeps the newest even when it is old
    assert _run_lock(w, folder, key).returncode == 0 and blob.exists()


@needs_bash
def test_lock_keeps_the_stage_when_encryption_fails(tmp_path):
    w, folder, key = _locked(tmp_path)
    _write(str(w.bin / "gpg"), "#!/usr/bin/env bash\nexit 2\n", 0o755)
    src = w.tmp / "instance"
    _write(str(src / "uploads" / "a.png"), "A")
    _stage(src, folder, "20261021-063000")
    out = _run_lock(w, folder, key)
    assert out.returncode == 1 and "stage stays" in out.stderr
    assert (folder / "20261021-063000.stage" / "manifest.jsonl").exists()        # nothing lost
    assert not list(folder.glob("*.hashes")) and not list((folder / "blobs").iterdir())


def test_restore_brings_back_every_file_and_refuses_a_wrong_one(tmp_path):
    folder, target = tmp_path / "uploads", tmp_path / "restored"
    rows = [{"path": "uploads/a.png", "sha256": _sha("A"), "size": 1, "mtime": 1760000000},
            {"path": "voice/b.webm", "sha256": _sha("B"), "size": 1, "mtime": 1760000000}]
    _write(str(folder / "20261021-063000.manifest.gpg"), "ENC:" + "".join(json.dumps(r) + "\n" for r in rows))
    _write(str(folder / "blobs" / (_sha("A") + ".gpg")), "ENC:A")
    _write(str(folder / "blobs" / (_sha("B") + ".gpg")), "ENC:X")                  # tampered
    mod = runpy.run_path(os.path.join(DEPLOY, "restore_uploads.py"))
    mod["main"].__globals__["decrypt"] = lambda path, identity=None: open(path, "rb").read()[4:]
    assert mod["main"](str(folder), "20261021-063000", str(target)) == 1
    assert (target / "uploads" / "a.png").read_text() == "A" and os.stat(target / "uploads" / "a.png").st_mtime == 1760000000
    assert not (target / "voice" / "b.webm").exists()


@needs_bash
def test_backup_stages_and_converts_only_with_the_key(tmp_path):
    w = World(tmp_path, repo=False)
    folder = w.tmp / "deploy"
    for name in ("backup.sh", "compose-env.sh", "alarm.sh", "offsite.sh", "lock_uploads.sh"):
        _write(str(folder / name), _read("deploy", "docker", name), 0o755)
    _write(str(folder / "snapshot_uploads.py"), _read("deploy", "docker", "snapshot_uploads.py"))
    _write(str(w.path("backups", "uploads", "20261012-081339", "a.png")), "A")
    out = w.run(script=str(folder / "backup.sh"), AS_SERVER="1")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "--staged" not in w.text("docker.log")                                # without the key: as before
    raw = json.loads(w.path("askurious-status", "backups.json").read_text())
    assert raw["uploads"][0]["encrypted"] is False and raw["uploads"][0]["snapshots"] == 1
    _write(str(w.bin / "gpg"), GPG_COPY, 0o755)
    _write(str(w.path("askurious-env", "backup.asc")), "PUBLIC KEY\n")
    _write(str(folder / ".env"), "BACKUP_ENCRYPT_KEY=~/askurious-env/backup.asc\n")
    w.path("docker.log").unlink()
    out = w.run(script=str(folder / "backup.sh"), AS_SERVER="1")
    assert out.returncode == 0, out.stdout + out.stderr
    log = w.text("docker.log")
    assert "python - /app/instance /snap 14 --staged" in log                       # the new snapshot, staged
    assert "python - /snap/20261012-081339 /snap 14 --staged --stamp 20261012-081339" in log   # the old one converted
    assert "uploads encrypted:" in out.stdout


@needs_bash
def test_offsite_sends_an_encrypted_snapshot_as_it_is(tmp_path):
    w = World(tmp_path, repo=False)
    folder = w.tmp / "deploy"
    _write(str(folder / "offsite.sh"), _read("deploy", "docker", "offsite.sh"), 0o755)
    _write(str(w.bin / "gpg"), GPG_COPY, 0o755)
    _write(str(w.bin / "rsync"), '#!/usr/bin/env bash\ncp "${@: -2:1}"*.tar "$SERVER/" 2>/dev/null; true\n', 0o755)
    _write(str(w.path("askurious-env", "offsite.asc")), "PUBLIC KEY\n")
    _write(str(folder / ".env"), "OFFSITE_TARGET=backup@example.org:askurious\nOFFSITE_KEY=~/askurious-env/offsite.asc\n")
    up = w.path("backups", "uploads")
    _write(str(up / "20261021-063000.manifest.gpg"), "ENC:manifest")
    _write(str(up / "20261021-063000.hashes"), _sha("A") + "\n")
    _write(str(up / "blobs" / (_sha("A") + ".gpg")), "ENC:A")
    _write(str(up / "blobs" / (_sha("Z") + ".gpg")), "ENC:Z")                      # of another snapshot: not sent
    out = w.run(script=str(folder / "offsite.sh"), AS_SERVER="1")
    assert out.returncode == 0, out.stdout + out.stderr
    with tarfile.open(w.path("uploads-20261021-063000.tar")) as tar:
        assert sorted(tar.getnames()) == ["20261021-063000.manifest.gpg", f"blobs/{_sha('A')}.gpg"]
    assert "uploads" not in (w.text("gpg.log") or "")                              # not encrypted twice


@needs_bash
def test_dumps_before_a_deploy_encrypted_after_it_and_after_a_day(tmp_path):
    """Ralf 2026-10-21: the dumps before a deploy encrypted right after a deploy that went through (restart.sh calls
    "./backup.sh lock-before-deploy"); one of a deploy that stopped by the next regular run once older than a day."""
    w = World(tmp_path, repo=False)
    folder = w.tmp / "deploy"
    for name in ("backup.sh", "compose-env.sh", "alarm.sh", "offsite.sh", "lock_uploads.sh"):
        _write(str(folder / name), _read("deploy", "docker", name), 0o755)
    _write(str(folder / "snapshot_uploads.py"), _read("deploy", "docker", "snapshot_uploads.py"))
    _write(str(w.bin / "gpg"), GPG_COPY, 0o755)
    _write(str(w.path("askurious-env", "backup.asc")), "PUBLIC KEY\n")
    _write(str(folder / ".env"), "BACKUP_ENCRYPT_KEY=~/askurious-env/backup.asc\n")
    fresh = w.path("backups", "askurious-before-deploy-20261021-090000.dump")
    old = w.path("backups", "askurious-before-deploy-20261019-090000.dump")
    for path, age in ((fresh, 600), (old, 2 * 86400)):
        _write(str(path), "PGDMP-x\n")
        os.utime(path, (time.time() - age, time.time() - age))
    out = w.run(script=str(folder / "backup.sh"), AS_SERVER="1")                  # regular: only the old one
    assert out.returncode == 0, out.stdout + out.stderr
    assert fresh.exists() and not old.exists() and old.with_name(old.name + ".gpg").exists()
    out = w.run("lock-before-deploy", script=str(folder / "backup.sh"), AS_SERVER="1")
    assert out.returncode == 0, out.stdout + out.stderr
    assert not fresh.exists() and fresh.with_name(fresh.name + ".gpg").read_text().startswith("ENC:")
    restart = _read("deploy", "docker", "restart.sh")
    assert restart.rstrip().endswith('./backup.sh lock-before-deploy || ./alarm.sh "Deploy done, but the dump before it '
                                     'is not encrypted (backup.sh lock-before-deploy)" || true')


def test_plain_snapshot_leaves_the_backup_lock_out(tmp_path):
    """The live lock of the backup window is no data (deploy 2026-10-21: #1326 had lost the line, the storage guard
    proof in the new image refused the deploy)."""
    src, snap = tmp_path / "instance", tmp_path / "snap"
    _write(str(src / "audio.webm"), "A")
    _write(str(src / ".storage-backup.lock"), "")
    runpy.run_path(os.path.join(DEPLOY, "snapshot_uploads.py"))["main"](str(src), str(snap), 14)
    (stamp,) = [p for p in snap.iterdir() if p.is_dir()]
    assert (stamp / "audio.webm").exists() and not (stamp / ".storage-backup.lock").exists()
