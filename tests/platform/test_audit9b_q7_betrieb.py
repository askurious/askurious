"""Security and performance audit 2026-10-09b, package Q7 "Betrieb": one nginx server block per https name with its
certificate at a fixed path, read by the master only (B1); www names that redirect (S-B1, Ralf 2026-10-10); empty old
names leave their block out (B4); the migrations' own time limits under PostgreSQL (B3)."""
import os
import re

import pytest

from tests.nginx_conf import render, server_blocks, servers
from tests.platform.test_audit8_b_deploy import needs_bash

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DOCKER = os.path.join(HERE, "deploy", "docker")


def _read(*parts):
    with open(os.path.join(DOCKER, *parts), encoding="utf-8") as f:
        return f.read()


def _active(text):
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


# --- B1: one server block per name, fixed paths -------------------------------------------------------------------

@needs_bash
def test_b1_one_block_per_name_with_its_own_certificate(tmp_path):
    conf = render(tmp_path)
    blocks = server_blocks(conf)
    sides = {"askurious.de": "researcher", "polltergeist.de": "researcher",
             "ask.askurious.de": "participant", "ask.polltergeist.de": "participant",
             "status.askurious.de": "status", "status.polltergeist.de": "status"}
    for name, side in sides.items():
        block = blocks[name]
        assert f"ssl_certificate     /etc/nginx/certs/{name}/fullchain.pem;" in block
        assert f"ssl_certificate_key /etc/nginx/certs/{name}/privkey.pem;" in block
        assert "listen 443 ssl;" in block and "listen [::]:443 ssl;" in block and "http2 on;" in block
        # with the files it includes itself (the maintenance page, wartung.conf)
        assert _read("nginx", "locations", side + ".conf").replace(
            "include /etc/nginx/locations/wartung.conf;", _read("nginx", "locations", "wartung.conf")) in block
    # never a certificate picked per handshake, never a name list or a regular expression for the names
    assert "$ssl_server_name" not in conf and "~^status" not in conf
    assert all(" " not in name for name in blocks)
    # every ${...} filled, the template's own blocks gone (all names come from servers.sh)
    assert "${" not in conf
    assert "server_name ${" not in _read("nginx", "askurious.conf.template")
    assert "include /etc/nginx/servers/*.conf;" in _read("nginx", "askurious.conf.template")
    assert "return 302 https://$researchers_host/;" in blocks["ask.polltergeist.de"]   # -> polltergeist.de/


@needs_bash
def test_b1_names_in_lower_case_without_a_dot_at_the_end_and_once(tmp_path):
    """nginx compares the name the client asked for (SNI, Host) in lower case and without a dot at the end; the
    names in server_name and in the certificate's path are written the same way, so POLLTERGEIST.DE. finds its block."""
    conf, names, _ = servers(tmp_path, RESEARCHER_HOST="Askurious.DE.",
                             BRAND_RESEARCHER_HOSTS="Polltergeist.de. askurious.de",
                             BRAND_PARTICIPANT_HOSTS="ASK.polltergeist.de")
    assert names.count("askurious.de") == 1 and "polltergeist.de" in names and "ask.polltergeist.de" in names
    assert "status.polltergeist.de" in names and "www.polltergeist.de" in names
    assert not any(n != n.lower() or n.endswith(".") for n in names)
    assert conf.count("server_name askurious.de;") == 1


@needs_bash
def test_b1_no_injection_through_a_name(tmp_path):
    conf, names, err = servers(tmp_path, BRAND_RESEARCHER_HOSTS="evil.de;include", BRAND_PARTICIPANT_HOSTS="../x")
    assert "include /etc/nginx/locations" in conf
    assert not any(";" in n or "/" in n for n in names)
    assert "evil" not in conf and "../" not in conf and "left out" in err


