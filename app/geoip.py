"""Country and German federal state of an IP address (opt-in per survey, Survey.geo_ip).

Looked up in a table of this server (app/data/geo_ip.bin.gz, built by tools/build_geo_ip.py from the free
database "IP to City Lite" of DB-IP, CC BY 4.0): no request to anybody. The address is used for the lookup
and dropped; only the result is kept with the participation (Response.geo_country, Response.geo_state).

What the result is worth: the country is reliable, the state is an estimate. Fixed lines are mostly placed in
the right region; mobile networks leave through a few central exits; company, authority and university
networks show the seat of the institution; VPNs, Private Relay and data centres show where their exit is.
The pages that switch it on and that use it say so (SOURCES).
"""
import functools
import gzip
import ipaddress
import json
import os
import sys
from array import array
from bisect import bisect_right

DATA = os.path.join(os.path.dirname(__file__), "data", "geo_ip.bin.gz")
DBIP = '<a href="https://db-ip.com" target="_blank" rel="noopener">IP Geolocation by DB-IP</a>'  # the attribution CC BY 4.0 asks for

POESE = ("Poese, I., Uhlig, S., Kaafar, M. A., Donnet, B., & Gueye, B. (2011). IP geolocation databases: Unreliable? "
         "ACM SIGCOMM Computer Communication Review, 41(2), 53–56. https://doi.org/10.1145/1971162.1971171")
GHARAIBEH = ("Gharaibeh, M., Shah, A., Huffaker, B., Zhang, H., Ensafi, R., & Papadopoulos, C. (2017). A look at router "
             "geolocation in public and commercial databases. In Proceedings of the 2017 Internet Measurement Conference "
             "(IMC ’17) (pp. 463–469). Association for Computing Machinery. https://doi.org/10.1145/3131365.3131380")
KOMOSNY = ("Komosny, D., Voznak, M., & Rehman, S. U. (2017). Location accuracy of commercial IP address geolocation "
           "databases. Information Technology and Control, 46(3), 333–344. https://doi.org/10.5755/j01.itc.46.3.14451")
SOURCES = (POESE, GHARAIBEH, KOMOSNY)
COLUMNS = [
    ("geo_country", "Land, geschätzt aus der IP-Adresse (ISO 3166-1, zwei Buchstaben); leer = nicht ermittelt"),
    ("geo_state", "Bundesland, geschätzt aus der IP-Adresse (Schlüssel wie beim Element Bundesland: Länderschlüssel des AGS "
                  "ohne führende Null, 1 = Schleswig-Holstein … 16 = Thüringen); leer = nicht in Deutschland oder nicht ermittelt"),
]

# what a condition of the flow or of a Teilquote can be put on (app/conditions.py, flow.holds)
COUNTRY_REF, STATE_REF = "geo:country", "geo:state"
# the countries a condition offers to tick (code, German, English): Germany and its German-speaking neighbours
# first, then the rest of the EU and EFTA, the United Kingdom and a few more. Every other country is reached
# with "ist nicht"; the export always has the code
COUNTRIES = [
    ("DE", "Deutschland", "Germany"), ("AT", "Österreich", "Austria"), ("CH", "Schweiz", "Switzerland"),
    ("BE", "Belgien", "Belgium"), ("BG", "Bulgarien", "Bulgaria"), ("DK", "Dänemark", "Denmark"),
    ("EE", "Estland", "Estonia"), ("FI", "Finnland", "Finland"), ("FR", "Frankreich", "France"),
    ("GR", "Griechenland", "Greece"), ("IE", "Irland", "Ireland"), ("IS", "Island", "Iceland"),
    ("IT", "Italien", "Italy"), ("HR", "Kroatien", "Croatia"), ("LV", "Lettland", "Latvia"),
    ("LI", "Liechtenstein", "Liechtenstein"), ("LT", "Litauen", "Lithuania"), ("LU", "Luxemburg", "Luxembourg"),
    ("MT", "Malta", "Malta"), ("NL", "Niederlande", "Netherlands"), ("NO", "Norwegen", "Norway"),
    ("PL", "Polen", "Poland"), ("PT", "Portugal", "Portugal"), ("RO", "Rumänien", "Romania"),
    ("SE", "Schweden", "Sweden"), ("SK", "Slowakei", "Slovakia"), ("SI", "Slowenien", "Slovenia"),
    ("ES", "Spanien", "Spain"), ("CZ", "Tschechien", "Czechia"), ("HU", "Ungarn", "Hungary"),
    ("CY", "Zypern", "Cyprus"), ("GB", "Vereinigtes Königreich", "United Kingdom"), ("TR", "Türkei", "Turkey"),
    ("UA", "Ukraine", "Ukraine"), ("US", "USA", "United States"),
]


