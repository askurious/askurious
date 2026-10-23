"""A database dump alone must not advance the regular backup success marker."""
from tests.platform.test_audit8_b_deploy import _write, needs_bash
from tests.privacy.test_backup_uploads import _server


@needs_bash
def test_upload_snapshot_failure_keeps_previous_success_marker(tmp_path):
    world, folder = _server(tmp_path)
    marker = world.path("askurious-status", "backup-ok")
    _write(str(marker), "previous-success\n")
    docker = world.bin / "docker"
    script = docker.read_text(encoding="utf-8")
    script = script.replace('case "$*" in', 'case "$*" in\n  *"python - /app/instance /snap"*) exit 1 ;;', 1)
    _write(str(docker), script, 0o755)
    result = world.run(script=str(folder / "backup.sh"), AS_SERVER="1")
    assert result.returncode == 1 and "uploads snapshot failed" in result.stderr
    assert marker.read_text() == "previous-success\n"


@needs_bash
def test_failed_configured_offsite_copy_keeps_previous_success_marker(tmp_path):
    world, folder = _server(tmp_path)
    marker = world.path("askurious-status", "backup-ok")
    _write(str(marker), "previous-success\n")
    _write(str(folder / ".env"), "OFFSITE_TARGET=backup@example.org:askurious\n")
    result = world.run(script=str(folder / "backup.sh"), AS_SERVER="1")
    assert result.returncode == 1 and "off-site copy failed" in result.stderr
    assert marker.read_text() == "previous-success\n"
