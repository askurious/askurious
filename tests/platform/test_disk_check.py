"""deploy/docker/disk-check.sh: a message to Slack when a file system of the server has less than DISK_MIN_FREE_PERCENT
free (default 20), repeated only after DISK_ALARM_HOURS, one message once there is space again; it never deletes
anything, and the deploy puts it into the crontab. Played through with bash and stand-ins for df, docker and
alarm.sh (operations audit 2026-11-11: no alarm on the disk before)."""
import os

from tests.helpers import bounded_run
from tests.platform.test_audit8_b_deploy import BASH, BASH_SLOW, _read, needs_bash


def _run(tmp_path, free_percent, env=None, dotenv=""):
    """disk-check.sh with df reporting `free_percent` % free on every path; returns (result, Slack messages)."""
    work = tmp_path / "deploy" / "docker"
    work.mkdir(parents=True, exist_ok=True)
    (work / "disk-check.sh").write_text(_read("deploy", "docker", "disk-check.sh"), encoding="utf-8", newline="\n")
    slack = tmp_path / "slack.txt"
    (work / "alarm.sh").write_text(f'#!/usr/bin/env bash\necho "$*" >> "{slack.as_posix()}"\n', encoding="utf-8",
                                   newline="\n")
    (work / ".env").write_text(dotenv, encoding="utf-8", newline="\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    size = 100 * 1024 * 1024  # 100 GB in KB
    avail = size * free_percent // 100
    (bin_dir / "df").write_text(
        '#!/usr/bin/env bash\n'
        'echo "Filesystem 1024-blocks Used Available Capacity Mounted on"\n'
        f'echo "/dev/root {size} {size - avail} {avail} {100 - free_percent}% /"\n', encoding="utf-8", newline="\n")
    (bin_dir / "docker").write_text('#!/usr/bin/env bash\necho /var/lib/docker\n', encoding="utf-8", newline="\n")
    for f in (bin_dir / "df", bin_dir / "docker", work / "disk-check.sh", work / "alarm.sh"):
        os.chmod(f, 0o755)
    home = tmp_path / "home"
    (home / "backups").mkdir(parents=True, exist_ok=True)
    # the stand-ins first inside bash: Git's bash on Windows puts its own /usr/bin (with df) before an inherited PATH
    run = f'PATH="$(cygpath -u "{bin_dir}" 2>/dev/null || echo "{bin_dir.as_posix()}"):$PATH" exec bash "{(work / "disk-check.sh").as_posix()}"'
    out = bounded_run.run([BASH, "-c", run], cwd=work, text=True, timeout=60 * BASH_SLOW,
                          env={**os.environ, "HOME": str(home), **(env or {})})
    lines = slack.read_text(encoding="utf-8").splitlines() if slack.exists() else []
    return out, lines


@needs_bash
def test_enough_space_says_nothing(tmp_path):
    out, slack = _run(tmp_path, 35)
    assert out.returncode == 0, out.stderr
    assert slack == []


@needs_bash
def test_below_twenty_percent_alarms_once_per_file_system(tmp_path):
    out, slack = _run(tmp_path, 12)
    assert out.returncode == 1
    assert len(slack) == 1, slack  # /, ~/backups and Docker's folder lie on one file system here: named once
    assert "Disk space short (below 20 % free): /: 12 % free (12 GB of 100 GB)" in slack[0]
    assert "Nothing was deleted" in slack[0]


@needs_bash
def test_the_alarm_repeats_only_after_the_hours_and_says_when_space_is_back(tmp_path):
    _run(tmp_path, 12)
    out, slack = _run(tmp_path, 11)              # still short, within 24 hours: no second message
    assert out.returncode == 1 and len(slack) == 1
    out, slack = _run(tmp_path, 11, env={"DISK_ALARM_HOURS": "0"})
    assert len(slack) == 2                       # the repeat once the hours are over
    out, slack = _run(tmp_path, 40)
    assert out.returncode == 0 and slack[-1] == "Disk space fine again: every file system has at least 20 % free."
    out, slack = _run(tmp_path, 40)
    assert len(slack) == 3                       # and only once


@needs_bash
def test_the_threshold_comes_from_the_env_file(tmp_path):
    out, slack = _run(tmp_path, 25, dotenv="DISK_MIN_FREE_PERCENT=30\n")
    assert out.returncode == 1 and "below 30 % free" in slack[0]
    out, slack = _run(tmp_path / "b", 25, dotenv="DISK_MIN_FREE_PERCENT=abc\n")
    assert out.returncode == 2 and slack == []


def test_it_only_reports_and_the_deploy_runs_it():
    script = _read("deploy", "docker", "disk-check.sh")
    code = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))
    for word in ("prune", "rm -r", "docker rm", "docker volume", "find "):
        assert word not in code, word
    assert "rm -f \"$state\"" in code  # the only file it removes: its own note of the last alarm
    deploy = _read("deploy", "docker", "deploy-docker.sh")
    assert "*/10 * * * * \\$HOME/askurious/deploy/docker/disk-check.sh" in deploy
