"""The maintenance page (Ralf, 2026-10-22): nginx shows it as 503 with Retry-After while the researchers' container
restarts in a deploy (switch forschende.an, deploy/docker/wartung.sh) and whenever a container does not answer (502).
The switch never stays on: restart.sh removes what it set, also when the deploy stops; wartung.sh removes it at its
end time; after-boot.sh after a reboot."""
import base64
import hashlib
import os
import re
import time

import pytest

from tests import nginx_conf
from tests.helpers import bounded_run
from tests.platform.test_audit8_b_deploy import BASH, BASH_SLOW, World, _server_from_before, _write, needs_bash

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DOCKER = os.path.join(HERE, "deploy", "docker")
PAGES = os.path.join(DOCKER, "nginx-image", "wartung")
SIDES, LANGS = ("forschende", "teilnahme"), ("de", "en")


def _read(*parts):
    with open(os.path.join(HERE, *parts), encoding="utf-8") as f:
        return f.read()


# --- nginx -------------------------------------------------------------------------------------------------------

def test_researcher_and_participant_names_carry_the_switch_and_the_failure_page(tmp_path):
    conf = nginx_conf.render(tmp_path, BRANDS="polltergeist.de=POLLtergeist")
    blocks = nginx_conf.server_blocks(conf)
    for host, side, name in (("askurious.de", "forschende", "Askurious"), ("polltergeist.de", "forschende", "POLLtergeist"),
                             ("ask.askurious.de", "teilnahme", "Askurious"), ("ask.polltergeist.de", "teilnahme", "POLLtergeist")):
        block = blocks[host]
        assert f'set $wartung_name "{name}";' in block
        # the side is set before the switch is read, the page's own address passes it
        assert block.index(f"set $wartung_seite {side};") < block.index("if (-f /etc/nginx/wartung/$wartung_seite.an)")
        assert "if ($wartung_frei) { set $wartung_an \"\"; }" in block
        assert "error_page 502 =503 /_wartung/ausfall;" in block and "error_page 503 /_wartung/schalter;" in block
        assert "internal;" in block[block.index("location ^~ /_wartung/"):]
    # the status page and the redirects of the old names are never behind it
    for host in ("status.askurious.de", "queryous.de", "www.askurious.de"):
        assert "wartung" not in blocks[host]
    assert "proxy_intercept_errors" not in conf          # the app's own 503 (backup window) stays as it is


def test_the_answer_keeps_the_protective_headers_and_says_when_to_try_again(tmp_path):
    conf = nginx_conf.render(tmp_path)
    page = conf[conf.index("location ^~ /_wartung/"):]
    page = page[:page.index("\n    }")]
    for header in ("Retry-After $wartung_retry", "Strict-Transport-Security $askurious_hsts", "X-Content-Type-Options nosniff",
                   "X-Frame-Options DENY", "Referrer-Policy no-referrer", 'Cache-Control "no-store"'):
        assert f"add_header {header} always;" in page
    assert re.search(r"map \$uri \$wartung_retry \{ default 30; /_wartung/schalter 120; \}", conf)
    assert 'map $http_accept $wartung_typ { default txt; "~*text/html" html; }' in conf


def test_brand_names_are_cleaned_and_fall_back_to_askurious(tmp_path):
    conf = nginx_conf.render(tmp_path, BRANDS='polltergeist.de=Poll"ter;geist $x, other.de=Other')
    blocks = nginx_conf.server_blocks(conf)
    assert 'set $wartung_name "Polltergeist x";' in blocks["polltergeist.de"]
    assert 'set $wartung_name "Askurious";' in blocks["askurious.de"]


# --- the pages ---------------------------------------------------------------------------------------------------

def _sha(text):
    return "sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode()


