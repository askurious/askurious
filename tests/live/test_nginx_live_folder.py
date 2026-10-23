"""nginx mounts its files from a folder no deploy moves or deletes (2026-10-10, after the deploy of 8f803b0f): from the
code folder, a mount held the folder of the deploy that last started nginx, renamed by the next deploy and deleted by
the one after, and the reload of the rolling restart failed. compose.yaml mounts from nginx-live (a link to
~/askurious-env/nginx), restart.sh writes nginx/ into it and creates nginx anew when it still sees another folder."""
import os
import re

from tests.platform.test_audit8_b_deploy import FAKE_DOCKER, _read, _server_from_before, _write, needs_bash, world  # noqa: F401

# the stand-in as in test_audit8_b_deploy, plus a running nginx: FAKE_NGINX=live reads the folder nginx-live, as a
# nginx does that mounts it; FAKE_NGINX=old reads nothing, as one that holds a deleted code folder
NGINX_DOCKER = FAKE_DOCKER.replace('case "$*" in\n', r'''case "$*" in
  "compose ps -q nginx") [ -n "${FAKE_NGINX:-}" ] && echo nginx0 ;;
  "compose exec -T nginx cat /etc/nginx/locations/.mount-check")
    [ "${FAKE_NGINX:-}" = live ] && cat "$PWD/nginx-live/locations/.mount-check" ;;
''', 1)


def _nginx_world(world):
    _write(str(world.bin / "docker"), NGINX_DOCKER, 0o755)
    _server_from_before(world)
    return world


def test_compose_mounts_every_nginx_file_from_the_live_folder():
    compose = _read("deploy", "docker", "compose.yaml")
    nginx = compose[compose.index("\n  nginx:\n"):]
    nginx = nginx[:nginx.index("\n  brand-")]
    binds = re.findall(r"^\s*- (\./[^:]+):", nginx, re.M)
    assert len(binds) == 6 and all(b.startswith("./nginx-live/") for b in binds)
    assert "./nginx/" not in compose


@needs_bash
def test_deploy_fills_the_live_folder_and_keeps_nginx_that_mounts_it(world):
    world = _nginx_world(world)
    out = world.run(FAKE_NGINX="live")
    assert out.returncode == 0, out.stdout + out.stderr
    live = world.path("askurious-env", "nginx")
    for rel in ("askurious.conf.template", "servers.sh", "locations/participant.conf"):
        assert (live / rel).read_text(encoding="utf-8") == _read("deploy", "docker", "nginx", *rel.split("/"))
    link = world.path("askurious", "deploy", "docker", "nginx-live")
    if os.name != "nt":                                                          # Git's bash copies instead of linking
        assert link.is_symlink() and os.readlink(link) == str(live)
    log = world.text("docker.log")
    assert "--force-recreate nginx" not in log
    assert "compose up -d --no-deps nginx" in log


@needs_bash
def test_nginx_on_another_folder_is_created_anew_before_the_participants(world):
    world = _nginx_world(world)
    out = world.run(FAKE_NGINX="old")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "nginx created anew" in out.stdout
    log = world.text("docker.log")
    assert log.index("compose up -d --no-deps --force-recreate nginx") < log.index("compose up -d --no-deps participant-a")


@needs_bash
def test_restart_writes_into_the_files_nginx_holds_and_drops_the_rest(world):
    world = _nginx_world(world)
    assert world.run(FAKE_NGINX="live").returncode == 0
    live = world.path("askurious-env", "nginx")
    if os.name == "nt":                         # Git's bash copied the folder: the one nginx-live names is the copy
        live = world.path("askurious", "deploy", "docker", "nginx-live")
    held = live / "locations" / "participant.conf"
    inode = os.stat(held).st_ino
    _write(str(live / "locations" / "gone.conf"), "# from a deploy before\n")
    conf = world.repo / "deploy" / "docker" / "nginx" / "locations" / "participant.conf"
    _write(str(conf), conf.read_text(encoding="utf-8") + "# changed\n")
    world.commit("second", push=True)
    out = world.run(FAKE_NGINX="live")
    assert out.returncode == 0, out.stdout + out.stderr
    if os.name == "nt":
        live = world.path("askurious", "deploy", "docker", "nginx-live")
        held = live / "locations" / "participant.conf"
    assert held.read_text(encoding="utf-8").endswith("# changed\n")
    if os.name != "nt":
        assert os.stat(held).st_ino == inode                                     # the same file, written into
    assert not (live / "locations" / "gone.conf").exists()
    assert (live / "locations" / ".mount-check").exists()
