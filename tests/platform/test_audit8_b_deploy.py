"""Code audit 2026-10-08, package B: deploy, backup and logs of the Docker server (deploy/docker).
The shell scripts run for real against a "server" in a temporary folder: ssh, docker, crontab
and openssl are small stand-ins that run the remote command locally and record what Docker was asked to do."""
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import subprocess
import sys

import pytest

from tests.helpers import bounded_run

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DOCKER = os.path.join(HERE, "deploy", "docker")


def _bash():
    """Git's bash on Windows (never WSL's), the system bash elsewhere."""
    if os.name == "nt":
        git = shutil.which("git")
        if git:
            for rel in ("../bin/bash.exe", "../../bin/bash.exe"):
                path = os.path.normpath(os.path.join(os.path.dirname(git), rel))
                if os.path.exists(path):
                    return path
        return None
    return shutil.which("bash")


BASH = _bash()
# Git's bash on Windows needs a multiple of the time under the load of parallel workers (-n 4): the time limits of
# the script runs are stretched there instead of failing a run that is only slow (audit 2026-10-13, CF-5)
BASH_SLOW = 4 if os.name == "nt" else 1
_no_bash = pytest.mark.skipif(BASH is None, reason="no bash")


def needs_bash(test):
    """A test that plays a deploy or nginx script through with bash: skipped without bash and marked `deploy`, so
    the everyday run leaves it out like the other deploy tests (tests/conftest.py; `--full` and a branch that changes
    deploy files run it; audit 2026-10-13, CF-5)."""
    return pytest.mark.deploy(_no_bash(test))


def _read(*parts):
    with open(os.path.join(HERE, *parts), encoding="utf-8") as f:
        return f.read()