def country_options(lang=None):
    return [(code, de if (lang or "de") in ("de", "de-du") else en) for code, de, en in COUNTRIES]


def country_name(code, lang=None):
    return dict(country_options(lang)).get(code, code)


def flow_view(survey, response):
    """What the conditions see: {COUNTRY_REF, STATE_REF} of this participation; {} while the survey does not ask
    for it or nothing was looked up yet. A lookup without a result gives "", which a condition reads as
    "nicht ermittelt"."""
    if not getattr(survey, "geo_ip", False) or getattr(response, "geo_country", None) is None:
        return {}
    return {COUNTRY_REF: response.geo_country, STATE_REF: response.geo_state or ""}


# the column labels of COLUMNS in an English codebook (the data files keep the German ones)
COLUMNS_EN = {
    "geo_country": "Country, estimated from the IP address (ISO 3166-1, two letters); empty = not found",
    "geo_state": "Federal state, estimated from the IP address (key as with the element federal state: state key of the "
                 "AGS without a leading zero, 1 = Schleswig-Holstein … 16 = Thüringen); empty = not in Germany or not found",
}


def codebook_note(lang=None):
    """The codebook note of the two columns in the reader's language `lang` (None: the page's)."""
    from app.items.base import reader

    if reader(lang) == "en":
        return ("looked up once, with the consent to take part, in a table on the server; the IP address was not stored. "
                "The country is reliable, the federal state an estimate (mobile networks, company and university "
                "networks and VPNs often show another one). Table: IP Geolocation by DB-IP (https://db-ip.com, CC BY 4.0), "
                f"adapted, version at export {month() or 'unknown'}. " + " ".join(SOURCES))
    return ("einmal mit der Zustimmung zur Teilnahme in einer Tabelle auf dem Server nachgeschlagen; die IP-Adresse wurde "
            "nicht gespeichert. Das Land ist verlässlich, das Bundesland eine Schätzung (Mobilfunk, Firmen- und "
            "Hochschulnetze, VPN zeigen oft ein anderes). Tabelle: IP Geolocation by DB-IP (https://db-ip.com, CC BY 4.0), "
            f"bearbeitet, Stand beim Export {month() or 'unbekannt'}. " + " ".join(SOURCES))


@functools.cache
def _table():
    """({"month", "codes"}, {4: (starts, values), 6: (starts, values)}), or None while the file is missing or
    cannot be read (cut off by an upload, damaged): then nobody is looked up ("nicht ermittelt") and the log says
    why, instead of an error for everybody who agrees to take part. Read once per process: some 7 MB as arrays."""
    if not os.path.exists(DATA):
        return None
    try:
        return _read(DATA)
    except (OSError, EOFError, ValueError, KeyError, TypeError, IndexError) as e:  # gzip.BadGzipFile is an OSError
        _log_unreadable(e)
        return None


def _read(path):
    with gzip.open(path, "rb") as fh:
        if fh.readline() != b"QGEO1\n":
            raise ValueError("not a table of app/geoip.py")
        head = json.loads(fh.readline())
        codes = head["codes"]
        ranges = {}
        for family, kind, width in ((4, "I", 4), (6, "Q", 8)):
            n = int(head[f"v{family}"])
            starts, values = array(kind), array("H")
            starts.frombytes(fh.read(n * width))
            values.frombytes(fh.read(n * 2))
            if len(starts) != n or len(values) != n:
                raise ValueError("the table ends early")
            if sys.byteorder == "big":
                starts.byteswap()
                values.byteswap()
            if values and max(values) >= len(codes):
                raise ValueError("a range points at no code")
            ranges[family] = (starts, values)
    return head, ranges