@pytest.mark.parametrize("side", SIDES)
@pytest.mark.parametrize("lang", LANGS)
def test_each_page_exists_with_its_text_and_a_csp_that_admits_only_its_own_style_and_script(side, lang):
    page = _read("deploy", "docker", "nginx-image", "wartung", f"{side}.{lang}.html")
    assert _read("deploy", "docker", "nginx-image", "wartung", f"{side}.{lang}.txt").strip()
    style = re.search(r"<style>(.*?)</style>", page, re.S).group(1)
    script = re.search(r"<script>(.*?)</script>", page, re.S).group(1)
    csp = re.search(r'http-equiv="Content-Security-Policy" content="([^"]+)"', page).group(1)
    assert f"style-src '{_sha(style)}'" in csp and f"script-src '{_sha(script)}'" in csp
    assert "default-src 'none'" in csp and "connect-src 'self'" in csp
    # nginx swaps the brand's name in (sub_filter): never inside style or script, whose hashes would no longer fit
    assert "__MARKE__" not in style + script
    assert f'<html lang="{lang}">' in page and "/healthz" in script
    assert "http://" not in page and "https://" not in page          # nothing loaded from elsewhere


def test_all_pages_share_one_style_and_one_script():
    found = set()
    for side in SIDES:
        for lang in LANGS:
            page = _read("deploy", "docker", "nginx-image", "wartung", f"{side}.{lang}.html")
            found.add((re.search(r"<style>(.*?)</style>", page, re.S).group(1),
                       re.search(r"<script>(.*?)</script>", page, re.S).group(1)))
    assert len(found) == 1


def test_only_the_researchers_page_reloads_itself():
    """A participant's page of answers was a POST: reloading would send it again unasked; they choose."""
    for lang in LANGS:
        assert 'data-selbst="1"' in _read("deploy", "docker", "nginx-image", "wartung", f"forschende.{lang}.html")
        participant = _read("deploy", "docker", "nginx-image", "wartung", f"teilnahme.{lang}.html")
        assert 'data-selbst="0"' in participant and 'id="zurueck"' in participant


def test_the_nginx_image_carries_the_pages_and_compose_mounts_the_switch():
    assert "COPY deploy/docker/nginx-image/wartung /srv/wartung" in _read("deploy", "docker", "nginx-image", "Dockerfile")
    compose = _read("deploy", "docker", "compose.yaml")
    assert "- ${HOME}/askurious-status/wartung:/etc/nginx/wartung:ro" in compose
    assert "BRANDS: ${BRANDS:-}" in compose[compose.index("  nginx:"):]
    assert "wartung=absent:/stamps/wartung," in compose and "STATUS_INFO: backup,wartung" in compose


# --- wartung.sh --------------------------------------------------------------------------------------------------

def _wartung(tmp_path, *args):
    env = dict(os.environ, WARTUNG_DIR=(tmp_path / "w").as_posix())
    return bounded_run.run([BASH, os.path.join(DOCKER, "wartung.sh").replace("\\", "/"), *args], env=env, text=True,
                           timeout=int(os.environ.get("BASH_TEST_TIMEOUT", str(60 * BASH_SLOW))))


@needs_bash
def test_switch_on_and_off_with_an_end_time(tmp_path):
    out = _wartung(tmp_path, "an", "alle", "5", "Restore")
    assert out.returncode == 0, out.stderr
    for side in SIDES:
        end, reason = (tmp_path / "w" / f"{side}.an").read_text().split(" ", 1)
        assert 4 * 60 < int(end) - time.time() <= 5 * 60 and reason.strip() == "Restore"
    assert "until" in _wartung(tmp_path, "status").stdout
    assert _wartung(tmp_path, "aus", "forschende").returncode == 0
    assert sorted(p.name for p in (tmp_path / "w").iterdir()) == ["teilnahme.an"]


@needs_bash
def test_pruefen_switches_off_what_ran_out_or_cannot_be_read(tmp_path):
    folder = tmp_path / "w"
    folder.mkdir()
    (folder / "forschende.an").write_text(f"{int(time.time()) - 5} deploy\n")
    (folder / "teilnahme.an").write_text("garbage\n")
    keep = folder / "later.an"                         # not a side: left alone by the sides, still checked
    keep.write_text(f"{int(time.time()) + 600}\n")
    out = _wartung(tmp_path, "pruefen")
    assert out.returncode == 0, out.stderr
    assert not (folder / "forschende.an").exists() and not (folder / "teilnahme.an").exists()
    assert keep.exists() and out.stderr.count("switched off after its end time") == 2