def _write(path, text, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    if mode:
        os.chmod(path, mode)


# stand-ins on the PATH (bash's own PATH, set inside bash: Git's bash puts /usr/bin first)
FAKE_SSH = r'''#!/usr/bin/env bash
cmd="${@: -1}"
if [[ -n "${FAKE_TRUNCATE:-}" && "$cmd" == *sha256sum* ]]; then
  head -c 3000 | HOME="$SERVER" bash -c "cd ~; $cmd"; exit
fi
HOME="$SERVER" bash -c "cd ~; $cmd"
'''
# images are files image-<tag>.txt in the server's home: their content is the app/deployed_at.txt they were built with
FAKE_DOCKER = r'''#!/usr/bin/env bash
echo "$*" >> "$SERVER/docker.log"
if [ -n "${FAKE_UNHEALTHY:-}" ] && [[ "$*" == *"exec -T $FAKE_UNHEALTHY python"* ]]; then exit 1; fi
img() { echo "$SERVER/image-${1#askurious:}.txt"; }
case "$*" in
  *storage_guard_probe.py*)  # restart.sh: the storage guard in the new image (code review 2026-10-15, L4)
    if [ -n "${FAKE_GUARD_FAIL:-}" ]; then echo "AssertionError: flock not held" >&2; exit 1; fi
    echo '{"result": "PASS", "bash_backup_protocol_passed": true}' ;;
  *storage_backup_lock.py*)
    for value in "$@"; do case "$value" in *:/backup-control) control=${value%:/backup-control} ;; esac; done
    signal=${@: -1}; signal=${signal##*/}
    echo READY
    # never longer than 5 minutes: a backup.sh killed before its release leaves no stand-in waiting for good
    n=0; while [ ! -f "$control/$signal" ]; do sleep 0.1; n=$((n + 1)); [ "$n" -lt 3000 ] || exit 1; done
    echo gate-released >> "$SERVER/docker.log" ;;
  "run --rm --entrypoint tar askurious:latest -C /app/app/static -cf - .") tar cf - --files-from /dev/null ;;
  "image inspect askurious:latest") [ -f "$(img latest)" ] ;;
  "image inspect -f {{.Id}} askurious:latest") [ -f "$(img latest)" ] && cp "$(img latest)" "$(img id-before)" && echo id-before ;;
  tag\ *) cp "$(img "$2")" "$(img "$3")" ;;
  "run --rm --entrypoint cat askurious:previous /app/app/deployed_at.txt") cat "$(img previous)" ;;
  "compose build"*) cp "$PWD/../../app/deployed_at.txt" "$(img latest)" ;;
  *"SELECT version_num"*) echo "${FAKE_DB_VERSION:-}" ;;
  "compose ps --services --status running") printf '%b' "${FAKE_RUNNING:-}" ;;
  "compose ps -q participant-a participant-b") [ -z "${FAKE_PARTICIPANTS:-}" ] || echo running ;;
  *"FROM pg_database"*) echo "${FAKE_DATABASES:-}" ;;
  *pg_dump*) if [ -n "${FAKE_DUMP_FAIL:-}" ]; then echo "pg_dump: error: connection failed" >&2; exit 1; fi; echo "PGDMP-fake" ;;
  *"pg_restore -l"*) grep -q '^PGDMP' ;;
  *) exit 0 ;;
esac
'''
FAKES = {"ssh": FAKE_SSH, "docker": FAKE_DOCKER, "crontab": "#!/usr/bin/env bash\ncat >/dev/null 2>&1 || true\n",
         "openssl": "#!/usr/bin/env bash\necho fresh$RANDOM$RANDOM\n"}
WRAP = ('p() { if command -v cygpath >/dev/null; then cygpath -u "$1"; else echo "$1"; fi; }; '
        'export PATH="$(p "$FAKEBIN"):$PATH" SERVER="$(p "$SERVER")"; '
        'if [ -n "${AS_SERVER:-}" ]; then export HOME="$SERVER"; fi; exec bash "$(p "$SCRIPT")" "$@"')


class World:
    """A repository with a bare origin, a "server" home and the stand-ins."""

    def __init__(self, tmp, repo=True):
        self.tmp, self.repo, self.server, self.bin = tmp, tmp / "repo", tmp / "server", tmp / "bin"
        self.server.mkdir()
        for name, text in FAKES.items():
            _write(str(self.bin / name), text, 0o755)
        if repo:
            self._make_repo()

    def _make_repo(self):
        tmp = self.tmp
        r = str(self.repo)
        for rel in (".dockerignore", "config.py", "passenger_wsgi.py", "licence.md", "log.md", "requirements.lock", "requirements-postgres.txt",
                    "app/__init__.py", "migrations/env.py", "tools/check_vulnerabilities.py", "tools/vendor_versions.txt", "tools/build_static.py", "tools/storage_backup_lock.py",
                    "tests/privacy/storage_guard_probe.py", "instance_overlay/README.md",
                    "deploy/docker/requirements-docker.lock"):
            _write(os.path.join(r, rel), "# stand-in\n")
        for rel in ("compose.yaml", "compose-env.sh", "deploy-docker.sh", "restart.sh", "backup.sh", "alarm.sh", "wartung.sh",
                    "nginx/askurious.conf.template", "nginx/self-signed.sh", "nginx/participant-upstream.sh",
                    "nginx/brand-stack.sh", "nginx/servers.sh", "nginx/locations/researcher.conf",
                    "nginx/locations/participant.conf", "nginx/locations/status.conf",
                    "nginx/locations/wartung.conf"):
            os.makedirs(os.path.dirname(os.path.join(r, "deploy", "docker", rel)), exist_ok=True)
            shutil.copy(os.path.join(DOCKER, rel), os.path.join(r, "deploy", "docker", rel))
        shutil.copytree(Path(DOCKER) / "isolation", Path(r) / "deploy/docker/isolation")
        self.git("init", "-q")
        self.git("symbolic-ref", "HEAD", "refs/heads/main")  # as `init -b main`, which git before 2.28 lacks (WSL)
        self.commit("first")
        subprocess.run(["git", "init", "-q", "--bare", str(tmp / "origin.git")], check=True)
        self.git("remote", "add", "origin", str(tmp / "origin.git"))
        self.git("push", "-q", "origin", "main")

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), "-c", "user.email=t@t", "-c", "user.name=t",
                               "-c", "core.autocrlf=false", *args], check=True, capture_output=True, text=True).stdout

    def commit(self, text, push=False):
        _write(str(self.repo / "app" / "version.py"), f"V = {text!r}\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", text)
        if push:
            self.git("push", "-q", "origin", "main")

    def run(self, *args, script=None, **env):
        full = dict(os.environ, FAKEBIN=str(self.bin), SERVER=str(self.server), SKIP_TESTS="1", SKIP_OSV="1",
                    SKIP_REASON="tests of the deploy script",
                    SCRIPT=script or str(self.repo / "deploy" / "docker" / "deploy-docker.sh"))
        full.pop("PYTHON", None)
        full["DOCKER_HOST_SSH"] = "askurious-server"  # the script has no target of its own (instance_overlay/README.md)
        full.update(env)
        return bounded_run.run([BASH, "-c", WRAP, "deploy", *args], env=full, text=True, stdin=subprocess.DEVNULL,
                               timeout=int(os.environ.get("BASH_TEST_TIMEOUT", str(180 * BASH_SLOW))))

    def path(self, *parts):
        return self.server.joinpath(*parts)

    def text(self, *parts):
        p = self.path(*parts)
        return p.read_text(encoding="utf-8") if p.exists() else None


