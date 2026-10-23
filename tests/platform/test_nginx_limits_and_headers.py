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
    for path in ("/auth/password/abc", "/invite/abc", "/join/abc", "/account/confirm-email/abc", "/privacy/delete/12-x"):
        m = re.search(pattern, path)
        assert m and "abc" not in value.replace("$fixed", m.group("fixed")) and "12-x" not in value
    assert not re.search(pattern, "/surveys/1/editor")
