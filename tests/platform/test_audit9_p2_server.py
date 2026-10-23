"""Security and performance audit 2026-10-09, package P2 "nginx und Server": the nginx image on a maintained
branch (BT1), TLS suites (BT7), no version (BT8), HSTS on every https name (BT9), compression only for static files
(N-9), the database's own role without superuser rights (BT4), time limits (BT12), sshd settings (BT5)."""
import os
import re

from tests.nginx_conf import render, server_blocks
from tests.platform.test_audit8_b_deploy import needs_bash

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _read(*parts):
    with open(os.path.join(HERE, *parts), encoding="utf-8") as f:
        return f.read()


def _conf():
    return _read("deploy", "docker", "nginx", "askurious.conf.template")


def _server_blocks(conf):
    """The text of each server { ... } block (they hold one level of braces: the locations)."""
    return re.findall(r"^server \{\n(.*?)^\}$", conf, re.S | re.M)


def _active(conf):
    return "\n".join(line.split("#", 1)[0] for line in conf.splitlines())


def test_bt1_nginx_on_a_fixed_release_of_the_stable_branch():
    # since 2026-10-11 (audit 2026-10-10, B7) the own image askurious-nginx is built on it, with openssl
    compose = _read("deploy", "docker", "compose.yaml")
    assert not re.findall(r"^\s*image: (nginx:\S+)", compose, re.M) and "dockerfile: deploy/docker/nginx-image/Dockerfile" in compose
    images = re.findall(r"^FROM (nginx:\S+)$", _read("deploy", "docker", "nginx-image", "Dockerfile"), re.M)
    assert len(images) == 1
    # an exact release (1.30.5-alpine), even minor = stable branch; never the expired 1.29 or a moving tag
    m = re.fullmatch(r"nginx:1\.(\d+)\.\d+-alpine@sha256:[0-9a-f]{64}", images[0])  # and its digest (CFG-05)
    assert m and int(m.group(1)) >= 30 and int(m.group(1)) % 2 == 0, images[0]
    assert "1.29" not in _read("deploy", "docker", "maintenance.sh")
    assert "days ago" in _read("deploy", "docker", "maintenance.sh")                # warns about an old image


def test_bt7_bt8_tls_suites_and_no_version():
    conf = _active(_conf())
    head = conf[:conf.index("server {")]
    assert re.search(r"^server_tokens off;$", conf, re.M)
    ciphers = re.search(r"^ssl_ciphers (\S+);$", conf, re.M).group(1).split(":")
    assert ciphers and all(c.startswith("ECDHE-") and ("GCM" in c or "CHACHA20" in c) for c in ciphers)
    assert "ssl_protocols TLSv1.2 TLSv1.3;" in conf and "ssl_session_tickets off;" in conf
    assert "server_tokens" in head


@needs_bash
def test_bt9_hsts_on_every_https_name_once(tmp_path):
    conf = _active(render(tmp_path))
    https = [b for b in _server_blocks(conf) if "listen 443 ssl;" in b]
    # one block per name (B1): askurious.de, ask., status. and the same of polltergeist.de; the three old names and
    # the two www names that redirect
    assert len(https) == 11
    for block in https:
        assert "add_header Strict-Transport-Security $askurious_hsts always;" in block
    # one value for all names; includeSubDomains only with HSTS_SUBDOMAINS=1 (audit 2026-10-18, CFG-10)
    assert 'map "" $askurious_hsts { default "max-age=31536000"; }' in conf
    # the app sends the same header: nginx drops the app's, so it is sent once
    assert re.search(r"^proxy_hide_header Strict-Transport-Security;$", conf, re.M)
    # no add_header in a location: it would replace the server's (nginx inherits add_header only as a whole)
    for location in re.findall(r"location[^{]*\{([^}]*)\}", conf):
        if "add_header" in location:
            assert "add_header Strict-Transport-Security $askurious_hsts always;" in location


@needs_bash
def test_n9_compression_only_for_static_files(tmp_path):
    conf = _active(render(tmp_path, BRAND_RESEARCHER_HOSTS="", BRAND_PARTICIPANT_HOSTS=""))
    ons = re.findall(r"location[^{}]*\{[^}]*gzip on;[^}]*\}", conf)
    assert len(ons) == 2 and all("location ^~ /static/ {" in block for block in ons)
    assert "text/html" not in re.search(r"gzip_types ([^;]*);", conf).group(1)    # html always goes with gzip on
    blocks = server_blocks(conf)
    assert "root /srv;" in blocks["askurious.de"] and "gzip_static on;" in blocks["askurious.de"]
    participant = blocks["ask.askurious.de"]
    assert "root /srv;" in participant and "gzip_static on;" in participant
    # ^~ wins over the regular expression that lets /static through as well
    assert participant.index("location ^~ /static/") < participant.index("location / { return 404; }")


def test_bt10_participant_upstream_keeps_max_fails_zero():
    script = _read("deploy", "docker", "nginx", "participant-upstream.sh")
    assert 'echo "server $1:8000 resolve max_fails=0$2;"' in script


def test_bt4_app_role_with_the_old_one_as_fallback():
    compose = _read("deploy", "docker", "compose.yaml")
    assert ("DATABASE_URL: postgresql://${DB_APP_USER:-askurious}:${DB_APP_PASSWORD:-${POSTGRES_PASSWORD}}@db/askurious"
            in compose)
    script = _read("deploy", "docker", "db-app-role.sh")
    assert "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS" in script
    assert "ALTER DATABASE askurious OWNER TO $ROLE;" in script and "set -euo pipefail" in script
    # the password goes to PostgreSQL through stdin and into the .env, never to the screen or a command line
    for line in script.splitlines():
        if "$pw" in line:
            assert not re.search(r"\becho\b[^>|]*\$pw", line) or ">>" in line or "| psql_su" in line, line
            assert not ("psql" in line and " -c " in line), line
    assert "psql_su <<SQL" in script
    assert "BETRIEB.md" in script and "db-app-role.sh" in _read("deploy", "docker", "BETRIEB.md")


def test_bt12_database_time_limits():
    compose = _read("deploy", "docker", "compose.yaml")
    command = re.search(r'command: \["postgres", (.*?)\]', compose, re.S).group(1)
    assert '"statement_timeout=300s"' in command and '"idle_in_transaction_session_timeout=600s"' in command
    assert '"max_connections=500"' in command                                   # unchanged


def test_bt5_sshd_settings():
    conf = _read("deploy", "docker", "server", "10-askurious.conf")
    active = {line.split()[0]: line.split()[1] for line in conf.splitlines() if line and not line.startswith("#")}
    assert active["PasswordAuthentication"] == "no" and active["X11Forwarding"] == "no"
    assert active["PermitRootLogin"] == "no" and active["KbdInteractiveAuthentication"] == "no"
    assert "AllowUsers" not in active                                            # only after asking who signs in