@pytest.fixture
def world(tmp_path):
    if BASH is None:
        pytest.skip("no bash")
    return World(tmp_path)


@pytest.fixture
def server(tmp_path):
    """Only the server and the stand-ins, for backup.sh."""
    if BASH is None:
        pytest.skip("no bash")
    return World(tmp_path, repo=False)


def _server_from_before(world, secret="SECRET_KEY=alt\nCOMPUTE_SECRET=c\nPARTICIPANT_TOKEN_KEY=p\n"):
    """The server as on 2026-10-08: the only .env inside the code folder."""
    _write(str(world.path("askurious", "deploy", "docker", ".env")), secret, 0o600)
    _write(str(world.path("askurious", "app", "deployed_at.txt")), "before\n")


# --- B1: an aborted upload never swaps the code folder, the .env is never lost or replaced ---------------------

@needs_bash
def test_b1_broken_upload_changes_nothing_and_the_env_moves_out(world):
    _server_from_before(world)
    out = world.run(FAKE_TRUNCATE="1")                                           # as in repro_deploy_env_loss.sh
    assert out.returncode != 0 and "live code untouched" in out.stderr
    assert world.text("askurious", "app", "deployed_at.txt") == "before\n"       # not swapped
    assert world.text("askurious", "deploy", "docker", ".env") == "SECRET_KEY=alt\nCOMPUTE_SECRET=c\nPARTICIPANT_TOKEN_KEY=p\n"
    assert not world.path("askurious.old").exists()
    out = world.run()                                                            # the next deploy keeps the secrets
    assert out.returncode == 0, out.stdout + out.stderr
    assert world.text("askurious-env", ".env") == "SECRET_KEY=alt\nCOMPUTE_SECRET=c\nPARTICIPANT_TOKEN_KEY=p\n"
    live = world.path("askurious", "deploy", "docker", ".env")
    assert live.read_text(encoding="utf-8") == "SECRET_KEY=alt\nCOMPUTE_SECRET=c\nPARTICIPANT_TOKEN_KEY=p\n"
    if os.name != "nt":                                                          # Git's bash copies instead of linking
        assert live.is_symlink() and os.readlink(live) == str(world.path("askurious-env", ".env"))
        assert stat.S_IMODE(os.stat(world.path("askurious-env")).st_mode) == 0o700


