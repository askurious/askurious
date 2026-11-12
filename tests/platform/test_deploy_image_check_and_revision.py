"""Every tool the scripts of the server call under ../../tools/ goes up with the deploy (deploy-docker.sh, the git
archive): tools/image_digests.py did not, so the weekly check of the pinned base images never ran and said nothing;
maintenance.sh now says so in Slack if it is missing. And the image carries the commit it was built from as its label
org.opencontainers.image.revision (compose.yaml build args from compose-env.sh) (operations audit 2026-11-11)."""
import re

from tests.platform.test_audit8_b_deploy import _read

SCRIPTS = ("maintenance.sh", "backup.sh", "restart.sh", "after-boot.sh", "wartung.sh", "disk-check.sh")


def test_every_tool_the_server_scripts_call_goes_up():
    deploy = _read("deploy", "docker", "deploy-docker.sh")
    archive = deploy[deploy.index("git -C \"$HERE\" archive"):]
    archive = archive[:archive.index("> \"$ARCHIVE\"")]
    for name in SCRIPTS:
        try:
            script = _read("deploy", "docker", name)
        except FileNotFoundError:
            continue
        for tool in set(re.findall(r"\.\./\.\./(tools/[\w.]+)", script)):
            assert tool in archive, (name, tool)


def test_a_missing_image_check_is_reported():
    script = _read("deploy", "docker", "maintenance.sh")
    part = script[script.index("if [ -f ../../tools/image_digests.py ]"):]
    assert "else" in part.split("\nfi")[0] and "image_digests.py is missing" in part


def test_the_image_names_its_commit():
    assert "LABEL org.opencontainers.image.revision=$REVISION" in _read("deploy", "docker", "Dockerfile")
    assert "REVISION: ${ASKURIOUS_REVISION:-}" in _read("deploy", "docker", "compose.yaml")
    env = _read("deploy", "docker", "compose-env.sh")
    assert "ASKURIOUS_REVISION=$(sed -n 2p ../../app/deployed_at.txt" in env and "export ASKURIOUS_REVISION" in env
