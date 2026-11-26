"""nginx after the security audit of 2026-10-13: body limits per path instead of 45 MB everywhere (DP-12), the
protective headers also on the answers nginx gives itself, and no answer at all for a foreign Host header (DP-13)."""
import re

from tests.nginx_conf import render, server_blocks
from tests.platform.test_audit8_b_deploy import needs_bash


def _active(conf):
    return "\n".join(line.split("#", 1)[0] for line in conf.splitlines())


def _limit(block, location=None):
    """client_max_body_size of the server block, or of the location that starts with `location`."""
    if location is None:
        return re.search(r"^\s*client_max_body_size (\S+);", block, re.M).group(1)
    line = next(line for line in block.splitlines() if line.strip().startswith(location))
    m = re.search(r"client_max_body_size (\S+);", line)
    return m.group(1) if m else None


@needs_bash
def test_body_limits_follow_what_the_app_takes(tmp_path):
    """Audit 2026-10-13, DP-12: nginx kept up to 45 MB (researchers) and 25 MB (participants) of any request on its disk
    before the app refused it with its own, smaller limits."""
    blocks = server_blocks(_active(render(tmp_path)))
    researcher, participant = blocks["askurious.de"], blocks["ask.askurious.de"]
    assert _limit(researcher) == "11m"                                             # MAX_CONTENT_LENGTH 10 MB
    assert _limit(researcher, "location = /surveys/new/import") == "45m"            # IMPORT_MAX_BYTES 40 MB
    assert _limit(researcher, "location ~ ^/surveys/[^/]+/paper/upload$") == "45m"  # paper.UPLOAD_BYTES 40 MB
    # the own Rechenknecht calls researcher:8000 directly; from outside the callbacks are closed (Abgleich 2026-10-15)
    assert "location ^~ /rechenknecht/ { return 404; }" in researcher
    assert _limit(participant) == "3m"                                             # PARTICIPANT_FORM_BYTES 2 MB
    assert _limit(participant, "location ^~ /s/") == "25m"                         # voice messages
    # ^~ before the regular expression that lets /s through as well
    assert participant.index("location ^~ /s/") < participant.index("location ~ ^/(s|p|")
    # the brand's names share the files
    assert _limit(blocks["ask.polltergeist.de"], "location ^~ /s/") == "25m"


def test_body_limits_match_the_app():
    """The paths and sizes above are the app's own (DP-12): a renamed route would otherwise get 11 MB only."""
    from app import paper
    from config import Config

    assert Config.MAX_CONTENT_LENGTH <= 11 * 1024 * 1024 and Config.PARTICIPANT_FORM_BYTES <= 3 * 1024 * 1024
    assert Config.IMPORT_MAX_BYTES <= 45 * 1024 * 1024 and paper.UPLOAD_BYTES <= 45 * 1024 * 1024


def test_import_and_paper_routes_are_where_nginx_expects_them(app):
    rules = {r.endpoint: r.rule for r in app.url_map.iter_rules()}
    assert rules["wizard.import_file"] == "/surveys/new/import"
    assert re.fullmatch(r"/surveys/<sid:survey_id>/paper/upload", rules["paper.upload"])
    assert rules["compute.result"].startswith("/rechenknecht/")


@needs_bash
def test_own_answers_carry_the_protective_headers_and_foreign_hosts_get_none(tmp_path):
    """Audit 2026-10-13, DP-13: nginx's own 404, redirects and 502 had no X-Content-Type-Options, X-Frame-Options or
    Referrer-Policy; a request with one of our names in the handshake and another Host header landed in a default
    server without any answer of its own (nginx's start page)."""
    conf = _active(render(tmp_path))
    # only where the app sent none: an empty value adds nothing, so the app's headers stay once and an embedded
    # dashboard (CSP frame-ancestors *, no X-Frame-Options) is not framed out by nginx
    assert re.search(r'^map \$upstream_http_x_content_type_options \$own_nosniff \{ "" nosniff; default ""; \}$', conf, re.M)
    assert re.search(r'^map \$upstream_http_content_security_policy \$own_frame_options \{ "" DENY; default ""; \}$', conf, re.M)
    assert re.search(r'^map \$upstream_http_referrer_policy \$own_referrer_policy \{ "" no-referrer; default ""; \}$', conf, re.M)
    https = [b for b in server_blocks(conf).values() if "listen 443 ssl;" in b]
    assert len(https) == 11
    for block in https:
        assert "add_header X-Content-Type-Options $own_nosniff always;" in block
        assert "add_header X-Frame-Options $own_frame_options always;" in block
        assert "add_header Referrer-Policy $own_referrer_policy always;" in block
    # Static answers replace inheritance with all protective headers, including nginx's own 404.
    for location in re.findall(r"location[^\{]*\{([^}]*)\}", conf):
        if "add_header" in location:
            for header in ("Strict-Transport-Security", "X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy"):
                assert f"add_header {header} " in location
    default = re.search(r"server \{\n\s*listen 443 ssl default_server;(.*?)\n\}", conf, re.S).group(1)
    assert "ssl_reject_handshake on;" in default and re.search(r"^\s*return 444;$", default, re.M)