@needs_bash
def test_b1_no_new_secrets_while_an_old_env_lies_anywhere_and_only_on_request(world):
    _write(str(world.path("askurious", "app", "deployed_at.txt")), "before\n")
    _write(str(world.path("askurious.old", "deploy", "docker", ".env")), "SECRET_KEY=alt\n", 0o600)
    out = world.run(NEW_ENV="1")
    assert out.returncode != 0 and "askurious.old/deploy/docker/.env" in out.stderr
    assert not world.path("askurious-env", ".env").exists()
    assert world.text("askurious", "app", "deployed_at.txt") == "before\n"
    assert world.text("askurious.old", "deploy", "docker", ".env") == "SECRET_KEY=alt\n"
    world.path("askurious.old", "deploy", "docker", ".env").unlink()             # no .env anywhere now
    out = world.run()
    assert out.returncode != 0 and "NEW_ENV=1" in out.stderr
    assert not world.path("askurious-env", ".env").exists()
    assert world.text("askurious", "app", "deployed_at.txt") == "before\n"
    out = world.run(NEW_ENV="1")
    assert out.returncode == 0, out.stdout + out.stderr
    env = world.text("askurious-env", ".env")
    assert re.search(r"^SECRET_KEY=fresh\d+$", env, re.M) and re.search(r"^COMPUTE_SECRET=fresh\d+$", env, re.M)
    # the keys that keep the two sides apart (audit 2026-10-22, E-01)
    for name in ("PARTICIPANT_TOKEN_KEY", "RESEARCHER_SESSION_KEY", "TOKEN_KEY"):
        assert re.search(rf"^{name}=fresh\d+$", env, re.M), name


# --- B8: the image before stays as askurious:previous, rollback puts image and code folder back together -------

@needs_bash
def test_b8_previous_image_and_rollback(world):
    _server_from_before(world)
    assert world.run().returncode == 0
    first = world.text("askurious", "app", "deployed_at.txt")
    world.commit("second", push=True)
    assert world.run().returncode == 0
    assert world.text("askurious-env", ".env") == "SECRET_KEY=alt\nCOMPUTE_SECRET=c\nPARTICIPANT_TOKEN_KEY=p\n"   # B1: a second deploy too
    log = world.text("docker.log")
    assert log.rindex("tag askurious:latest askurious:previous") < log.rindex("compose build")
    assert world.text("image-previous.txt") == first
    out = world.run("rollback")
    assert out.returncode == 0, out.stdout + out.stderr
    assert world.text("askurious", "app", "deployed_at.txt") == first == world.text("image-latest.txt")
    log = world.text("docker.log")
    after = log[log.rindex("tag askurious:previous askurious:latest"):]          # restart.sh --no-build: no new image
    assert "compose build" not in after and "compose up -d --no-deps participant-a participant-b" not in after
    assert after.index("compose up -d --no-deps participant-a") < after.index("compose up -d --no-deps participant-b")
    again = world.run("rollback")                                                # no pair left: nothing changes
    assert again.returncode != 0 and "nothing changed" in again.stderr
    assert world.text("askurious", "app", "deployed_at.txt") == first


def test_b8_docker_waits_for_gunicorn_before_it_kills():
    compose = _read("deploy", "docker", "compose.yaml")
    anchor = compose[compose.index("x-app: &app"):compose.index("services:")]
    assert "stop_grace_period: 35s" in anchor                                    # participant and researcher
    assert compose.count("<<: *app\n") == 5                # participant-a, researcher, housekeeping, the brand's two
    assert re.search(r'"--graceful-timeout", "30"', compose)


# --- B12: Python in a Windows checkout, only what is on origin/main goes up ------------------------------------

@needs_bash
def test_b12_only_origin_main_goes_up_and_the_windows_python_is_found(world):
    _server_from_before(world)
    world.commit("local only")
    out = world.run()
    assert out.returncode != 0 and "not in origin/main" in out.stdout
    # with the exception switch it goes on: tests and OSV with .venv/Scripts/python.exe (here one that reports a
    # vulnerability, so that the deploy stops before the upload)
    calls = world.tmp / "calls.txt"
    _write(str(world.repo / ".venv" / "Scripts" / "python.exe"),
           f'#!/usr/bin/env bash\necho "$*" >> "{calls.as_posix()}"\n[[ "$*" != *check_vulnerabilities* && "$*" != *green_run* ]]\n', 0o755)
    out = world.run(ALLOW_UNMERGED="1", SKIP_TESTS="", SKIP_OSV="")
    assert out.returncode != 0 and "Known vulnerabilities" in out.stdout
    green, tests, osv = calls.read_text(encoding="utf-8").splitlines()
    assert "tools/green_run.py" in green                         # no green full run of this state: the tests run
    assert tests.startswith("-m pytest")
    assert "--full" in tests.split()  # the slow and the deploy tests too (code review 2026-10-15, L1)
    assert "check_vulnerabilities.py" in osv and "deploy/docker/requirements-docker.lock" in osv and "deploy/docker/requirements-static.lock" in osv
    assert osv.endswith("/requirements-dev.txt")                  # pytest and the tools of the tests too (DP-20)
    assert world.text("askurious", "app", "deployed_at.txt") == "before\n" and not world.path("askurious.new").exists()


