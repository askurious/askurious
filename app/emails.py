"""The one check whether a text is an e-mail address, for every field that takes one: sign-in and accounts,
invitations, the contact e-mail of the consent and of a live session, links by mail, and the free text answer with
the check "E-Mail-Adresse" (Ralf, 2026-10-11: one rule for all).

The local part (before the @) is plain ASCII only (RFC 5322 atext and dots), the domain may be written with umlauts
and is stored in its ASCII form (IDNA, "xn--"). A mail server without SMTPUTF8 refuses any other character, and one
such address held up the whole mail queue (audit 2026-10-11, AU1/M8). Invisible, direction-changing and full-width
characters are refused, so no address looks like another one in a list."""
import re
import unicodedata

MAX_LENGTH = 254  # the longest address the mail standards allow

# one plain address: no space, no character that separates or quotes addresses in a mail header (, ; < > " ( ) [ ] \),
# no control character; otherwise "root,x@example.org" became two recipients (audit 2026-10-09b, E5)
_PART = r'[^@\s,;<>"()\[\]\\\x00-\x1f\x7f]+'
EMAIL_RE = re.compile(rf"^{_PART}@{_PART}\.{_PART}\Z")
# the local part: RFC 5322 atext and dots, ASCII only (the separators above are not in it)
_LOCAL_RE = re.compile(r"^[a-z0-9!#$%&'*+/=?^_`{|}~.-]+\Z")
# a domain in ASCII: labels of letters, digits and hyphens
_DOMAIN_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{1,63}\Z")


def _suspicious(value):
    """True for a character that hides or disguises: format characters (zero width, direction marks: Cf), spaces
    other than the plain one (Zs), unassigned, private or surrogate code points, and every character that NFKC writes
    differently (full width "ａ", ligatures)."""
    if unicodedata.normalize("NFKC", value) != value:
        return True
    return any(unicodedata.category(ch) in ("Cf", "Zs", "Zl", "Zp", "Cc", "Co", "Cs", "Cn") for ch in value)


def _ascii_domain(domain):
    """The domain in ASCII (IDNA 2008, "xn--" labels); None if it cannot be one."""
    if domain.isascii():
        return domain if _DOMAIN_RE.match(domain) else None
    try:
        try:
            import idna  # IDNA 2008, comes with requests
        except ImportError:  # pragma: no cover - the codec of the standard library (IDNA 2003)
            out = domain.encode("idna").decode("ascii")
        else:
            out = idna.encode(domain, uts46=False).decode("ascii")
    except Exception:  # noqa: BLE001 - idna.IDNAError, UnicodeError: no domain
        return None
    return out if _DOMAIN_RE.match(out) else None


def normal_email(value):
    """The address as stored and compared: trimmed, lower case, the domain in ASCII; None if it is no address."""
    from email.utils import getaddresses

    value = (value or "").strip().lower()
    if len(value) > MAX_LENGTH or _suspicious(value) or not EMAIL_RE.match(value):  # the length first: the pattern is quadratic in it
        return None
    local, _, domain = value.rpartition("@")
    if not _LOCAL_RE.match(local) or local.startswith(".") or local.endswith(".") or ".." in local:
        return None
    domain = _ascii_domain(domain)
    if domain is None:
        return None
    value = f"{local}@{domain}"
    if len(value) > MAX_LENGTH:
        return None
    # and what the mail library reads out of it is exactly this one address
    return value if getaddresses([value]) == [("", value)] else None


def wire_address(value):
    """The address as it goes into a mail header (To, Reply-To): normal_email, also for addresses stored before the
    rule above; None if it cannot go out (then the mail is refused, or written without Reply-To)."""
    return normal_email(value)


def is_email(value):
    """True if `value` (trimmed) is one e-mail address; the text itself stays as it was typed."""
    return normal_email(value) is not None
