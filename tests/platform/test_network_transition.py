"""DP-15: no early network disconnects, foreign-container mutation or credential fallback."""
import importlib.util
import json
from pathlib import Path

import pytest

from tests.platform.test_audit8_b_deploy import DOCKER, World, _write, needs_bash

spec = importlib.util.spec_from_file_location("network_transition", Path(DOCKER) / "isolation/transition.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def controller(tmp_path):
    obj = module.Transition.__new__(module.Transition)
    obj.project = "test"
    obj.state = tmp_path / "state.json"
    obj.services = {
        "db": {"networks": {"backend": {}}, "image": "postgres:17"},
        "participant-a": {"networks": {"frontend": {}, "backend": {}}},
        "participant-b": {"networks": {"frontend": {}, "backend": {}}},
        "nginx": {"networks": {"frontend": {"aliases": ["test.example"]}}},
    }
    obj.cfg = {"networks": {name: {"name": "test_" + name} for name in ("frontend", "backend")}}
    obj.preflight = lambda: None
    obj.check_networks = lambda: None
    obj.check_roles = lambda: None
    obj.compose = lambda *a, **kw: ""
    obj.data = {name: {"Id": name, "Config": {"Labels": {"com.docker.compose.config-hash": "old"}, "Env": []},
                       "State": {"Running": True}, "Image": "pg-image", "NetworkSettings": {"Networks": {"test_default": {}}}}
                for name in obj.services}
    obj.container = lambda name: obj.data[name]
    return obj


def test_prepare_only_connects_and_preserves_legacy_endpoints(tmp_path, monkeypatch):
    obj = controller(tmp_path)
    calls = []
    monkeypatch.setattr(module, "command", lambda args, **kw: calls.append(args) or "")
    obj.prepare()
    assert calls and all(call[:3] == ["docker", "network", "connect"] for call in calls)
    assert all("test_default" in data["NetworkSettings"]["Networks"] for data in obj.data.values())
    assert any("test.example" in call for call in calls)


def target_endpoints(obj):
    for name, data in obj.data.items():
        data["NetworkSettings"]["Networks"] = {"test_" + key: {} for key in obj.services[name]["networks"]}
    obj.data["db"]["NetworkSettings"]["Networks"]["test_default"] = {}


@pytest.mark.parametrize("failure", ["not-running", "old-network", "old-credentials", "nginx-unreachable"])
def test_finalize_keeps_legacy_db_on_incomplete_rollout(tmp_path, monkeypatch, failure):
    obj = controller(tmp_path)
    target_endpoints(obj)
    if failure == "not-running":
        obj.data["participant-b"]["State"]["Running"] = False
    elif failure == "old-network":
        obj.data["participant-b"]["NetworkSettings"]["Networks"]["test_default"] = {}
    elif failure == "old-credentials":
        obj.services["participant-b"]["environment"] = {"DATABASE_URL": "runtime", "DATABASE_SCHEMA_MODE": "check"}
        obj.data["participant-b"]["Config"]["Env"] = ["DATABASE_URL=owner", "DATABASE_SCHEMA_MODE=auto"]
    else:
        obj.compose = lambda *a, **kw: (_ for _ in ()).throw(module.Refused("unreachable"))
    monkeypatch.setattr(module, "command", lambda *a, **kw: pytest.fail("must not disconnect"))
    with pytest.raises(module.Refused):
        obj.finalize()
    assert not obj.state.exists()


def test_finalize_disconnects_only_owned_db_endpoint_after_http_checks(tmp_path, monkeypatch):
    obj = controller(tmp_path)
    target_endpoints(obj)
    calls = []
    obj.compose = lambda *a, **kw: calls.append(a) or ""
    def run(args, **kwargs):
        calls.append(tuple(args))
        return json.dumps([{"Labels": {"com.docker.compose.project": "test"}}])
    monkeypatch.setattr(module, "command", run)
    obj.finalize()
    assert calls[-1] == ("docker", "network", "disconnect", "test_default", "db")
    assert sum("wget" in call for call in calls) == 2
    assert json.loads(obj.state.read_text())["image"] == "pg-image"


def test_foreign_legacy_network_is_never_disconnected(tmp_path, monkeypatch):
    obj = controller(tmp_path)
    target_endpoints(obj)
    calls = []
    monkeypatch.setattr(module, "command", lambda args, **kw: calls.append(args) or json.dumps([{"Labels": {}}]))
    with pytest.raises(module.Refused, match="Foreign legacy"):
        obj.finalize()
    assert len(calls) == 1 and not obj.state.exists()


@pytest.mark.parametrize("url", [None, "sqlite:///:memory:", "postgresql://u:p@other/d", "postgresql://owner@db/d",
                                 "postgresql://u:p@db/d?options=-crole=owner"])
def test_missing_or_ambiguous_connections_are_refused(url):
    with pytest.raises(module.Refused):
        module.connection(url)


@needs_bash
@pytest.mark.parametrize("failed", ["migrate", "prepare", "finalize", ""])
def test_isolated_restart_orders_backup_job_and_sequential_routing(tmp_path, failed):
    w = World(tmp_path)
    folder = w.repo / "deploy/docker"
    _write(str(w.repo / "migrations/versions/0059.py"), "revision = '0059'\ndown_revision = None\nROLLING = True\n")
    _write(str(folder / ".env"), "DB_ISOLATION=1\n")
    fake_python = '''#!/usr/bin/env bash
echo "isolation $2" >> "$SERVER/docker.log"
[ "$2" != "${FAKE_ISOLATION_FAIL:-}" ]
'''
    _write(str(w.bin / "python3"), fake_python, 0o755)
    docker = (w.bin / "docker").read_text()
    docker = docker.replace('case "$*" in', '''if [[ "$*" == *"run --rm --no-deps migrate"* ]] && [ "${FAKE_JOB_FAIL:-}" = 1 ]; then exit 1; fi
case "$*" in''')
    _write(str(w.bin / "docker"), docker, 0o755)
    out = w.run("--no-build", script=str(folder / "restart.sh"), AS_SERVER="1", FAKE_PARTICIPANTS="1",
                FAKE_DB_VERSION="0059", FAKE_JOB_FAIL="1" if failed == "migrate" else "",
                FAKE_ISOLATION_FAIL=failed if failed != "migrate" else "")
    log = w.text("docker.log")
    assert log.index("pg_dump") < log.index("isolation prepare")
    if failed == "prepare":
        assert out.returncode != 0 and "run --rm --no-deps migrate" not in log
    else:
        assert log.index("isolation prepare") < log.index("run --rm --no-deps migrate")
        if failed == "migrate":
            assert out.returncode != 0 and "up -d --no-deps researcher" not in log
        else:
            assert log.index("run --rm --no-deps migrate") < log.index("up -d --no-deps researcher")
            assert log.index("participant-a up") < log.index("participant-b down")
            assert log.index("http://participant-a:8000/healthz") < log.index("participant-a up")
            assert log.index("participant-b up") < log.index("isolation finalize")
            assert (out.returncode == 0) == (not failed), out.stdout + out.stderr
    assert "up -d --no-deps --no-recreate --wait db" in log or failed == "prepare"


@needs_bash
def test_isolated_rollback_to_old_runtime_code_is_refused_before_swap(tmp_path):
    w = World(tmp_path, repo=False)
    _write(str(w.path("askurious", "deploy", "docker", ".env")), "DB_ISOLATION=1\n")
    _write(str(w.path("askurious", "deploy", "docker", "compose-env.sh")), Path(DOCKER, "compose-env.sh").read_text())
    _write(str(w.path("askurious", "app", "deployed_at.txt")), "new\nhead\n")
    _write(str(w.path("askurious.old", "app", "deployed_at.txt")), "old\nprevious\n")
    before = w.text("askurious", "app", "deployed_at.txt")
    out = w.run("rollback", script=str(Path(DOCKER) / "deploy-docker.sh"))
    assert out.returncode != 0 and "old image/code lacks isolated runtime" in out.stderr
    assert w.text("askurious", "app", "deployed_at.txt") == before