@needs_bash
@pytest.mark.parametrize("args", [("an", "irgendwo"), ("an", "alle", "0"), ("an", "alle", "241"), ("an", "alle", "x"), ("aus",), ()])
def test_wrong_calls_switch_nothing(tmp_path, args):
    out = _wartung(tmp_path, *args)
    assert out.returncode == 2 and not (tmp_path / "w").exists()


# --- restart.sh and the deploy -----------------------------------------------------------------------------------

# a stand-in in front of the docker stand-in: notes whether the switch is on when the researchers' container restarts
SPY = r'''#!/usr/bin/env bash
if [[ "$*" == "compose up -d --no-deps researcher"* ]]; then
  if [ -e "$SERVER/askurious-status/wartung/forschende.an" ]; then echo WARTUNG-AN >> "$SERVER/docker.log"
  else echo WARTUNG-AUS >> "$SERVER/docker.log"; fi
fi
exec "$(dirname "$0")/docker-fake" "$@"
'''


@pytest.fixture
def spied(tmp_path):
    if BASH is None:
        pytest.skip("no bash")
    w = World(tmp_path)
    os.replace(w.bin / "docker", w.bin / "docker-fake")
    _write(str(w.bin / "docker"), SPY, 0o755)
    _server_from_before(w)
    return w


@needs_bash
def test_deploy_switches_the_page_on_for_the_researchers_restart_and_off_after(spied):
    out = spied.run()
    assert out.returncode == 0, out.stdout + out.stderr
    log = spied.text("docker.log")
    assert "WARTUNG-AN" in log and "WARTUNG-AUS" not in log
    assert list(spied.path("askurious-status", "wartung").glob("*.an")) == []


@needs_bash
def test_a_deploy_that_stops_leaves_no_switch_on_and_keeps_one_set_by_hand(spied):
    _write(str(spied.bin / "sleep"), "#!/usr/bin/env bash\nexit 0\n", 0o755)
    _write(str(spied.bin / "seq"), "#!/usr/bin/env bash\necho 1\n", 0o755)
    folder = spied.path("askurious-status", "wartung")
    _write(str(folder / "teilnahme.an"), f"{int(time.time()) + 600} von Hand\n")
    out = spied.run(FAKE_UNHEALTHY="researcher")
    assert out.returncode != 0 and "researcher did not answer" in out.stderr
    assert "WARTUNG-AN" in spied.text("docker.log")
    assert not (folder / "forschende.an").exists()
    assert (folder / "teilnahme.an").exists()              # not this deploy's: only by hand or its end time


def test_cron_and_boot_switch_it_off():
    deploy = _read("deploy", "docker", "deploy-docker.sh")
    assert "* * * * * \\$HOME/askurious/deploy/docker/wartung.sh pruefen" in deploy
    assert './wartung.sh aus alle' in _read("deploy", "docker", "after-boot.sh")
    restart = _read("deploy", "docker", "restart.sh")
    assert "trap finish EXIT" in restart and "trap 'exit 130' INT TERM HUP" in restart


# --- the status page ---------------------------------------------------------------------------------------------

def test_status_page_shows_the_switch_for_information(tmp_path):
    from app import status_node

    assert status_node.parse_checks("wartung=absent:/stamps/wartung") == [("wartung", "absent", "/stamps/wartung")]
    assert status_node.check("absent", str(tmp_path / "missing"))[0] is True
    (tmp_path / "teilnahme.an").write_text("1\n")
    (tmp_path / ".forschende.tmp").write_text("1\n")
    ok, _, detail = status_node.check("absent", str(tmp_path))
    assert ok is False and detail == "teilnahme"
