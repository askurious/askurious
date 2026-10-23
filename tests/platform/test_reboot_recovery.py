"""After a reboot of the Docker server (Ralf, 2026-10-17): every container comes back by itself, a restart.lock left
by a reboot during a deploy or the maintenance window no longer blocks the next one, and after-boot.sh tells Slack
which containers came back. Played through with bash and stand-ins for docker and alarm.sh."""
import os
import re
import time

from tests.helpers import bounded_run
from tests.platform.test_audit8_b_deploy import BASH, BASH_SLOW, _read, needs_bash


def test_every_service_restarts_by_itself():
    compose = _read("deploy", "docker", "compose.yaml")
    services = compose[compose.index("\nservices:\n"):compose.index("\nvolumes:\n")]
    blocks = re.split(r"\n  (?=[a-z][a-z-]*:)", services)[1:]
    for block in blocks:
        name = block.split(":", 1)[0]
        if "<<: *app" in block or block.startswith("participant-b:") or "<<: *brand-researcher" in block:
            continue  # restart: unless-stopped comes with x-app or the service they copy
        if "\n    image:" not in block and "\n    build:" not in block:
            continue  # a one-off job (migrate) is not meant to come back
        assert "restart: unless-stopped" in block, name


def test_deploy_puts_after_boot_into_the_crontab():
    deploy = _read("deploy", "docker", "deploy-docker.sh")
    assert "@reboot \\$HOME/askurious/deploy/docker/after-boot.sh" in deploy


def _lock_check(tmp_path, booted, locked):
    """restart.sh's lock handling with a boot time of `booted` and a lock dated `locked` (None: no lock)."""
    script = _read("deploy", "docker", "restart.sh")
    part = script[script.index('RESTART_LOCK="$HOME'):]
    part = part[:part.index("\ntrap ")]
    stat = tmp_path / "stat"
    stat.write_text(f"cpu  1 2 3\nbtime {booted}\n", encoding="utf-8", newline="\n")
    part = part.replace("/proc/stat", stat.as_posix())
    home = tmp_path / "home"
    (home / "askurious-env").mkdir(parents=True)
    lock = home / "askurious-env" / "restart.lock"
    if locked is not None:
        lock.mkdir()
        os.utime(lock, (locked, locked))
    out = bounded_run.run([BASH, "-c", part + "\necho taken"], cwd=tmp_path, text=True, timeout=60 * BASH_SLOW,
                          env={**os.environ, "HOME": str(home)})
    return out


@needs_bash
def test_a_lock_from_before_the_boot_goes_by_itself(tmp_path):
    now = int(time.time())
    out = _lock_check(tmp_path, booted=now - 60, locked=now - 3600)
    assert out.returncode == 0 and "taken" in out.stdout, out.stderr
    assert "before the server's last boot" in out.stderr


@needs_bash
def test_a_lock_since_the_boot_still_blocks(tmp_path):
    now = int(time.time())
    out = _lock_check(tmp_path, booted=now - 3600, locked=now - 60)
    assert out.returncode == 1 and "taken" not in out.stdout
    assert "Another restart is running" in out.stderr


@needs_bash
def test_no_lock_is_taken_as_before(tmp_path):
    out = _lock_check(tmp_path, booted=int(time.time()) - 60, locked=None)
    assert out.returncode == 0 and "taken" in out.stdout, out.stderr


def _after_boot(tmp_path, ps_lines):
    work = tmp_path / "deploy" / "docker"
    work.mkdir(parents=True)
    for name in ("after-boot.sh", "compose-env.sh"):
        (work / name).write_text(_read("deploy", "docker", name), encoding="utf-8", newline="\n")
    (work / "alarm.sh").write_text('#!/usr/bin/env bash\necho "SLACK: $*"\n', encoding="utf-8", newline="\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ps = "\n".join(ps_lines)
    docker = ('#!/usr/bin/env bash\n'
              'case "$1 $2" in\n'
              '  "info "*) exit 0 ;;\n'
              f'  "compose ps") if [ "${{3:-}}" = -q ]; then printf "%s\\n" {" ".join(f"id{i}" for i in range(len(ps_lines)))};'
              f' else printf "%s\\n" "{ps}"; fi ;;\n'
              'esac\n')
    (bin_dir / "docker").write_text(docker, encoding="utf-8", newline="\n")
    for name in ("after-boot.sh", "alarm.sh"):
        os.chmod(work / name, 0o755)
    os.chmod(bin_dir / "docker", 0o755)
    home = tmp_path / "home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home), "AFTER_BOOT_WAIT": "0", "AFTER_BOOT_STEP": "1",
           "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", "")}
    return bounded_run.run([BASH, (work / "after-boot.sh").as_posix()], cwd=work, text=True,
                           timeout=60 * BASH_SLOW, env=env)


@needs_bash
def test_after_boot_reports_all_back(tmp_path):
    out = _after_boot(tmp_path, ["db running healthy", "nginx running ", "certbot running "])
    assert out.returncode == 0, out.stderr
    assert "SLACK: Server restarted: all 3 containers run and are healthy" in out.stdout


@needs_bash
def test_after_boot_names_what_did_not_come_back(tmp_path):
    out = _after_boot(tmp_path, ["db running healthy", "researcher running unhealthy", "compute exited "])
    assert out.returncode == 1
    assert "Not running or not healthy" in out.stdout
    assert "researcher (running, unhealthy)" in out.stdout and "compute (exited)" in out.stdout
    assert "db (" not in out.stdout