@needs_bash
def test_a_state_that_ran_green_in_full_is_not_tested_again(world):
    """Test suite concept 2026-10-18, F: tools/green_run.py knows a green --full run of this git tree; the deploy then
    skips its own full run, FULL_TESTS=1 runs it anyway."""
    _server_from_before(world)
    world.commit("local only")
    calls = world.tmp / "calls.txt"
    _write(str(world.repo / ".venv" / "Scripts" / "python.exe"),
           f'#!/usr/bin/env bash\necho "$*" >> "{calls.as_posix()}"\n[[ "$*" != *check_vulnerabilities* ]]\n', 0o755)
    out = world.run(ALLOW_UNMERGED="1", SKIP_TESTS="", SKIP_OSV="")
    assert out.returncode != 0 and "not run again" in out.stdout
    lines = calls.read_text(encoding="utf-8").splitlines()
    assert "tools/green_run.py" in lines[0] and not [line for line in lines if line.startswith("-m pytest")]
    calls.unlink()
    world.run(ALLOW_UNMERGED="1", SKIP_TESTS="", SKIP_OSV="", FULL_TESTS="1")
    assert calls.read_text(encoding="utf-8").splitlines()[0].startswith("-m pytest")


# --- B3: a failed dump ends with exit 1 and leaves nothing behind ---------------------------------------------

def _backup(world, **env):
    script = world.tmp / "deploy" / "backup.sh"
    _write(str(script), _read("deploy", "docker", "backup.sh"), 0o755)
    _write(str(script.parent / "compose-env.sh"), _read("deploy", "docker", "compose-env.sh"))
    _write(str(script.parent / "snapshot_uploads.py"), _read("deploy", "docker", "snapshot_uploads.py"))
    return world.run(script=str(script), AS_SERVER="1", **env)


@needs_bash
def test_b3_failed_dump_is_an_error_and_leaves_no_part(server):
    world = server
    backups = world.path("backups")
    _write(str(backups / "askurious-20261001-0230.dump"), "PGDMP-old\n")
    out = _backup(world, FAKE_DUMP_FAIL="1")
    assert out.returncode == 1 and "dump failed" in out.stderr
    assert sorted(n for n in os.listdir(backups) if "dump" in n) == ["askurious-20261001-0230.dump"]


@needs_bash
def test_b3_good_dump_is_checked_kept_private_and_old_ones_go(server):
    world = server
    backups = world.path("backups")
    # every four hours, 14 days (Ralf 2026-10-12): 90 young ones (the newest 84 stay) and 2 older than 14 days
    now = __import__("time").time()
    for i in range(92):
        path = backups / f"askurious-202609{i // 6 + 1:02d}-{i % 6:02d}30.dump"
        _write(str(path), "PGDMP-old\n")
        age = (20 + i) * 86400 if i < 2 else (92 - i) * 3600
        os.utime(path, (now - age, now - age))
    _write(str(backups / "askurious-20260930-0230.dump.part"), "broken")         # a leftover of a broken run
    out = _backup(world)
    assert out.returncode == 0, out.stdout + out.stderr
    names = sorted(n for n in os.listdir(backups) if "dump" in n)
    assert len(names) == 84 and not any(n.endswith(".part") for n in names)
    assert "askurious-20260901-0030.dump" not in names and "askurious-20260901-0130.dump" not in names
    new = [n for n in names if not n.startswith("askurious-202609")]
    assert len(new) == 1 and (backups / new[0]).read_text(encoding="utf-8") == "PGDMP-fake\n"
    assert "pg_restore -l" in world.text("docker.log")
    if os.name != "nt":
        assert stat.S_IMODE(os.stat(backups / new[0]).st_mode) == 0o600
        # also what older runs left readable for the group, and the folder (audit 2026-10-09, BT11)
        assert all(stat.S_IMODE(os.stat(backups / n).st_mode) == 0o600 for n in names)
        assert stat.S_IMODE(os.stat(backups / "uploads").st_mode) == 0o700
        assert stat.S_IMODE(os.stat(backups).st_mode) == 0o700