@needs_bash
def test_b1_self_signed_covers_every_name(tmp_path):
    """self-signed.sh makes a certificate for each name servers.sh lists (nginx does not start while one is
    missing); servers.sh runs first (04 before 05)."""
    names = servers(tmp_path)[1]
    assert {"askurious.de", "ask.askurious.de", "status.askurious.de", "polltergeist.de", "ask.polltergeist.de",
            "status.polltergeist.de", "queryous.de", "ask.queryous.de", "status.queryous.de",
            "www.askurious.de", "www.polltergeist.de"} == set(names)
    script = _read("nginx", "self-signed.sh")
    assert re.search(r'^for host in \$\(cat "\$names"\); do', script, re.M)
    compose = _read("compose.yaml")
    assert "./nginx-live/servers.sh:/docker-entrypoint.d/04-servers.sh:ro" in compose
    assert "./nginx-live/self-signed.sh:/docker-entrypoint.d/05-self-signed.sh:ro" in compose
    assert "./nginx-live/locations:/etc/nginx/locations:ro" in compose
    restart = _read("restart.sh")
    assert "nginx/servers.sh" in restart and "nginx/locations/*.conf" in restart   # a change restarts nginx


def test_b1_only_the_master_reads_the_keys():
    """Fixed paths: nginx's master (root) reads the keys at the start and at each reload, never a worker. The copies
    are root's and 0600 again (written under umask 077, moved into place), not the user nginx's as in #800."""
    script = _active(_read("nginx", "self-signed.sh"))
    assert "chown" not in script and "ln -s" not in script and "cp " not in script
    assert re.search(r"^umask 077$", script, re.M)
    assert 'cat "$src/privkey.pem" > "$dst/privkey.pem.new"' in script
    assert 'mv -f "$dst/privkey.pem.new" "$dst/privkey.pem"' in script
    assert 'chmod 644 "$dst/fullchain.pem.new"' in script and "chmod 644 \"$dst/privkey" not in script
    compose = _read("compose.yaml")
    assert "sh /docker-entrypoint.d/05-self-signed.sh >/dev/null; nginx -s reload" in compose


# --- old names (rename 2026-10-09) and B4 ---------------------------------------------------------------------

@needs_bash
def test_old_names_redirect_with_path_and_query(tmp_path):
    compose = _read("compose.yaml")
    blocks = server_blocks(render(tmp_path))
    for old, new, host in (("OLD_RESEARCHER_HOST", "askurious.de", "queryous.de"),
                           ("OLD_PARTICIPANT_HOST", "ask.askurious.de", "ask.queryous.de"),
                           ("OLD_STATUS_HOST", "status.askurious.de", "status.queryous.de")):
        assert f"{old}: ${{{old}:-{host}}}" in compose
        block = blocks[host]
        # 308 keeps a POST a POST; $request_uri carries path and query
        assert f"return 308 https://{new}$request_uri;" in block
        assert "proxy_pass" not in block and "include" not in block
        assert f"ssl_certificate     /etc/nginx/certs/{host}/fullchain.pem;" in block
        assert "Strict-Transport-Security $askurious_hsts always" in block
    assert "access_log off;" in blocks["ask.queryous.de"]      # no log of participants' requests (B2)


@needs_bash
def test_b4_empty_old_names_leave_their_block_out(tmp_path):
    """OLD_*_HOST= empty (the old names switched off) gave "server_name ;" and the path /etc/nginx/certs//...: nginx
    did not start. Now their blocks are left out."""
    empty = dict(OLD_RESEARCHER_HOST="", OLD_PARTICIPANT_HOST="", OLD_STATUS_HOST="")
    conf, names, err = servers(tmp_path, **empty)
    assert not any("queryous" in n for n in names) and "queryous" not in conf
    assert "server_name ;" not in conf and "certs//" not in conf and "left out" not in err
    full = render(tmp_path, **empty)
    assert "${" not in full and "server_name ;" not in full and "certs//" not in full
    assert "askurious.de" in server_blocks(full)


# --- S-B1: www names redirect -----------------------------------------------------------------------------------

@needs_bash
def test_www_names_redirect_to_the_name_without_www(tmp_path):
    blocks = server_blocks(render(tmp_path))
    for www, target in (("www.askurious.de", "askurious.de"), ("www.polltergeist.de", "polltergeist.de")):
        block = blocks[www]
        assert f"return 301 https://{target}$request_uri;" in block
        assert f"ssl_certificate_key /etc/nginx/certs/{www}/privkey.pem;" in block
        assert "proxy_pass" not in block and "include" not in block
    assert not any(name.startswith("www.ask.") or name.startswith("www.status.") for name in blocks)


