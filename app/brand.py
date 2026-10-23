"""A second name for the same app under a second domain (Ralf, 2026-10-09: polltergeist.de as "Polltergeist").

BRANDS in .env names further domains and the name the app carries there, e.g. "polltergeist.de=Polltergeist"
(several separated by commas). A request to such a domain or one of its sub-domains (ask.polltergeist.de) shows that
name wherever the app says "Askurious", and the links the app builds stay on that domain: PUBLIC_BASE_URL and
PARTICIPANT_BASE_URL with their domain swapped. Only configured domains count, so a forged Host header never decides
an address (code review H10). Everything else, also work outside a request (cron, mail queue), is the main name.

SITE_BRAND (Ralf, 2026-10-09, datenbank-je-marke-konzept.md) is for an installation of its own for one brand, with
its own database: "polltergeist.de=POLLtergeist" makes that the name of everything this installation does, also
outside a request (mails of the queue, housekeeping, estimations), with PUBLIC_BASE_URL on that domain. Empty, as
everywhere so far: the main name, as above.

The name is swapped only in the platform's own texts, never in what researchers and participants wrote (question
texts, titles, answers) and never in downloads (audit 2026-10-09b, M1): in the texts of de_en(), tr() and ui()
before their values are filled in (app/lang.py, app/ui_i18n.py), and in what templates write literally, through
BrandExtension (`{{ brand_name() }}` in place of the word, when a template is compiled). A download (a file of the
export, a CSV, a ZIP, a Word file; is_download) keeps the main name, so the same export reads the same under every
domain. The brand is found once per request and BRANDS parsed once per value (P8).
"""
import contextvars
import functools
import re

from flask import current_app, has_app_context, has_request_context, request
from jinja2.ext import Extension
from jinja2.lexer import Token
from markupsafe import Markup

from config import url_host

NAME = "Askurious"
_WORD = re.compile(r"\b" + NAME + r"\b")
_override = contextvars.ContextVar("brand", default=None)


@functools.lru_cache(maxsize=16)
def _parsed(value):
    out = []
    for part in (value or "").split(","):
        domain, _, name = part.partition("=")
        domain, name = domain.strip().lower().rstrip("."), name.strip()
        if domain and name:
            out.append((domain, name))
    return tuple(out)


def parse(value):
    """[(domain, name)] from "a.de=A, b.de=B"; entries without a name or domain are passed over. Parsed once per
    value and process (audit 2026-10-09b, P8)."""
    return list(_parsed(value or ""))


def _main_domain():
    return url_host(current_app.config["PUBLIC_BASE_URL"])


def _match(host):
    host = (host or "").lower().rstrip(".")
    for domain, name in _parsed(current_app.config.get("BRANDS") or ""):
        if host == domain or host.endswith("." + domain):
            return domain, name
    return None


def _site():
    """(domain, name) of SITE_BRAND, the brand of the whole installation; None without one."""
    if not has_app_context():
        return None
    found = _parsed(current_app.config.get("SITE_BRAND") or "")
    return found[0] if found else None


# downloads whose rule has no file name: they keep the main name like the others (is_download)
DOWNLOAD_ENDPOINTS = {"dashboard.export", "surveys.prereg_file", "surveys.prereg_stamp", "surveys.snapshot_file",
                      "interview.version_file"}
_DOWNLOAD_FILE = re.compile(r"\.(<[^>]*>|csv|zip|docx|sav|omv|rds|R|md|py|qdc|qdpx|txt|bib|ris|sps)$|"
                            r"^(survey|seal)\.json$")
_KEY = "askurious.brand"  # the brand of a request, found once (P8); in its WSGI environ, which lives as long as it


def is_download(rule=None, endpoint=None):
    """True for a request (or a URL rule) that answers with a file to save: an export, a CSV, a ZIP, a Word file.
    Those keep the main name (M1): the same export reads the same under every domain."""
    if rule is None and has_request_context():
        rule, endpoint = request.url_rule, request.endpoint
    if rule is None:
        return False
    text = getattr(rule, "rule", rule)
    endpoint = endpoint or getattr(rule, "endpoint", None)
    if endpoint in DOWNLOAD_ENDPOINTS:
        return True
    if endpoint in ("public.robots", "public.favicon", "static"):
        return False
    return bool(_DOWNLOAD_FILE.search(text.rstrip("/").rsplit("/", 1)[-1]))


def current():
    """(domain, name) of the brand this request came to; None for the main name. An installation with SITE_BRAND
    has that brand wherever no other one is asked for. Found once per request (audit 2026-10-09b, P8)."""
    forced = _override.get()
    if forced is not None:
        return forced or _site()
    if not has_request_context():
        return _site()
    environ = request.environ
    if _KEY not in environ:
        environ[_KEY] = (_match(url_host("//" + request.host)) or _site(), is_download())
    return environ[_KEY][0]


