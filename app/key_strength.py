"""Are the server's keys strong enough? (audit 2026-10-13, KR-5)

SECRET_KEY signs the sessions and, with TOKEN_KEY, POOL_KEY and DIARY_KEY, locks or derives what the database keeps
(OAuth tokens, second factors, signing keys, deletion codes). A short or made-up key ("askurious2026") can be guessed
offline from a copy of the database. Every server got a random one so far (deploy-docker.sh: openssl rand -hex 32):

- shorter than MIN_LENGTH characters, or one character over and over (no randomness at all, "trivial"): the app
  refuses to start outside this computer (create_app), as with the development key. No random key of any usual tool
  is like that.
- shorter than GOOD_LENGTH, with little variety (less than GOOD_BITS by the characters' frequencies), shaped like a
  passphrase (words with blanks or dashes between them) or little more than words of a guess list (the site's own
  names, "secret", "key", "production", years, the 10,000 most frequent passwords): "weak" (the last two since the
  audit of 2026-10-18, CFG-02: such a key could be guessed offline from a signed cookie, which every participant
  gets). It starts, so that a deploy never stops a server whose keys worked so far, but says so loudly in the log at
  every start and on Administration > Server; the operators change it (BETRIEB.md).

A new key: python -c "import secrets; print(secrets.token_urlsafe(48))" (BETRIEB.md, "Schlüssel wechseln").
"""
import functools
import math
import re
from collections import Counter

MIN_LENGTH = 16
GOOD_LENGTH = 32
GOOD_BITS = 128
# what must be left of a key besides the words of the guess list: a random key with a word in it by chance keeps far
# more (48 hex digits with a five-letter word: 43 characters, some 160 bits)
REST_LENGTH = 24
REST_BITS = 96

# the settings checked; the *_FALLBACKS lists as well (an old key there opens as much as the current one)
NAMES = ("SECRET_KEY", "TOKEN_KEY", "POOL_KEY", "DIARY_KEY", "RESEARCHER_SESSION_KEY", "PARTICIPANT_TOKEN_KEY")
WARN_NAMES = ("COMPUTE_SECRET", "CRON_KEY")


def bits(key):
    """Shannon's estimate of the key's randomness: its length times the entropy of its characters' frequencies."""
    if not key:
        return 0.0
    n = len(key)
    return n * -sum(c / n * math.log2(c / n) for c in Counter(key).values())


# words a made-up key is built of (besides the context words and the frequent passwords of app/passwords.py)
GUESS_WORDS = (
    "secret", "secrets", "production", "prod", "staging", "develop", "development", "change", "changeme", "insecure",
    "default", "flask", "django", "server", "token", "session", "signing", "random", "string", "super", "very",
    "long", "strong", "please", "replace", "geheimer", "schluessel", "schlüssel", "sicher", "passphrase",
)
_SEPARATORS = re.compile(r"[\s\-_.,;:+/|]+")


def _passphrase(key):
    """Words with blanks or separators between them ("correct horse battery staple"): a random key of the usual tools
    has no blank, and between its "-" and "_" (token_urlsafe) stand mixed letters and digits, never whole words."""
    if re.search(r"\s", key):
        return True
    parts = [p for p in _SEPARATORS.split(key) if p]
    return len(parts) >= 3 and all(p.isdigit() or (p.isalpha() and (p.islower() or p.istitle() or p.isupper()))
                                   for p in parts)


@functools.lru_cache(maxsize=1)
def _words():
    from app import passwords

    return frozenset({w for w in GUESS_WORDS} | {w for w in passwords.CONTEXT if len(w) >= 4}
                     | {w for w in passwords._common() if len(w) >= 5 and w.isalpha()})


def _rest(key):
    """The key without the words of the guess list and, once there was such a word, without the years 1900 to 2099
    and the separators between them (in a random key of digits a "2015" is no year). The key as it is without one."""
    text = key.casefold()
    found = sorted((w for w in _words() if w in text), key=len, reverse=True)  # "production" before "prod"
    if not found:
        return key
    for word in found:
        text = text.replace(word, "")
    text = re.sub(r"(?:19|20)\d\d", "", text)
    return _SEPARATORS.sub("", text)