@needs_bash
def test_backup_snapshots_the_uploads_of_a_running_brand(server):
    """Abgleich 2026-10-15, T08-N-1: since #990 backup.sh removed the line breaks from the list of running services
    (tr -d with a line break instead of \\r), so it never found brand-researcher and left a brand's uploads out
    without an error."""
    out = _backup(server, FAKE_RUNNING=r"researcher\nbrand-researcher\nparticipant-a\n")
    assert out.returncode == 0, out.stdout + out.stderr
    log = server.text("docker.log")
    assert re.search(r"-v \S*backups/uploads:/snap researcher python - ", log)
    assert re.search(r"-v \S*backups/uploads-brand:/snap brand-researcher python - ", log)
    out = _backup(server, FAKE_RUNNING=r"researcher\n")
    assert out.returncode == 0 and server.text("docker.log").count("uploads-brand:/snap") == 1   # no brand: none


# --- B2: no access log with IP addresses, logs with a size limit ----------------------------------------------

def _server_block(tmp_path, host):
    """The server block of one name in the configuration nginx reads (nginx/servers.sh writes one per name)."""
    from tests.nginx_conf import render, server_blocks

    return server_blocks(render(tmp_path))[host]


@needs_bash
def test_b2_no_access_log_with_addresses(tmp_path):
    from tests.nginx_conf import render, server_blocks

    conf = render(tmp_path)
    blocks = server_blocks(conf)
    head = conf[:conf.index("server {")]
    assert re.search(r"^access_log off;$", head, re.M)                            # the image's "main" log ends here
    assert re.search(r"^error_log \S+ crit;", head, re.M)
    formats = re.findall(r"log_format \w+ '([^;]*)';", conf)
    assert formats and not any("$remote_addr" in f or "$request " in f or "$request_uri" in f or "$args" in f
                               or "$http_x_forwarded_for" in f for f in formats)
    for host in ("ask.askurious.de", "ask.polltergeist.de", "ask.queryous.de"):
        assert re.search(r"^\s*access_log off;", blocks[host], re.M)
    for host in ("askurious.de", "polltergeist.de", "status.askurious.de", "status.polltergeist.de"):
        assert re.search(r"access_log \S+ noaddress if=\$failed;", blocks[host])
    for line in re.findall(r"^\s*access_log .*$", conf, re.M):
        assert line.strip().startswith("access_log off;") or " noaddress " in line


def test_b2_gunicorn_without_access_log_and_every_log_limited():
    compose, dockerfile = _read("deploy", "docker", "compose.yaml"), _read("deploy", "docker", "Dockerfile")
    assert "--access-logfile" not in compose and "--access-logfile" not in dockerfile
    assert re.search(r'x-logging: &logging\n  driver: json-file\n  options:\n    max-size: "20m"\n    max-file: "3"', compose)
    services = compose[compose.index("services:"):compose.index("\nvolumes:")]
    names = re.findall(r"^  ([\w-]+):(?: &brand-researcher)?$", services, re.M)
    assert set(names) == {"db", "researcher", "compute", "status", "nginx", "certbot", "whisper",
                          "brand-participant", "brand-researcher", "brand-compute", "housekeeping", "brand-housekeeping"}  # the last three: profile "brand"
    # the two participants' containers (one template, the second a copy of the first): logging from x-app
    assert "\n  participant-a: &participant\n    <<: *app\n" in services
    assert "\n  participant-b: *participant\n" in services
    for name, body in zip(names, re.split(r"^  [\w-]+:(?: &brand-researcher)?$", services, flags=re.M)[1:]):
        assert "logging: *logging" in body or "<<: *app" in body or "<<: *brand-researcher" in body, name
    assert "logging: *logging" in compose[compose.index("x-app: &app"):compose.index("services:")]


