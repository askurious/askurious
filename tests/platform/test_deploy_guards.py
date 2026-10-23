"""Guards of deploy/docker/deploy-docker.sh from the failed deploy of 2026-10-13: a HEAD behind origin/main (or
without what the server runs) took back a migration others had deployed and the researchers' container did not
start; a missing SSH alias showed up as "still runs as ~/queryous". Played through with the stand-ins of
test_audit8_b_deploy.py."""
from tests.platform.test_audit8_b_deploy import _server_from_before, _write, needs_bash, world  # noqa: F401
from tests.platform.test_audit8_b_deploy import _read


@needs_bash
def test_backup_helper_reaches_the_uploaded_build_context(world):
    for path in (".dockerignore", "tools/storage_backup_lock.py"):
        _write(str(world.repo / path), _read(*path.split("/")))
    world.commit("real backup build inputs", push=True)
    _server_from_before(world)
    out = world.run()
    assert out.returncode == 0, out.stdout + out.stderr
    assert world.text("askurious", "tools", "storage_backup_lock.py") == _read("tools", "storage_backup_lock.py")
    assert "!tools/storage_backup_lock.py" in world.text("askurious", ".dockerignore").splitlines()


@needs_bash
def test_no_connection_is_named_as_such(world):
    _write(str(world.bin / "ssh"), '#!/usr/bin/env bash\necho "ssh: Could not resolve hostname x" >&2\nexit 255\n', 0o755)
    out = world.run()
    assert out.returncode != 0 and "No SSH connection to askurious-server" in out.stderr
    assert "~/.ssh/config" in out.stderr and "still runs as" not in out.stdout + out.stderr
    out = world.run(DOCKER_HOST_SSH="someone@somewhere")
    assert "No SSH connection to someone@somewhere" in out.stderr and "~/.ssh/config" not in out.stderr


@needs_bash
def test_a_head_behind_origin_main_stays_here(world):
    _server_from_before(world)
    world.commit("merged by someone else", push=True)
    world.git("reset", "-q", "--hard", "HEAD~1")
    out = world.run()
    assert out.returncode != 0 and "is behind origin/main" in out.stdout
    assert world.text("askurious", "app", "deployed_at.txt") == "before\n" and not world.path("askurious.new").exists()


@needs_bash
def test_what_the_server_runs_must_be_in_head(world):
    _server_from_before(world)
    world.git("checkout", "-q", "-b", "elsewhere")
    world.commit("deployed from another checkout")
    elsewhere = world.git("rev-parse", "--short=8", "HEAD").strip()
    world.git("checkout", "-q", "main")
    _write(str(world.path("askurious", "app", "deployed_at.txt")), f"2026-10-13T18:00:00Z\n{elsewhere}\n")
    out = world.run()
    assert out.returncode != 0 and f"The server runs {elsewhere}, which HEAD" in out.stdout
    assert not world.path("askurious.new").exists()
    # what the server runs is in HEAD: on to the upload
    _write(str(world.path("askurious", "app", "deployed_at.txt")),
           "2026-10-13T18:00:00Z\n" + world.git("rev-parse", "--short=8", "HEAD").strip() + "\n")
    assert "The server runs" not in world.run().stdout


@needs_bash
def test_the_storage_guard_runs_in_the_new_image_before_anything_restarts(world):
    """Code review 2026-10-15, L4: tests/privacy/test_storage_guard.py is skipped on Windows, from where the deploys
    go; restart.sh runs its real flock proof in the image just built, even with SKIP_TESTS=1 (World's default)."""
    _server_from_before(world)
    out = world.run()
    assert out.returncode == 0, out.stdout + out.stderr
    assert "storage guard checked in the new image" in out.stdout
    log = world.text("docker.log")
    probe = log.index("storage_guard_probe.py")
    assert log.rindex("compose build", 0, probe) < probe < log.index("compose up")
    call = log[log.rindex("\n", 0, probe) + 1:log.index("compose up")]                # the command spans two lines
    assert "--network none" in call and "askurious:latest" in call and "timeout 120 python" in call
    assert world.path("askurious", "tests", "privacy", "storage_guard_probe.py").exists()   # uploaded with the code


@needs_bash
def test_a_failing_storage_guard_stops_the_deploy_and_keeps_the_image_from_before(world):
    _server_from_before(world)
    assert world.run().returncode == 0
    first = world.text("image-latest.txt")
    world.commit("second", push=True)
    _write(str(world.server / "docker.log"), "")
    out = world.run(FAKE_GUARD_FAIL="1")
    assert out.returncode != 0 and "storage guard" in out.stderr and "flock not held" in out.stderr
    log = world.text("docker.log")
    assert "compose up" not in log and "tag id-before askurious:latest" in log
    assert world.text("image-latest.txt") == first                     # the untested image is not askurious:latest


@needs_bash
def test_a_server_that_needs_its_overlay_refuses_a_deploy_without_it(world, tmp_path):
    """After the operator's legal texts moved into its private repository (Ralf, 2026-10-19), a deploy without them
    would put the public code's neutral imprint on askurious.de: the server's marker stops it before the upload."""
    _server_from_before(world)
    _write(str(world.path("askurious-env", "overlay-required")), "")
    out = world.run()
    assert out.returncode != 0 and "set INSTANCE_OVERLAY_SRC" in out.stderr
    assert world.text("askurious", "app", "deployed_at.txt") == "before\n"          # nothing went up
    overlay = tmp_path / "overlay"
    _write(str(overlay / "templates" / "public" / "_owner.html"), "Betreiber der Instanz\n")
    out = world.run(INSTANCE_OVERLAY_SRC=str(overlay))
    assert out.returncode == 0, out.stdout + out.stderr
    assert world.text("askurious", "instance_overlay", "templates", "public", "_owner.html") == "Betreiber der Instanz\n"
