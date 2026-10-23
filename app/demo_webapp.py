"""Example web app for an external section ("Externer Abschnitt", app/external.py): the weather demo
(Philipp, 2026-10-08). The person names a city, the app asks Open-Meteo for the current temperature (free, no key)
and sends city and temperature back to the survey through the return link.

In the flow, an external section with
    address  https://ask.askurious.de/demo/weather?lang={{lang}}&return={{return}}
    values   city (text), temperature (number)
uses it. Opened without a return link (by hand, to try it), it works the same but sends nothing anywhere.

Unsigned on purpose: the signature of the values (docs/externer-abschnitt-signatur.md) needs the key of the
section on the server of the web app, which this demo for every survey cannot have. It only sends the person back
to a return link of this installation (…/s/<code>/back?t=…), never to another address.

Open to anyone, so it asks Open-Meteo sparingly (code audit 2026-10-08, W11): answers stay CACHE_SECONDS in a small
cache (CACHE_SIZE cities, the oldest goes first), each sender may look up PER_SENDER new cities per WINDOW and all
together ALL_SENDERS, and at most LOOKUPS_AT_ONCE lookups run at once. Beyond that the page says that the weather
service cannot be reached, and the person can still go back to the survey."""
import re
import threading
import time
from collections import OrderedDict
from urllib.parse import quote, urlsplit

import requests
from flask import Blueprint, render_template, request

from app.hosts import participant_base
from app.ui_i18n import html_dir, html_lang, tr

bp = Blueprint("demo_webapp", __name__, url_prefix="/demo")

GEOCODING = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST = "https://api.open-meteo.com/v1/forecast"
TIMEOUT = (3, 4)          # seconds to connect, to wait for each part of the answer
CACHE_SECONDS = 600       # one question per city and 10 minutes is enough for a demo
CACHE_SIZE = 500
WINDOW = 600
PER_SENDER = 20           # new cities per sender and WINDOW
ALL_SENDERS = 600         # new cities of all senders together per WINDOW (Open-Meteo's free use is per server)
LOOKUPS_AT_ONCE = 4
_cache = OrderedDict()
_cache_lock = threading.Lock()
_senders = {}
_everyone = {}
_at_once = threading.BoundedSemaphore(LOOKUPS_AT_ONCE)
RETURN_PATH = re.compile(r"^/s/[A-Za-z0-9_-]{1,64}/back\Z")
DECIMAL_POINT = ("en", "zh-Hans", "ar")


def return_link(raw):
    """The return link if it is one of this installation's, else None (no redirect to an address of anyone's)."""
    if not raw or len(raw) > 1000:
        return None
    base, parts = urlsplit(participant_base()), urlsplit(raw)
    if (parts.scheme, parts.netloc) != (base.scheme, base.netloc) or not RETURN_PATH.match(parts.path):
        return None
    if not re.fullmatch(r"t=[A-Za-z0-9_-]{20,400}", parts.query or ""):
        return None
    return raw


def temperature(city, lang):
    """(place name, °C) for a city, or (None, None) if no place has that name; raises OSError if Open-Meteo does
    not answer."""
    key = (city.casefold(), lang)
    with _cache_lock:
        hit = _cache.get(key)
        if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
            _cache.move_to_end(key)
            return hit[1]
    _allowed()
    if not _at_once.acquire(blocking=False):
        raise OSError("too many lookups at once")
    try:
        places = requests.get(GEOCODING, params={"name": city, "count": 1, "language": html_lang(lang)[:2],
                                                 "format": "json"}, timeout=TIMEOUT)
        places.raise_for_status()
        found = (places.json().get("results") or [None])[0]
        if not found:
            result = (None, None)
        else:
            weather = requests.get(FORECAST, params={"latitude": found["latitude"], "longitude": found["longitude"],
                                                     "current": "temperature_2m"}, timeout=TIMEOUT)
            weather.raise_for_status()
            name = ", ".join(x for x in (found.get("name"), found.get("country")) if x)
            result = (name, float(weather.json()["current"]["temperature_2m"]))
    except (requests.RequestException, ValueError, KeyError, TypeError) as e:
        raise OSError(str(e)) from e
    finally:
        _at_once.release()
    with _cache_lock:
        _cache[key] = (time.monotonic(), result)
        _cache.move_to_end(key)
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)
    return result


def _allowed():
    """Count one lookup at Open-Meteo for this sender and for everyone (app/limits.py: in memory, hashed, per
    process); OSError once either has its share of this WINDOW."""
    from app import limits

    sender = limits._sender()
    if sender is not None and not limits._take(_senders, sender, PER_SENDER, WINDOW):
        raise OSError("too many lookups from this sender")
    if not limits._take(_everyone, "all", ALL_SENDERS, WINDOW):
        raise OSError("too many lookups")


@bp.route("/weather")
def weather():
    lang = request.args.get("lang", "de")
    lang = lang if re.fullmatch(r"[a-zA-Z-]{2,8}", lang) else "de"
    back = return_link(request.args.get("return"))
    city = (request.args.get("stadt") or "").strip()[:100]
    place = temp = problem = None
    if city:
        try:
            place, temp = temperature(city, lang)
            if place is None:
                problem = tr(lang, "Diese Stadt wurde nicht gefunden. Bitte versuchen Sie eine andere.",
                             "This city was not found. Please try another one.")
        except OSError:
            problem = tr(lang, "Der Wetterdienst ist gerade nicht erreichbar. Sie können trotzdem zur Befragung zurückkehren.",
                         "The weather service cannot be reached right now. You can still go back to the survey.")
    shown = None
    if temp is not None:
        shown = f"{temp:.1f}" if lang in DECIMAL_POINT else f"{temp:.1f}".replace(".", ",")
    target = None
    if back:
        values = [("city", place or city), ("temperature", f"{temp:.1f}" if temp is not None else "")] if city else []
        target = back + "".join(f"&{n}={quote(v, safe='')}" for n, v in values)
    return render_template(
        "demo/wetter.html", lang=lang, html_lang=html_lang(lang), html_dir=html_dir(lang), city=city, place=place,
        shown=shown, problem=problem, back=back, target=target,
        t={"title": tr(lang, "Wetter-Demo", "Weather demo"),
           "intro": tr(lang, "Diese kleine Web-App zeigt, wie ein externer Abschnitt einer Befragung funktioniert: Sie nennen eine Stadt, die App fragt die aktuelle Temperatur ab und gibt Stadt und Temperatur an die Befragung zurück.",
                       "This small web app shows how an external section of a survey works: you name a city, the app looks up the current temperature and passes city and temperature back to the survey."),
           "city": tr(lang, "Stadt", "City"),
           "ask": tr(lang, "Temperatur abfragen", "Get the temperature"),
           "result": tr(lang, "In {place} sind es gerade {temp} °C.", "It is {temp} °C in {place} right now.",
                        place=place or "", temp=shown or ""),
           "back": tr(lang, "Zurück zur Befragung", "Back to the survey"),
           "alone": tr(lang, "Diese Seite wurde ohne Rückkehrlink geöffnet: Die Werte gehen an keine Befragung.",
                       "This page was opened without a return link: the values go to no survey."),
           "source": tr(lang, "Wetterdaten: Open-Meteo.com", "Weather data: Open-Meteo.com")})
