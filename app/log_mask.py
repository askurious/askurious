"""Tokens in the paths of links that open something by themselves are masked in the app's log (audit 2026-10-13, KR-9).

A 500 on a valid link wrote the whole path into the log ("Exception on /d/<token> [GET]"), and with it a token that
still works: a dashboard, a preview link, a quota link, an invitation or a join link, the link to set a password, a
diary entry or the link without mail. The path stays readable, the token becomes "***". Only parts of at least 16
characters count as a token, so ids and short codes stay as they are. Since the audit of 2026-10-22 (E-07) also the
monitoring feed (/api/monitoring/<token>) and the status of a brand (/status/<token>.json).
"""
import logging
import re

TOKEN_PATH = re.compile(r"(/(?:auth/password|account/confirm-email|invite/confirm|invite|join|signup/confirm|signup|joinorg|org/join|d|p|q|api/monitoring|status)/|/s/[^/\s]+/(?:e|no-mail)/)([A-Za-z0-9_.~%-]{16,})")
# a key behind /cron/ in an address: the route takes it in the header X-Cron-Key only (surveys/cron.py), but a call
# that still sends ?key= must not leave it in the log
CRON_KEY = re.compile(r"(/cron/[^\s?]*\?(?:[^\s#]*?&)?key=)([^&\s#\"']+)")
# tokens that travel as a parameter of an address: the personal link of an invitation mail (einladung), the handover
# from the main address to the participants' one (uebergabe), the return from an external section (/back?t=)
QUERY_TOKEN = re.compile(r"([?&](?:einladung|uebergabe)=|/back\?(?:[^\s#]*?&)?t=)([^&\s#\"']{8,})")
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
    text = QUERY_TOKEN.sub(lambda m: m.group(1) + "***", text)
    return CONTROL.sub(_escape, text)


class MaskTokens(logging.Filter):
    """A logging filter that masks the tokens in the message and in its string arguments."""

    def filter(self, record):
        record.msg = mask(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(mask(a) for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: mask(v) for k, v in record.args.items()}
        if record.exc_text:  # a traceback formatted already (it may quote the address)
            record.exc_text = _mask_lines(record.exc_text)
        return True


def _mask_lines(text):
    """mask() for a text of several lines that stay lines (a traceback)."""
    return "\n".join(mask(line) for line in str(text).split("\n"))


class MaskingFormatter(logging.Formatter):
    """Wraps a handler's formatter: the traceback it adds to a record is masked like the message."""

    def __init__(self, inner):
        super().__init__()
        self.inner = inner

    def format(self, record):
        return _mask_lines(self.inner.format(record))


def install(app):
    """Put the filter on the app's logger (Flask writes "Exception on <path>" there), on werkzeug's and on gunicorn's
    (its error log names the address of a request it had to end), and let their handlers mask what they format (the
    traceback of an exception)."""
    for logger in (app.logger, logging.getLogger("werkzeug"), logging.getLogger("gunicorn.error")):
        if not any(isinstance(f, MaskTokens) for f in logger.filters):
            logger.addFilter(MaskTokens())
        for handler in logger.handlers:
            if not isinstance(handler.formatter, MaskingFormatter):
                handler.setFormatter(MaskingFormatter(handler.formatter or logging.Formatter()))