def _log_unreadable(error):
    from flask import current_app, has_app_context

    message = "IP table %s cannot be read (%s): no participation gets a country or state until it is replaced"
    if has_app_context():
        current_app.logger.error(message, DATA, type(error).__name__)
    else:
        import logging

        logging.getLogger(__name__).error(message, DATA, type(error).__name__)


def month():
    """The issue of the database the table was built from ("2026-10"), "" without a table."""
    table = _table()
    return table[0]["month"] if table else ""


def lookup(address):
    """(country, state) of an address: the ISO code of the country ("DE") and, in Germany, the state key of the
    AGS without the leading zero ("5", the codes of the element Bundesland), else "". ("", "") for an address
    that says nothing: missing, not an address, local or private, or in no range of the table."""
    table = _table()
    try:
        ip = ipaddress.ip_address((address or "").strip())
    except ValueError:
        return "", ""
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if table is None or not ip.is_global:
        return "", ""
    starts, values = table[1][ip.version]
    at = bisect_right(starts, int(ip) if ip.version == 4 else int(ip) >> 64) - 1
    if at < 0:
        return "", ""
    country, _, state = table[0]["codes"][values[at]].partition("-")
    return country, state


def note(survey, response, given=None):
    """Look the participation's country and state up, once: called by runtime.step when a page was stored, so
    it happens with the request that agrees to take part and the conditions right behind the consent see the
    result. Nothing is kept of the address itself. given (test runs): (country, state) to take instead of the
    lookup. Dummy data are not looked up: they would all get the address of the researcher who creates them."""
    if not getattr(survey, "geo_ip", False) or getattr(response, "geo_country", 0) is not None:
        return  # also what only quacks like a participation (the separated section)
    if getattr(response, "source", "real") == "dummy":
        return
    consent = survey.consent_item
    if consent is None or (response.answers or {}).get(str(consent.id)) != "1":
        return  # not agreed (yet): the address is not touched
    if given is not None:
        response.geo_country, response.geo_state = given
        return
    from flask import has_request_context, request

    response.geo_country, response.geo_state = lookup(request.remote_addr) if has_request_context() else ("", "")


def simulated(value):
    """(country, state) a test run tries out, from the choice of the preview bar: "DE-5", "AT", "none" (looked up
    without a result); None for anything else (the address of the request is looked up)."""
    from app.items.federal_state import STATE_CODES

    if value == "none":
        return "", ""
    country, _, state = (value or "").partition("-")
    if country in dict(country_options()) and (not state or (country == "DE" and state in STATE_CODES)):
        return country, state
    return None


def preview_choices():
    """[(value, label)] for the preview bar: every state, the countries, and "not found"."""
    from app.items.federal_state import STATES

    return ([(f"DE-{code}", f"Deutschland · {de}") for code, de, _ in STATES]
            + [(code, de) for code, de in country_options()] + [("none", "nicht ermittelt")])


def field_counts(survey):
    """Where the real participations came from, counted in the database: {"countries", "states": [(name, started,
    completed)], most frequent first, "unknown": (started, completed)}; None while the survey does not ask for
    it or nobody was looked up yet."""
    from app.extensions import db
    from app.items.federal_state import STATES
    from app.models import Response

    if not getattr(survey, "geo_ip", False):
        return None
    rows = db.session.execute(
        db.select(Response.geo_country, Response.geo_state, Response.status, db.func.count())
        .filter(Response.survey_id == survey.id, Response.source == "real", Response.is_preview.is_(False),
                Response.geo_country.is_not(None))
        .group_by(Response.geo_country, Response.geo_state, Response.status)).all()
    if not rows:
        return None
    countries, states, unknown = {}, {}, [0, 0]
    for country, state, status, n in rows:
        done = n if status == Response.COMPLETED else 0
        for table, key in ((countries, country), (states, state if country == "DE" else None)):
            if key:
                table.setdefault(key, [0, 0])
                table[key][0] += n
                table[key][1] += done
        if not country:
            unknown[0] += n
            unknown[1] += done
    names = {code: de for code, de, _ in STATES}

    def ranked(table, name):
        return sorted(((name(key), started, done) for key, (started, done) in table.items()), key=lambda r: (-r[1], r[0]))

    return {"countries": ranked(countries, country_name), "states": ranked(states, lambda code: names.get(code, code)),
            "unknown": tuple(unknown), "map": state_map({code: started for code, (started, _) in states.items()})}