def test_app_sends_the_headers_nginx_looks_for(client):
    """The maps above test the app's own headers: the app sends all three on its pages (else nginx adds them)."""
    r = client.get("/")
    assert r.headers.get("X-Content-Type-Options") and r.headers.get("Referrer-Policy")
    assert r.headers.get("Content-Security-Policy")


def test_internal_callbacks_closed_from_outside_and_tokens_kept_out_of_the_log():
    """Abgleich 2026-10-15, DP-12 rest: anyone could send /rechenknecht/ headers of the right form and make the app read
    up to 64 MB; NB-9: the transcription cancel relay /guide/internal/ was reachable from outside; KR-9: a failed
    request to /auth/password/<token>, /invite/<token> or /join/<token> wrote the token into nginx's access log."""
    import os

    from tests.nginx_conf import NGINX, _read

    researcher = _active(_read(os.path.join(NGINX, "locations", "researcher.conf")))
    assert "location ^~ /rechenknecht/ { return 404; }" in researcher
    assert "location ^~ /guide/internal/ { return 404; }" in researcher
    template = _active(_read(os.path.join(NGINX, "askurious.conf.template")))
    fmt = re.search(r"log_format noaddress '([^']*)';", template).group(1)
    assert "$logged_uri" in fmt and "$uri" not in fmt.replace("$logged_uri", "")
    block = re.search(r"map \$uri \$logged_uri \{(.*?)\}", template, re.S).group(1)
    pattern, value = re.search(r'"~(.+?)" "(.+?)";', block).groups()
    pattern = pattern.replace("(?<", "(?P<")
    for path in ("/auth/password/abc", "/invite/abc", "/join/abc", "/account/confirm-email/abc", "/privacy/delete/12-x",
                 # audit 2026-11-20: registration, founding and organisation links, the monitoring feed, the status JSON
                 "/signup/abc", "/joinorg/abc", "/org/join/abc", "/api/monitoring/abc", "/status/abc.json"):
        m = re.search(pattern, path)
        assert m and "abc" not in value.replace("$fixed", m.group("fixed")) and "12-x" not in value
    for path in ("/surveys/1/editor", "/org/3/people", "/joinery", "/statusseite"):
        assert not re.search(pattern, path), path


def test_every_token_in_a_path_is_kept_out_of_the_log(app):
    """Audit 2026-11-20: every route of the researchers' and the status side with <token> in its path is covered by
    the mask of nginx's log of failed requests (the participants' side keeps no access log at all)."""
    import os

    from app import hosts
    from tests.nginx_conf import NGINX, _read

    template = _active(_read(os.path.join(NGINX, "askurious.conf.template")))
    block = re.search(r"map \$uri \$logged_uri \{(.*?)\}", template, re.S).group(1)
    pattern = re.search(r'"~(.+?)" "(.+?)";', block).group(1).replace("(?<", "(?P<")
    missing = []
    for rule in app.url_map.iter_rules():
        if "<token>" not in rule.rule or hosts.side_of(rule.endpoint) == hosts.PARTICIPANT:
            continue
        if not re.search(pattern, rule.rule.replace("<token>", "abc")):
            missing.append(rule.rule)
    assert not missing, missing


def test_every_written_form_of_an_ipv6_address_counts_for_its_64(app):
    """nginx limits per /64 of an IPv6 sender: the map takes the first four groups from every form in which an
    address is written (RFC 5952 leaves out one run of zero groups as "::")."""
    import ipaddress
    import os
    import random

    from tests.nginx_conf import NGINX, _read

    template = _active(_read(os.path.join(NGINX, "askurious.conf.template")))
    block = re.search(r"map \$remote_addr \$limit_key \{(.*?)\}\s*\nmap", template, re.S).group(1)
    lines = [(re.compile(p.replace("(?<", "(?P<")), value.strip('"'))
             for p, value in re.findall(r'"~(.+?)" ("?\$[^;]+);', block)]
    assert len(lines) == 5

    def net(text):
        """The key nginx counts this address under, as four groups (a key of fewer groups stands for zeros)."""
        for pattern, value in lines:
            m = pattern.search(text)
            if m:
                key = re.sub(r"\$(\w+)", lambda g: m.group(g.group(1)), value)
                groups = [g for g in key.split(":") if g]
                return ":".join(groups + ["0"] * (4 - len(groups)))
        return None

    rng = random.Random(7)
    seen = set()
    for _ in range(3000):
        groups = [rng.choice((0, 0, 0, rng.randrange(1, 0x10000))) for _ in range(8)]
        if groups[0] == 0:
            groups[0] = 0x2001
        address = ipaddress.IPv6Address(":".join(f"{g:x}" for g in groups))
        written = str(address)                                              # as nginx writes it (compressed)
        expected = ":".join(f"{g:x}" for g in groups[:4])
        assert net(written) == expected, written
        seen.add(written.count(":"))
    assert net("2001::1:a:b:c:d") == "2001:0:0:1" and net("2001:db8::1:a:b:c") == "2001:db8:0:0"
    assert net("2001::4266:0:0:e5d0:a998") == "2001:0:0:4266" and net("2001::7:1:a:b:c:d") == "2001:0:7:1"
    assert net("2001:db8::9:1:a:b:c") == "2001:db8:0:9"
    assert net("192.0.2.7") is None and len(seen) > 3
