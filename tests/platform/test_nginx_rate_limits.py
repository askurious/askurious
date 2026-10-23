"""nginx brakes per address in front of the app (security audit 2026-10-13, TN-2/DP-3/TN-32/AU-9): the participants'
pages and the diary app, the addresses with a secret, the forms of the sign-in, and the requests under way at once.
Before there was no limit_req or limit_conn anywhere, and the app's own limits counted per process. The values are wide
enough for a lecture hall behind one address; the live vote's phones get their own wide brakes (Abgleich 2026-10-15,
NB-7)."""
import os
import re

from tests.nginx_conf import NGINX, _read, render, server_blocks
from tests.platform.test_audit8_b_deploy import needs_bash

TEMPLATE = _read(os.path.join(NGINX, "askurious.conf.template"))


def _map_key(addr):
    """What the map $remote_addr $limit_key gives for an address (nginx tries the regexes in order)."""
    block = re.search(r"map \$remote_addr \$limit_key \{\n(.*?)\n\}", TEMPLATE, re.S).group(1)
    for pattern, value in re.findall(r'"~(.+?)" (\$\w+);', block):
        m = re.search(pattern, addr)
        if m:
            return m.group(value[1:])
    return "binary:" + addr


def test_the_zones_are_defined_with_their_rates():
    for line in ("limit_req_zone $limit_key zone=ask_pages:20m rate=100r/s;",
                 "limit_req_zone $limit_key zone=ask_secret:10m rate=30r/m;",
                 "limit_req_zone $limit_post zone=ask_signin:10m rate=20r/m;",
                 "limit_req_zone $limit_key zone=ask_live:20m rate=1000r/s;",
                 "limit_req_zone $limit_post zone=ask_live_post:10m rate=50r/s;",
                 "limit_req_zone $limit_key zone=ask_search:10m rate=10r/s;",
                 "limit_conn_zone $limit_key zone=ask_conn:20m;",
                 "limit_req_status 429;", "limit_conn_status 429;",
                 'map $request_method $limit_post { POST $limit_key; default ""; }'):
        assert line in TEMPLATE, line
    # the address is never written to a log: the refusals are "warn", the error log stays at "crit"
    assert "limit_req_log_level warn;" in TEMPLATE and "error_log /var/log/nginx/error.log crit;" in TEMPLATE


def test_ipv6_counts_by_its_64_and_ipv4_by_the_address():
    assert _map_key("2001:db8:1:2:3:4:5:6") == _map_key("2001:db8:1:2::9") == "2001:db8:1:2"
    assert _map_key("2001:db8::1") == _map_key("2001:db8::5:6:7:8") == "2001:db8:"
    assert _map_key("2001:db8:1:3::1") != _map_key("2001:db8:1:2::1")
    assert _map_key("203.0.113.7") == "binary:203.0.113.7"


@needs_bash                     # servers.sh runs in bash: marked deploy like the others (CF-5)
def test_the_sides_use_them(tmp_path):
    blocks = server_blocks(render(tmp_path))
    participant, researcher = blocks["ask.askurious.de"], blocks["askurious.de"]
    assert "limit_conn ask_conn 400;" in participant and "limit_conn ask_conn 100;" in researcher
    assert re.search(r"location ~ \^/\(s\|p\|d\|q\|j\|demo\)\(/\|\$\) \{ limit_req zone=ask_pages burst=2000 nodelay;",
                     participant)
    assert "location ^~ /api/diary/ { limit_req zone=ask_pages burst=2000 nodelay;" in participant
    live = re.search(r"location ~ \^/live\(/\|\$\) \{([^}]*)\}", participant).group(1)
    assert "limit_req zone=ask_live burst=4000 nodelay;" in live and "limit_req zone=ask_live_post burst=1500 nodelay;" in live
    other = re.search(r"location ~ \^/\(media\|geo\|static\)\(/\|\$\) \{([^}]*)\}", participant).group(1)
    assert "limit_req" not in other
    assert ("location ~ ^/auth/(sign-in|sign-in/code|forgot-password)$ { limit_req zone=ask_signin burst=30 nodelay;"
            in researcher)
    # the construct toolbox, open to visitors (audit 2026-10-22, D-03)
    assert "location ^~ /constructs/ { limit_req zone=ask_search burst=100 nodelay;" in researcher
    secrets = re.findall(r"location ~ (\S+) \{ limit_req zone=ask_secret burst=100 nodelay;", researcher)
    for path in ("/privacy/delete", "/verify/abc", "/auth/password/t", "/invite/t", "/join/t", "/api/v1/surveys/1/data.csv",
                 "/account/confirm-email/token"):
        assert any(re.search(secret, path) for secret in secrets), path
    for path in ("/privacy", "/surveys/1/editor/", "/auth/login"):
        assert not any(re.search(secret, path) for secret in secrets), path


def test_a_lecture_hall_behind_one_address_gets_through():
    """500 phones that open a survey at the same moment, about 3 requests each, within the burst plus 5 s of rate."""
    rate = int(re.search(r"zone=ask_pages:\d+m rate=(\d+)r/s", TEMPLATE).group(1))
    burst = 2000
    assert 500 * 3 <= burst + 5 * rate


def test_a_lecture_hall_in_a_live_vote_gets_through():
    """Abgleich 2026-10-15 NB-7: 1000 phones behind one address poll every 2 s and answer a quiz question within 5 s."""
    rate = int(re.search(r"zone=ask_live:\d+m rate=(\d+)r/s", TEMPLATE).group(1))
    post = int(re.search(r"zone=ask_live_post:\d+m rate=(\d+)r/s", TEMPLATE).group(1))
    assert 1000 / 2 <= rate and 1000 <= 1500 + 5 * post