def state_map(counts):
    """The states coloured by the number of participations, as the element Bundesland shows its answers on the
    results page (surveys/_state_results.html): the entry that template takes. None without a participation
    from Germany or without the outlines."""
    from flask import current_app

    from app.items import federal_state as fs
    from app.items.map_marker import OSM_ATTRIBUTION, OSM_TILES
    from app.items.postal_code import classes
    from app.lang import de_en

    counts = {code: counts.get(code, 0) for code in fs.STATE_CODES}
    topology = fs.areas()
    if not sum(counts.values()) or not topology:
        return None
    cfg = current_app.config
    return {"passes": (), "states": {
        "topology": topology, "counts": counts, "n": sum(counts.values()), "persons": sum(counts.values()),
        "names": {code: de for code, de, _ in fs.STATES}, "classes": classes([v for v in counts.values() if v]),
        "empty": sum(1 for v in counts.values() if not v), "abroad": 0, "no_answer": 0,
        "map": {"center": [51.1657, 10.4515], "zoom": 6, "tiles": cfg.get("MAP_TILE_URL", OSM_TILES),
                "attribution": cfg.get("MAP_TILE_ATTRIBUTION", OSM_ATTRIBUTION) + " · " + de_en("Ländergrenzen", "State borders") + ": © " + fs.BKG}}}


def comparison(survey, sources=("real",), include_incomplete=False):
    """How often the state estimated from the IP address is the state the person names, for the page
    "Qualitätssicherung": one row per question that gives a state (a postal code, the element Bundesland, a shared
    location), over
    the participations of the data pool that name a German state there and were looked up:
    [{"code", "what", "n", "same", "other", "abroad", "unknown"}]. None while the survey does not ask for the
    lookup; [] without such a question. It describes the sample, it marks nobody: a differing state is normal
    (mobile networks, company and university networks, VPN)."""
    if not getattr(survey, "geo_ip", False):
        return None
    from app import export, flow
    from app.items import postal_code
    from app.items.choice import selected
    from app.items.federal_state import STATE_CODES

    in_loop = flow.loop_blocks(survey)
    questions = [i for b in survey.blocks if b.id not in in_loop for i in b.items
                 if i.type in ("postal_code", "federal_state", "geolocation")]
    if not questions:
        return []
    responses = [r for r in export._responses(survey, include_incomplete, sources) if r.geo_country is not None]
    rows = []
    for item in questions:
        row = {"code": item.code, "what": {"postal_code": "Postleitzahl", "geolocation": "Standort"}.get(item.type, "Bundesland"),
               "n": 0, "same": 0, "other": 0, "abroad": 0, "unknown": 0}
        for r in responses:
            answer = (r.answers or {}).get(str(item.id))
            if item.type == "geolocation":  # the state of the shared position, not an estimate of the IP address itself
                named = answer.get("state") if isinstance(answer, dict) and answer.get("src") == "pos" else None
            else:
                named = postal_code.state(answer) if item.type == "postal_code" else selected(answer)
            if str(named) not in STATE_CODES:
                continue  # no answer, a code that does not exist, "nicht in Deutschland", "keine Angabe"
            row["n"] += 1
            key = ("unknown" if not r.geo_country else "abroad" if r.geo_country != "DE"
                   else "same" if r.geo_state == str(named) else "other")
            row[key] += 1
        rows.append(row)
    return rows


def dummy(rng):
    """(country, state) of a simulated participation: mostly Germany with the states by their share of the
    population, a few from abroad and a few without a result, so conditions and the comparison have something
    to show. Dummy data are never looked up (they would all have the address of whoever creates them)."""
    from app import census

    draw = rng.random()
    if draw < 0.03:
        return "", ""
    if draw < 0.08:
        return rng.choice(["AT", "CH", "NL", "FR"]), ""
    people = {code: sum(sum(rows) for rows in sexes.values()) for code, sexes in census.population()["states"].items()}
    codes = sorted(people)
    return "DE", rng.choices(codes, weights=[people[c] for c in codes])[0]
