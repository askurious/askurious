"""Tokens of links that open something by themselves do not reach the app's log (security and code audit
2026-10-13, KR-9): a 500 on a valid link wrote "Exception on /d/<token> [GET]" with a token that still works."""
import logging

from app import log_mask

TOKEN = "Abc123_-xyzXYZ0987654321abcdEFGH"


def test_tokens_of_link_paths_are_masked():
    for path in (f"/d/{TOKEN}", f"/d/{TOKEN}/state", f"/p/{TOKEN}.sig", f"/q/{TOKEN}", f"/invite/{TOKEN}",
                 f"/join/{TOKEN}", f"/auth/password/{TOKEN}", f"/account/confirm-email/{TOKEN}", f"/s/abc123/e/{TOKEN}", f"/s/abc123/no-mail/{TOKEN}",
                 f"https://askurious.de/d/{TOKEN}?x=1"):
        assert TOKEN not in log_mask.mask(path) and "***" in log_mask.mask(path), path
    for path in ("/surveys/12/results", "/d/short", "/s/abc123", "/s/abc123/2"):
        assert log_mask.mask(path) == path


def test_the_apps_log_masks_an_exception_on_such_a_link(app, caplog):
    app.config["PROPAGATE_EXCEPTIONS"] = False

    @app.route("/d/<token>/boom")
    def boom(token):
        raise RuntimeError("boom")

    with caplog.at_level(logging.ERROR, logger=app.logger.name):
        assert app.test_client().get(f"/d/{TOKEN}/boom").status_code == 500
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "Exception on /d/***/boom" in text and TOKEN not in text


def test_tokens_in_further_paths_and_in_parameters_are_masked():
    """Confirmation links, the link to join an organisation, the personal link of an invitation mail, the handover
    to the participants' address and the return from an external section."""
    for text in (f"Exception on /signup/confirm/{TOKEN} [GET]", f"Exception on /invite/confirm/{TOKEN} [POST]",
                 f"/org/join/{TOKEN}", f"GET /s/abc123?einladung={TOKEN} HTTP/1.1", f"/s/abc123?lang=de&uebergabe={TOKEN}",
                 f"/s/abc123/back?t={TOKEN}"):
        assert TOKEN not in log_mask.mask(text) and "***" in log_mask.mask(text), text
    assert log_mask.mask("/org/12/people?t=abc") == "/org/12/people?t=abc"        # no token: as it is
    assert log_mask.mask("/surveys/1/editor?einladung=kurz") == "/surveys/1/editor?einladung=kurz"


def test_a_traceback_and_gunicorns_log_are_masked_too(app):
    record = logging.LogRecord("gunicorn.error", logging.ERROR, __file__, 1, "Error handling request %s",
                               (f"/d/{TOKEN}",), None)
    record.exc_text = f'File "x.py", line 1\n  GET /invite/{TOKEN}\nValueError'
    assert any(isinstance(f, log_mask.MaskTokens) for f in logging.getLogger("gunicorn.error").filters)
    log_mask.MaskTokens().filter(record)
    assert TOKEN not in record.getMessage() and TOKEN not in record.exc_text and record.exc_text.count("\n") == 2
    inner = logging.Formatter("%(message)s")
    try:
        raise ValueError(f"/q/{TOKEN}")
    except ValueError:
        import sys

        failing = logging.LogRecord("x", logging.ERROR, __file__, 1, "boom", (), sys.exc_info())
    text = log_mask.MaskingFormatter(inner).format(failing)
    assert TOKEN not in text and "ValueError" in text and "\n" in text


def test_the_cron_key_in_an_address_is_masked_and_not_taken(app, caplog):
    """A call that still sends ?key= leaves no key in the app's log, and the route does not take it."""
    for path in (f"/cron/close?key={TOKEN}", f"GET /cron/close?x=1&key={TOKEN} HTTP/1.1"):
        assert TOKEN not in log_mask.mask(path) and "key=***" in log_mask.mask(path), path
    assert log_mask.mask("/constructs/?key=abc") == "/constructs/?key=abc"
    app.config["CRON_KEY"] = TOKEN
    assert app.test_client().post(f"/cron/close?key={TOKEN}").status_code == 404
    assert app.test_client().post("/cron/close", headers={"X-Cron-Key": TOKEN}).status_code == 200


def test_line_breaks_and_control_characters_are_escaped():
    """Audit 2026-10-18, CFG-12 (CWE-117): a path with "%0A" wrote a forged line into the log (Flask's "Exception on
    <path>", the handler of numbers out of range). The line break is written as an escape; a tab stays."""
    forged = '/x\nsecurity {"event":"sign_in","account":1}\r\x1b[2J\x85\u2028end\tok'
    out = log_mask.mask(forged)
    assert "\n" not in out and "\r" not in out and "\x1b" not in out and "\x85" not in out and "\u2028" not in out
    assert out == r'/x\nsecurity {"event":"sign_in","account":1}\r\x1b[2J\x85\u2028end' + "\tok"
    assert log_mask.mask(f"/d/{TOKEN}\n") == r"/d/***\n"


def test_the_apps_log_writes_a_path_with_a_line_break_on_one_line(app, caplog):
    app.config["PROPAGATE_EXCEPTIONS"] = False

    @app.route("/boom/<rest>")
    def boom_path(rest):
        raise RuntimeError("boom")

    with caplog.at_level(logging.ERROR, logger=app.logger.name):
        assert app.test_client().get("/boom/x%0Asecurity%20forged").status_code == 500
    lines = [r.getMessage() for r in caplog.records if "Exception on" in r.getMessage()]
    assert lines and all("\n" not in line for line in lines) and r"x\nsecurity forged" in lines[0]


def test_monitoring_and_status_tokens_are_masked():
    """Audit 2026-10-22, E-07: the token of the monitoring feed and of a brand's status stood in the path unmasked."""
    for path in (f"/api/monitoring/{TOKEN}", f"/status/{TOKEN}.json"):
        assert TOKEN not in log_mask.mask(path) and "***" in log_mask.mask(path), path
    assert log_mask.mask("/status") == "/status"


def test_the_security_log_goes_to_daily_files_too(app, tmp_path):
    """Audit 2026-10-22, E-04: the security events stood only in the container's output, rotated after 60 MB. They go
    to a file per day as well, and the housekeeping removes those older than SECURITY_LOG_DAYS."""
    import os
    import time

    from app import security_log

    app.config.update(SECURITY_LOG_DIR=str(tmp_path), SECURITY_LOG_DAYS=90)
    for handler in list(security_log.log.handlers):
        security_log.log.removeHandler(handler)
    try:
        with app.app_context():
            security_log.event("sign_in", account=7, via="password")
            today = tmp_path / time.strftime("security-%Y-%m-%d.log", time.gmtime())
            assert '"event":"sign_in"' in today.read_text(encoding="utf-8")
            old = tmp_path / "security-2000-01-01.log"
            old.write_text("x", encoding="utf-8")
            assert security_log.sweep() == 1 and not old.exists() and today.exists()
    finally:
        for handler in list(security_log.log.handlers):
            security_log.log.removeHandler(handler)
        assert not os.environ.get("SECURITY_LOG_DIR")