# --- H7: robots.txt and the tab icon on the participants' address ---------------------------------------------

@needs_bash
def test_h7_robots_and_favicon_go_to_the_participants_container(tmp_path):
    block = _server_block(tmp_path, "ask.askurious.de")
    for path in ("/robots.txt", "/favicon.ico"):
        assert re.search(r"location = %s \{ proxy_pass http://\$participant_pool; \}" % re.escape(path), block)
        assert block.index(path) < block.index("location / { return 404; }")


# --- B11: fixed versions with hashes, OSV before the build ----------------------------------------------------

def test_b11_image_installs_only_locked_packages():
    dockerfile = _read("deploy", "docker", "Dockerfile")
    installs = re.findall(r"pip install[^\n]*", dockerfile)
    assert installs and all("--require-hashes" in i and "--no-deps" in i for i in installs)
    assert "-r requirements-docker.lock" in installs[0] and "COPY requirements.lock deploy/docker/requirements-docker.lock" in dockerfile
    lock = _read("deploy", "docker", "requirements-docker.lock")
    for name in ("gunicorn", "packaging"):
        assert re.search(r"^%s==\S+ \\\n    --hash=sha256:[0-9a-f]{64}" % name, lock, re.M), name
    wanted = re.findall(r"^([a-z]+)==(\S+)$", _read("deploy", "docker", "requirements-docker.txt"), re.M)
    assert set(wanted) == set(re.findall(r"^([a-z]+)==(\S+) \\$", lock, re.M))
    compose = _read("deploy", "docker", "compose.yaml")
    assert ":latest" not in re.sub(r"askurious:latest", "", compose)               # only our own image floats
    assert re.search(r"image: certbot/certbot:v\d+\.\d+\.\d+", compose)


def test_b11_deploy_checks_vulnerabilities_of_both_locks_before_the_upload():
    script = _read("deploy", "docker", "deploy-docker.sh")
    osv = script.index("check_vulnerabilities.py")
    assert osv < script.index("git -C \"$HERE\" archive")
    assert "requirements-docker.lock" in script[osv:osv + 200]
    sys.path.insert(0, os.path.join(HERE, "tools"))
    try:
        import check_vulnerabilities
    finally:
        sys.path.pop(0)
    assert ("gunicorn", "23.0.0") in check_vulnerabilities.pins(os.path.join(DOCKER, "requirements-docker.lock"))




# --- rename to Askurious (2026-10-09): a server still under ~/queryous is renamed once, never deployed next to it ---

@needs_bash
def test_rename_a_plain_deploy_refuses_a_server_still_under_the_old_name(world):
    _write(str(world.path("queryous", "deploy", "docker", "compose.yaml")), "name: queryous\n")
    out = world.run()
    assert out.returncode != 0 and "deploy-docker.sh rename" in out.stdout + out.stderr
    assert not world.path("askurious").exists() and not world.path("askurious-env").exists()
    assert "compose" not in (world.text("docker.log") or "")                     # nothing started


@needs_bash
def test_rename_stops_when_the_new_names_exist_already(world):
    _write(str(world.path("queryous", "deploy", "docker", "compose.yaml")), "name: queryous\n")
    _write(str(world.path("askurious-env", ".env")), "SECRET_KEY=x\n", 0o600)
    out = world.run("rename")
    assert out.returncode != 0 and "exists already" in out.stderr
    assert "stop" not in (world.text("docker.log") or "")                        # the old stack runs on


@needs_bash
def test_rename_back_needs_the_old_stack(world):
    out = world.run("rename-back")
    assert out.returncode != 0 and "nothing to go back to" in out.stderr