def _named():
    """The brand whose name the texts carry: current(), except in a download, which keeps the main name (M1). Its
    links stay on the domain of the request (public_base and the like use current())."""
    b = current()
    if b and _override.get() is None and has_request_context() and request.environ[_KEY][1]:
        return _site()
    return b


class use:
    """with brand.use("polltergeist.de"): work outside a request under that brand (None or "": the main name, which
    is SITE_BRAND's brand in an installation that has one)."""

    def __init__(self, domain):
        self.domain = domain

    def __enter__(self):
        found = _match(self.domain) if self.domain else None
        self.token = _override.set(found or ())
        return self

    def __exit__(self, *exc):
        _override.reset(self.token)


def name():
    b = _named()
    return b[1] if b else NAME


def domain():
    """The domain of this brand, or None for the main one (whose addresses come from .env as they are)."""
    b = current()
    return b[0] if b else None


def _swap(url):
    """PUBLIC_BASE_URL or PARTICIPANT_BASE_URL with the main domain replaced by this brand's (only as the host's end)."""
    return rehost(url, domain(), _main_domain())


def rehost(url, domain, main):
    """`url` (or a bare host name) with the domain `main` at the end of its host replaced by `domain`:
    https://ask.askurious.de -> https://ask.polltergeist.de; unchanged when its host is not under `main`."""
    if not domain or not url or not main:
        return url
    host = url_host(url if "/" in url else "//" + url)
    if host == main or host.endswith("." + main):
        new_host = host[: len(host) - len(main)] + domain
        return url.replace(host, new_host, 1)
    return url


def public_base():
    """The researchers' address of this request's brand (no slash at the end)."""
    return _swap(current_app.config["PUBLIC_BASE_URL"])


def participant_base_url():
    """PARTICIPANT_BASE_URL of this request's brand; "" when the installation has none."""
    return _swap(current_app.config.get("PARTICIPANT_BASE_URL") or "")


def status_url():
    """STATUS_URL of this request's brand: status.polltergeist.de under polltergeist.de (nginx and certbot know each
    brand's status name, deploy/docker); "" when the installation has none."""
    return _swap(current_app.config.get("STATUS_URL") or "")


def apply(text):
    """The text with the main name replaced by this brand's; unchanged under the main name and for non-str. Meant
    for the platform's own texts before values are filled in (M1); Markup stays Markup."""
    if not isinstance(text, str) or NAME not in text:
        return text
    b = _named()
    if not b:
        return text
    out = _WORD.sub(b[1], text)
    return Markup(out) if isinstance(text, Markup) else out


class BrandExtension(Extension):
    """Jinja: the word "Askurious" in a template's own text becomes {{ brand_name() }} when the template is
    compiled, so a page carries the brand of its domain without a single value (a question text, a title, an
    answer) being touched afterwards (M1). Texts inside {{ de_en(...) }} and {{ ui(...) }} are swapped there."""

    def filter_stream(self, stream):
        for token in stream:
            if token.type != "data" or NAME not in token.value:
                yield token
                continue
            pos = 0
            for m in _WORD.finditer(token.value):
                if m.start() > pos:
                    yield Token(token.lineno, "data", token.value[pos:m.start()])
                yield Token(token.lineno, "variable_begin", "{{")
                yield Token(token.lineno, "name", "brand_name")
                yield Token(token.lineno, "lparen", "(")
                yield Token(token.lineno, "rparen", ")")
                yield Token(token.lineno, "variable_end", "}}")
                pos = m.end()
            if pos < len(token.value):
                yield Token(token.lineno, "data", token.value[pos:])


def install(app):
    """BrandExtension and brand_name() in the templates of `app` (the app and the status page)."""
    app.jinja_env.add_extension(BrandExtension)
    app.jinja_env.globals["brand_name"] = name
    # the main brand's wordmark is an image (templates/_wordmark.html); compared here, since BrandExtension would
    # turn the word in a template into brand_name()
    app.jinja_env.globals["main_brand"] = lambda: name() == NAME


def site():
    """A short key of this request's brand for templates and styles (data-site on <html>): its name in small letters,
    e.g. "polltergeist"; "" under the main name."""
    b = current()
    return re.sub(r"[^a-z0-9-]", "", b[1].lower()) if b else ""


DARK_SITES = {"polltergeist"}  # brands whose pages are dark until a person chooses otherwise (Ralf, 2026-10-10)


def default_mode():
    """Light or dark for someone who has chosen neither (data-default-mode on <html>, theme-init.js, new accounts):
    "dark" under POLLtergeist, "light" under every other name. A choice of the person always wins."""
    return "dark" if site() in DARK_SITES else "light"
