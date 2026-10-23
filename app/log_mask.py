"""Tokens in the paths of links that open something by themselves are masked in the app's log (audit 2026-10-13, KR-9).

A 500 on a valid link wrote the whole path into the log ("Exception on /d/<token> [GET]"), and with it a token that
still works: a dashboard, a preview link, a quota link, an invitation or a join link, the link to set a password, a
diary entry or the link without mail. The path stays readable, the token becomes "***". Only parts of at least 16
characters count as a token, so ids and short codes stay as they are. Since the audit of 2026-10-22 (E-07) also the
monitoring feed (/api/monitoring/<token>) and the status of a brand (/status/<token>.json).
"""
import logging
import re

TOKEN_PATH = re.compile(r"(/(?:auth/password|account/confirm-email|invite|join|d|p|q|api/monitoring|status)/|/s/[^/\s]+/(?:e|no-mail)/)([A-Za-z0-9_.~%-]{16,})")
# the key of the cron route, still accepted in the address for scheduled tasks set up before the header X-Cron-Key
# (surveys/cron.py; code review 2026-10-15, H9)
CRON_KEY = re.compile(r"(/cron/[^\s?]*\?(?:[^\s#]*?&)?key=)([^&\s#\"']+)")
# what could start a line of its own or hide text in a terminal: the C0 controls but the tab, DEL, the C1 controls
# (NEL among them) and the Unicode line and paragraph separators (audit 2026-10-18, CFG-12)
CONTROL = re.compile(r"[\x00-\x08\x0a-\x1f\x7f-\x9f  ]")
_NAMED = {"\n": r"\n", "\r": r"\r"}


def _escape(match):
    char = match.group(0)
    return _NAMED.get(char) or (f"\\x{ord(char):02x}" if ord(char) < 0x100 else f"\\u{ord(char):04x}")


def mask(text):
    """`text` with the token of each such path, and the cron key in an address, replaced by ***, and its line breaks
    and other control characters written as escapes (a backslash and "n"): a path with "%0A" in a 500 ("Exception on
    /x<line break>security {...}") or in the handler of numbers out of range forged a log line of its own before
    (audit 2026-10-18, CFG-12; CWE-117). A tab stays."""
    if not isinstance(text, str):
        return text
    text = CRON_KEY.sub(lambda m: m.group(1) + "***", TOKEN_PATH.sub(lambda m: m.group(1) + "***", text))
    return CONTROL.sub(_escape, text)


class MaskTokens(logging.Filter):
    """A logging filter that masks the tokens in the message and in its string arguments."""

    def filter(self, record):
        record.msg = mask(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(mask(a) for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: mask(v) for k, v in record.args.items()}
        return True


def install(app):
    """Put the filter on the app's logger (Flask writes "Exception on <path>" there) and on werkzeug's."""
    for logger in (app.logger, logging.getLogger("werkzeug")):
        if not any(isinstance(f, MaskTokens) for f in logger.filters):
            logger.addFilter(MaskTokens())