@needs_bash
def test_www_names_can_be_switched_off(tmp_path):
    conf, names, _ = servers(tmp_path, WWW_REDIRECT="0")
    assert not any(n.startswith("www.") for n in names) and "www." not in conf
    assert "WWW_REDIRECT: ${WWW_REDIRECT:-1}" in _read("compose.yaml")


def test_cert_asks_for_the_www_names_too():
    script = _read("deploy-docker.sh")
    cert = script[script.index("  cert)"):script.index("  rollback)")]
    assert "WWW_REDIRECT=0" in cert and "www.$RESEARCHER_HOST" in cert and "printf 'www.%s '" in cert
    assert re.search(r'for host in .*\$www; do', cert)


# --- B3: the migrations' own time limits --------------------------------------------------------------------------

class _Dialect:
    name = "postgresql"


class _Connection:
    """Stands in for a PostgreSQL connection: what was sent, committed and whether it went back into the pool."""

    def __init__(self):
        self.dialect, self.sent, self.commits, self.invalidated = _Dialect(), [], 0, False

    def exec_driver_sql(self, statement):
        self.sent.append(statement)

    def commit(self):
        self.commits += 1

    def invalidate(self):
        self.invalidated = True


def test_b3_migration_without_statement_limit_but_with_lock_timeout():
    from app import migrations

    connection, seen = _Connection(), []
    migrations._run_on(connection, lambda cfg, target: seen.append(list(connection.sent)), "head")
    # set before the migration runs, for this session only; the connection never goes back into the pool
    assert seen == [["SET statement_timeout = 0", "SET lock_timeout = '10s'"]]
    assert connection.invalidated and connection.commits == 2


def test_b3_connection_dropped_also_after_a_failed_migration():
    from app import migrations

    connection = _Connection()

    def fails(cfg, target):
        raise RuntimeError("broken")

    with pytest.raises(RuntimeError):
        migrations._run_on(connection, fails, "head")
    assert connection.invalidated


def test_b3_sqlite_keeps_its_connection():
    from alembic import command
    from sqlalchemy import create_engine

    from app import migrations

    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        migrations._run_on(connection, command.upgrade, "0001")
        assert not connection.invalidated
        assert migrations.current(connection) == "0001"
    engine.dispose()


def test_b3_a_lock_timeout_is_tried_again(app, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from app import migrations

    class Orig(Exception):
        sqlstate = "55P03"

    calls = []

    def run_on(connection, step, target="head"):
        calls.append(target)
        if len(calls) < 3:
            raise OperationalError("ALTER TABLE", {}, Orig("canceling statement due to lock timeout"))

    monkeypatch.setattr(migrations, "_run_on", run_on)
    monkeypatch.setattr(migrations, "LOCK_PAUSE", 0)
    with app.app_context():
        migrations._run(None)
        assert len(calls) == 3
        calls.clear()
        monkeypatch.setattr(migrations, "LOCK_TRIES", 2)
        with pytest.raises(OperationalError):
            migrations._run(None)
        assert len(calls) == 2


def test_b3_other_errors_are_not_tried_again(app, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from app import migrations

    calls = []

    def run_on(connection, step, target="head"):
        calls.append(target)
        raise OperationalError("ALTER TABLE", {}, Exception("something else"))

    monkeypatch.setattr(migrations, "_run_on", run_on)
    monkeypatch.setattr(migrations, "LOCK_PAUSE", 0)
    with app.app_context():
        with pytest.raises(OperationalError):
            migrations._run(None)
    assert len(calls) == 1


def test_b3_the_apps_connections_keep_their_limits():
    """The limits are set on the migrations' connection only; the server's statement_timeout (300 s) and the
    idle_in_transaction limit for the app stay in compose.yaml."""
    compose = _read("compose.yaml")
    assert '"-c", "statement_timeout=300s", "-c", "idle_in_transaction_session_timeout=600s"' in compose
    with open(os.path.join(HERE, "app", "migrations.py"), encoding="utf-8") as f:
        source = f.read()
    assert source.count("SET statement_timeout") == 1 and "connection.invalidate()" in source