def verdict(key):
    """None for a strong key, "short" below MIN_LENGTH and "trivial" for one character over and over (0 bits; both
    refused on a server), "weak" below GOOD_LENGTH or GOOD_BITS, shaped like a passphrase, or with too little left
    besides words of the guess list (warned)."""
    if len(key) < MIN_LENGTH:
        return "short"
    if len(set(key)) == 1:
        return "trivial"
    if len(key) < GOOD_LENGTH or bits(key) < GOOD_BITS or _passphrase(key):
        return "weak"
    rest = _rest(key)
    if rest != key and (len(rest) < REST_LENGTH or bits(rest) < REST_BITS):
        return "weak"
    return None


def findings(config):
    """[(name, "short"|"trivial"|"weak")] for every key that is set and not strong; empty settings are not checked (TOKEN_KEY,
    POOL_KEY and DIARY_KEY fall back to SECRET_KEY). Never contains a key itself."""
    out = []
    for name in NAMES:
        values = [(name, config.get(name) or "")]
        values += [(f"{name}_FALLBACKS", v) for v in (config.get(f"{name}_FALLBACKS") or [])]
        for label, value in values:
            found = verdict(value) if value else None
            if found and (label, found) not in out:
                out.append((label, found))
    return out


def check(app, local):
    """At the start (create_app): refuses keys that are too short or trivial on a server, names weak ones in the log.
    The development key is checked before this (create_app) and passes here on this computer."""
    from config import DEV_SECRET_KEY

    found = [(name, v) for name, v in findings(app.config)
             if not (name == "SECRET_KEY" and app.config.get("SECRET_KEY") == DEV_SECRET_KEY)]
    if local:
        return found
    short = [name for name, v in found if v == "short"]
    if short:
        raise RuntimeError(f"{', '.join(short)} shorter than {MIN_LENGTH} characters: guessable from a copy of the "
                           "database. Set a random key in .env, e.g. python -c \"import secrets; "
                           "print(secrets.token_urlsafe(48))\" (deploy/docker/BETRIEB.md, rotating keys)")
    trivial = [name for name, v in found if v == "trivial"]
    if trivial:
        raise RuntimeError(f"{', '.join(trivial)} is one character over and over (no randomness at all). Set a random "
                           "key in .env, e.g. python -c \"import secrets; print(secrets.token_urlsafe(48))\" "
                           "(deploy/docker/BETRIEB.md, rotating keys)")
    for name, _ in found:
        app.logger.error("%s is weak (under %d characters, little variety or made-up words): replace it with a random "
                         "key (deploy/docker/BETRIEB.md, rotating keys)", name, GOOD_LENGTH)
    # the shared key of the Rechenknecht and the key of the cron route: named when not strong, never stopping the start
    # (audit 2026-10-22, E-08: they were not checked at all)
    for name in WARN_NAMES:
        value = app.config.get(name) or ""
        if value and verdict(value):
            app.logger.error("%s is %s: replace it with a random key (deploy/docker/BETRIEB.md, rotating keys)", name,
                             verdict(value))
    for gap in separation_gaps(app.config):
        app.logger.warning("Sides not separated: %s (deploy/docker/BETRIEB.md, audit 2026-10-22, E-01)", gap)
    return found


def separation_gaps(config):
    """What still ties the participants' side to the researchers' where the two run apart (SIDE set, the Docker
    server): without RESEARCHER_SESSION_KEY a break-in on the participants' side signs researchers' and admins'
    sessions with SECRET_KEY; without PARTICIPANT_TOKEN_KEY it needs TOKEN_KEY, which opens every stored secret.
    Before, nothing said so (audit 2026-10-22, E-01). Names only, never a key. RESEARCHER_SESSION_KEY is the
    researchers' container's: the participants' never get it, so only that side names it missing."""
    if not config.get("SIDE"):
        return []
    gaps = []
    if config.get("SIDE") == "researcher" and not config.get("RESEARCHER_SESSION_KEY"):
        gaps.append("RESEARCHER_SESSION_KEY is empty")
    if not config.get("PARTICIPANT_TOKEN_KEY"):
        gaps.append("PARTICIPANT_TOKEN_KEY is empty")
    return gaps
