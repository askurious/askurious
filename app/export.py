"""CSV export of responses and a matching codebook.

One row per response (previews excluded), columns in questionnaire order. Reverse-coded
items are already recoded. Randomized orders are exported so order effects can be analysed.
Separator "comma" writes decimal points (R, Python, SPSS); "semicolon" writes decimal commas
(German Excel).
"""
import csv
import io
import re
from datetime import datetime, timezone
from types import SimpleNamespace

from app import brand
from app import constructs, theme, units
from app import item_condition
from app import rows as row_logic
from app.extensions import db
from app.items.base import DONT_KNOW_CODE, LEVEL_LABELS
from app.models import Response
from app.request_values import drop_surrogates
from app.timeutil import to_local

SOURCES = ("real", "test", "dummy")
META = [
    ("resp_id", "Laufende Nummer der Teilnahme"),
    ("pid", "Teilnahme-ID ({{pid}}: Survey-Code und Zufallscode), z. B. zur Verknüpfung mit einer externen Anwendung"),
    ("source", "real = echte Teilnahme | test = Testdurchlauf (Vorschau) | dummy = simulierte Daten"),
    ("qversion", "Version des Fragebogens (steigt, wenn eine pausierte/geschlossene Befragung erneut bearbeitet wird)"),
    ("status", "completed | screenout | quotafull (Quote voll) | excluded (Ausschluss im Ablauf) | active (nicht beendet); "
               "Ablehnungen werden nicht gespeichert"),
    ("lang", "Sprache"),
    ("assignment_network_starts", "Frühere neue Zuweisungen aus demselben Netzwerk in dieser UTC-Stunde; "
                                  "kein Nachweis derselben Person oder eines Betrugs (z. B. gemeinsam genutzter Anschluss)"),
    ("started", "Beginn (Ortszeit)"),
    ("finished", "Ende (Ortszeit): Abschluss, Screen-out oder Quote voll; leer, solange nicht beendet"),
    ("last_active", "Zuletzt aktiv (Ortszeit): letzte abgeschickte Seite einer nicht beendeten Teilnahme (Zeitpunkt des "
                    "Abbruchs); leer bei beendeten"),
    ("duration_s", "Bearbeitungsdauer in Sekunden"),
    ("duration_rel", "Bearbeitungsdauer relativ zum Median der abgeschlossenen echten Teilnahmen (1 = Median)"),
    ("speeder", "1 = Speeder nach der gewählten Regel: Bearbeitungsdauer unter dem gewählten Anteil des Medians oder "
                "Relative Speed Index über der gewählten Grenze (Leiner, 2019)"),
    ("last_page", "Seite, auf der die Teilnahme zuletzt stand (Position im Ablauf dieser Teilnahme, nicht die weiteste: "
                  "nach „Zurück“ die frühere Seite; Aufgaben mit eigenen Seiten sowie Quoten- und Endpunkte im Ablauf "
                  "zählen einzeln)"),
]


DEFAULT_SPEEDER_PCT = 50  # Leiner (2019): less than half the median completion time
LEINER = ("Leiner, D. J. (2019). Too fast, too straight, too weird: Non-reactive indicators for meaningless "
          "data in internet surveys. Survey Research Methods, 13(3), 229–248. https://doi.org/10.18148/srm/2019.v13i3.7403")


def speeder_pct(value):
    """The speeder threshold in percent of the median (5..100), from a request value."""
    try:
        return min(max(int(value), 5), 100)
    except (TypeError, ValueError):
        return DEFAULT_SPEEDER_PCT


RSI_COLUMN = ("qc_rsi", "Relative Speed Index (Leiner, 2019): Median der Seitenzeit ÷ eigene Seitenzeit, je Seite "
                        "höchstens 3, gemittelt über die gesehenen Seiten (1 = typisches Tempo, 2 = doppelt so schnell)")


# how often a stored participation started again from the first page because the questionnaire was changed while it
# was under way (runtime.ensure_layout, paradata "_relayout"); only for surveys opened for editing once (a layout can
# change only in a draft, and real participations exist only after the start)
# a survey in several languages: did the participation change its language on the way (paradata "_langs",
# paradata.note_language; audit 2026-10-12, T-5). "lang" is the last language
LANG_SWITCH_COLUMN = ("lang_switched", "1 = Sprache während der Teilnahme gewechselt (lang ist die zuletzt gewählte), "
                                       "0 = nicht gewechselt; festgehalten seit Oktober 2026, ältere Teilnahmen 0")


RELAYOUT_COLUMN = ("relayout", "Neubeginn nach Änderung des Fragebogens: wie oft diese Teilnahme wieder auf der ersten "
                               "Seite begann, weil der Fragebogen während der Teilnahme geändert wurde (0 = nie)")


def _behind_branch(survey, node):
    """The randomizer lies behind a branch condition: its column is empty for those who never reached it
    (flow.reached_conditions)."""
    from app import flow

    return any(a["t"] in ("branch", "else") for a in flow.ancestors(flow.tree(survey), node["id"]))


# a questionnaire with "Neuer Durchgang" (lifecycle.runs; Philipp, 2026-10-13): the run in which a participation began
RUN_COLUMN = ("run", "Durchgang, in dem die Teilnahme begann (1 = ab Start; ein neuer beginnt mit „Neuer Durchgang“ "
                           "auf der Seite Datenerhebung; Menschenmenge, Streubild und Vergleiche zählten nur den jeweiligen)")


def has_runs(survey):
    """Did a questionnaire begin a second run? Then the export names the run of every participation (RUN_COLUMN)."""
    if getattr(survey, "live", None) is not None:
        return False
    return any(isinstance(e, dict) and e.get("event") == "run" for e in (getattr(survey, "status_log", None) or []))


MODE_COLUMN = ("mode", "Erhebungsweg: online | paper (Papierfragebogen, eingescannt; ohne Bearbeitungsdauer)")


def has_paper(survey):
    """Does the survey hold participations from paper questionnaires (app/paper.py)? Once per request."""
    from app.models import counted_once

    if getattr(survey, "id", None) is None:
        return False
    return counted_once(("paper", survey.id), lambda: db.session.scalar(
        db.select(Response.id).filter_by(survey_id=survey.id, channel="paper").limit(1)) is not None)


def meta(survey):
    """The first columns of the data export: META, with page times also the speed index after "speeder", and after
    "qversion" the restarts of a survey edited again (RELAYOUT_COLUMN) and the run (RUN_COLUMN); after "source" the
    mode of a survey with paper questionnaires (MODE_COLUMN)."""
    cols = list(META)
    if has_paper(survey):
        at = [c for c, _ in cols].index("source") + 1
        cols = [*cols[:at], MODE_COLUMN, *cols[at:]]
    if len(getattr(survey, "languages", None) or []) > 1:
        at = [c for c, _ in cols].index("lang") + 1
        cols = [*cols[:at], LANG_SWITCH_COLUMN, *cols[at:]]
    if (getattr(survey, "qversion", None) or 1) > 1:
        at = [c for c, _ in cols].index("qversion") + 1
        cols = [*cols[:at], RELAYOUT_COLUMN, *cols[at:]]
    if has_runs(survey):
        at = [c for c, _ in cols].index("qversion") + 1
        cols = [*cols[:at], RUN_COLUMN, *cols[at:]]
    if not getattr(survey, "page_timing", False) or getattr(survey, "live", None) is not None:
        return cols
    at = [c for c, _ in cols].index("speeder") + 1
    return [*cols[:at], RSI_COLUMN, *cols[at:]]


LIVE_COLUMNS = [("durchgang", "Durchgang der Live-Abstimmung (Name, vergeben beim Start des Durchgangs)"),
                ("durchgang_id", "Nummer des Durchgangs der Live-Abstimmung (eindeutig, auch bei gleichem Namen)"),
                ("durchgang_ursprung", "Durchgang, in dem die Teilnahme abgegeben wurde (vor dem Zusammenführen von Durchgängen)"),
                ("durchgang_ursprung_id", "Nummer des Durchgangs, in dem die Teilnahme abgegeben wurde"),
                ("weiteres_wort", "Weiteres Wort eines Geräts („Mehrere Antworten nacheinander erlauben“): keine weitere Person"),
                ("hauptteilnahme", "Teilnahme-ID (pid) der Teilnahme desselben Geräts, zu der das weitere Wort gehört")]
# a further word (audit 2026-10-05, T-19): with "Mehrere Antworten pro Gerät" every further short text of a device is a
# row of its own, so each cell keeps one value; counting persons means leaving these rows out
LIVE_NOTES = {"weiteres_wort": "1 = weiteres Wort eines Geräts in einem Durchgang, keine weitere Person: Für die Zahl "
                               "der Teilnehmenden, Fehlende-Anteile und Zusammenhänge zwischen Fragen nur Zeilen mit 0 "
                               "verwenden; für die Wortwolke zählen alle Zeilen",
              "durchgang_ursprung": "gleich durchgang, außer der Durchgang wurde mit einem anderen zusammengeführt",
              "hauptteilnahme": "nur bei weiteres_wort = 1; leer bei Wörtern aus der Zeit vor dieser Spalte"}


# a diary study (app/diary.py): each row is one entry; "person" groups the entries of one person (long format)
DIARY_COLUMNS = [("person", "Personen-ID der Tagebuchstudie: Alle Einträge einer Person haben dieselbe"),
                 ("teil", "Teil: entry (Eingangsbefragung), exit (Abschlussbefragung) oder der Schlüssel der Kurzbefragung"),
                 ("eintrag", "Nummer des Eintrags der Person in diesem Teil (1, 2, …)"),
                 ("tag", "Tag des Zeitraums der Person (1 = erster Tag; leer in der Eingangsbefragung)"),
                 ("ausloeser", "Auslöser: fixed (Abfrage zu fester Zeit), random (Abfrage zu zufälliger Zeit) oder self "
                               "(von der Person begonnen)"),
                 ("faellig", "Zeitpunkt der Abfrage (UTC); leer, wenn die Person den Eintrag begonnen hat"),
                 ("benachrichtigt", "Zeitpunkt, zu dem das Handy die Abfrage zeigte (UTC)"),
                 ("verzoegerung_s", "Sekunden von der Abfrage bis zum Beginn des Eintrags"),
                 ("ortszeit", "Beginn des Eintrags in der Ortszeit der Person (HH:MM)"),
                 ("wochentag", "Wochentag des Beginns in der Ortszeit (1 = Montag … 7 = Sonntag)"),
                 ("abfrage", "Stand: beantwortet, zur_pruefung, abgebrochen oder verpasst (Abfrage ohne Eintrag; nur in der CSV-Datei)"),
                 ("erinnert", "1 = nach der Erinnerung geöffnet"),
                 ("nachgereicht", "1 = ohne Verbindung beantwortet und später gesendet: Beginn und Ende sind Angaben des Telefons"),
                 ("eingang", "Zeitpunkt, zu dem der Server den Eintrag erhielt (UTC)")]
DIARY_NOTES = {"person": "für Mehrebenenmodelle: Einträge (Ebene 1) in Personen (Ebene 2)",
               "verzoegerung_s": "nur bei Abfragen zu fester oder zufälliger Zeit",
               "abfrage": "verpasst: fällige Abfrage einer echten Person, abgelaufen ohne Eintrag; ohne Antworten. "
                          "zur_pruefung: offline gesendete Antwort, die als Prüffall auf „Offline-Antworten prüfen“ wartet; "
                          "ohne Antworten, weder beantwortet noch verpasst. "
                          "abgebrochen: ein begonnener, nicht beendeter Eintrag; enthält der Export nur beendete, steht er "
                          "als Zeile ohne Antworten da. Ein ausgelassener markierter Eintrag (qc_marked) erscheint nicht",
               "nachgereicht": "angenommen nur, wenn der angegebene Beginn vor dem Ablauf der Abfrage lag, nicht vor dem "
                               "letzten Abruf der Offline-Seite und nicht nach dem Eingang"}
DIARY_NOTES_EN = {"person": "for multilevel models: entries (level 1) within people (level 2)",
                  "verzoegerung_s": "only for prompts at fixed or random times",
                  "abfrage": "verpasst (missed): a due prompt of a real person that expired without an entry; no answers. "
                             "zur_pruefung (under review): an answer sent offline that waits as a review case on "
                             "“Review offline answers”; no answers, neither answered nor missed. "
                             "abgebrochen (broken off): an entry begun and not finished; in an export of finished entries "
                             "only, a row without answers. A marked entry left out (qc_marked) does not appear",
                  "nachgereicht": "taken only if the stated start lay before the prompt expired, not before the offline page "
                                  "was last fetched and not after the arrival"}


def _diary_values(r, persons, triggers=None):
    """The columns of a diary entry (DIARY_COLUMNS) from Response.occasion. triggers: {module key: its trigger}; an
    entry of a random module was stored as "fixed" before the audit 2026-10-12 (D-13), the module says which it was
    (its trigger cannot change once the study has answers, diary.locked)."""
    from app import diary

    occ = r.occasion or {}
    trigger = occ.get("trigger", "")
    if trigger == "fixed" and (triggers or {}).get(occ.get("part")) in diary.SCHEDULED:
        trigger = triggers[occ.get("part")]
    due = datetime.fromisoformat(occ["due"]) if occ.get("due") else None
    local = r.started_at.replace(tzinfo=timezone.utc).astimezone(diary.zone(occ.get("tz"))) if r.started_at else None
    return {"person": persons.get(r.enrolment_id, ""), "teil": occ.get("part", ""), "eintrag": occ.get("n", ""),
            "tag": occ.get("day") or "", "ausloeser": trigger if occ.get("part") != "entry" else "",
            "faellig": due.isoformat(timespec="minutes") if due else "", "benachrichtigt": (occ.get("notified") or "")[:16],
            "verzoegerung_s": int((r.started_at - due).total_seconds()) if due and r.started_at else "",
            "ortszeit": local.strftime("%H:%M") if local else "", "wochentag": local.isoweekday() if local else "",
            "abfrage": "beantwortet" if r.status == Response.COMPLETED else
                       "zur_pruefung" if r.status == "review" else "abgebrochen",
            "erinnert": int(bool(occ.get("reminded"))), "nachgereicht": int(bool(occ.get("offline"))),
            "eingang": r.received_at.isoformat(timespec="minutes") if getattr(r, "received_at", None) else ""}


def threshold(survey, speeder=None):
    """The speeder threshold in percent of the median: the survey's own (page "Qualitätskontrolle") unless a
    caller names one. A live vote has none (0: nobody is a speeder): its duration runs from joining to the last
    answer, so who joins late or stops early would fall out of the results, and there is no page to see or change it."""
    if getattr(survey, "live", None) is not None:
        return 0
    return speeder_pct(survey.speeder_pct) if speeder is None else speeder


def is_speeder(response, median, pct):
    """True for a completed participation faster than `pct` percent of the median (whole seconds, as rows). A
    negative duration is a data error (clock), never a speed: no speeder, as in the median."""
    if response.status != Response.COMPLETED or not response.completed_at or not response.started_at or not median:
        return False
    if getattr(response, "channel", None) == "paper":  # no completion time on paper
        return False
    seconds = int((response.completed_at - response.started_at).total_seconds())
    return 0 <= seconds and seconds / median < pct / 100


def marked_ids(survey, sources=("real",)):
    """Ids of the completed participations of these sources that quality control marks, as qc_marked of rows()
    for the same pool: speeders against the median(s) of the pool, and the survey's marking rule."""
    from app import quality

    ctx = quality.context(survey, _items(survey))
    flags = quality.can_mark(ctx)
    if not flags and speeder_rule(survey) == "rsi":  # the page times are enough, read once
        found = db.session.execute(db.select(Response.id, Response.paradata).filter(
            Response.survey_id == survey.id, Response.source.in_(sources), Response.status == Response.COMPLETED)).all()
        medians = page_medians(survey, (rsi_times(p) for _, p in found))
        limit = rsi_limit(survey)
        return {rid for rid, p in found if (value := rounded_rsi(rsi_times(p), medians)) is not None and value > limit}
    median = median_seconds(survey, True, sources)
    pct = threshold(survey)
    if not flags:  # speeders only: the two times are enough, the answers are not read
        rows = db.session.execute(db.select(Response.id, Response.started_at, Response.completed_at,
                                            Response.enrolment_id, Response.occasion).filter(
            Response.survey_id == survey.id, Response.source.in_(sources), Response.status == Response.COMPLETED,
            Response.channel.is_(None)))  # paper sheets have no completion time
        medians = {}  # a diary study: the median of each part (diary_part)

        def median_of(enrolment_id, occ):
            if not enrolment_id:
                return median
            part = (occ or {}).get("part", "entry")
            if part not in medians:
                medians[part] = median_seconds(survey, True, sources, part)
            return medians[part]
        return {rid for rid, started, done, eid, occ in rows if started and done and (m := median_of(eid, occ))
                and int((done - started).total_seconds()) >= 0  # a negative duration is a clock error
                and int((done - started).total_seconds()) / m < pct / 100}
    responses = _responses(survey, False, sources)
    speed = Speed(survey, sources, responses=responses)
    if ctx["open"]:
        quality.index_texts(ctx, responses)
    least = ctx["rules"]["min"]
    return {r.id for r in responses
            if quality.marked({"speeder": int(speed.speeder(r)),
                               "qc_flags": quality.request_values(survey, r, ctx)["qc_flags"]}, least)}


def marked_at_end(survey, response):
    """Quality control marks this one participation as it completes (quotas.settle, setting quota_drop_marked):
    a speeder against the median of the real completed participations so far (its own included, as rows() reads
    the real pool; no median, no speeder), or enough conspicuous things by the survey's marking rule. The
    identical open answers (qc_text_same) need the whole pool and do not count here. Reads this participation
    and the two times of the others, never their answers. With the speed index: against the page medians of the
    real completed participations, kept for a few seconds (app/balance.py) as they read every page time."""
    from app import quality

    if speeder_rule(survey) == "rsi":
        from app import balance

        medians = balance.cached(("pages", survey.id), lambda: page_medians(survey, completed_times(survey)))
        value = rounded_rsi(_times(response), medians)
        if value is not None and value > rsi_limit(survey):
            return True
    elif is_speeder(response, median_seconds(survey, part=diary_part(response)), threshold(survey)):
        return True
    ctx = quality.context(survey, _items(survey))
    if not quality.can_mark(ctx):
        return False
    return quality.marked({"speeder": 0, "qc_flags": quality.values(survey, response, ctx)["qc_flags"]}, ctx["rules"]["min"])


def diary_part(response):
    """The part of a diary entry ("entry" or a module key, app/diary.py) whose median it is measured against; None
    for a participation of any other survey. A short module and the long entry part are never one median."""
    occasion = getattr(response, "occasion", None)
    return (occasion or {}).get("part", "entry") if getattr(response, "enrolment_id", None) else None


def completion_seconds(survey, real_only=True, sources=None, part=None):
    """Completion times (s) of completed participations, the basis for the median: real ones only by
    default; with real_only=False also test runs and dummy data (distorts the result); `sources`
    (e.g. the data pool chosen on the results page) overrides both. `part`: of a diary study, the entries of this
    part only (diary_part); None: all."""
    from app.models import counted_once

    sources = tuple(sources) if sources else (("real",) if real_only else SOURCES)
    diary = part is not None and getattr(survey, "sampling", None) is not None

    def count():
        rows = db.session.execute(db.select(Response.started_at, Response.completed_at, Response.occasion).filter(
            Response.survey_id == survey.id, Response.source.in_(sources), Response.status == Response.COMPLETED,
            Response.completed_at.is_not(None), Response.started_at.is_not(None),
            Response.channel.is_(None)))  # the two times only, not the answers; paper sheets have no completion time
        times = (int((done - started).total_seconds()) for started, done, occ in rows
                 if not diary or (occ or {}).get("part", "entry") == part)
        return sorted(t for t in times if t >= 0)  # a negative duration is a data error (clock), never a speed
    return list(counted_once(("times", survey.id, tuple(sorted(sources)), part if diary else None), count))  # once per request


def speed_data(survey, pool=None, responses=None):
    """Completion times for the speeder histogram, from the chosen pool; `by_source` lets the page
    redraw it at once when the pool changes. With page times also the speed index of every completed
    participation of the pool ("rsi": values, number of pages counted) and the rule in force. responses: the
    participations of the pool (sources `pool`), when the caller has read them already."""
    by_source = {src: completion_seconds(survey, sources=[src]) for src in SOURCES}
    rule = {"rule": speeder_rule(survey), "limit": rsi_limit(survey), "rsi": None, "paths_differ": paths_differ(survey)}
    if getattr(survey, "sampling", None) is not None:  # a diary study: speeders count against the median of their part
        from app import diary

        parts = [("entry", diary.part_label("entry", survey))] + [(m["key"], diary.part_label(m["key"], survey))
                                                                  for m in diary.modules(survey)]
        rule["parts"] = [(label, median_seconds(survey, sources=pool or ("real",), part=key)) for key, label in parts]
    if getattr(survey, "page_timing", False) and getattr(survey, "live", None) is None:
        found = ([rsi_times(r.paradata) for r in responses if r.status == Response.COMPLETED] if responses is not None
                 else completed_times(survey, pool or ("real",)))
        medians = page_medians(survey, found)
        values = [v for t in found if (v := rounded_rsi(t, medians)) is not None]
        rule["rsi"] = {"values": sorted(values), "pages": sum("|" not in k for k in medians), "min_n": RSI_MIN_N,
                       "without": len(found) - len(values)}
    if pool:
        times, median = completion_seconds(survey, sources=pool), median_seconds(survey, sources=pool)
        return {"times": times, "median": median, "times_all": times, "median_all": median, "by_source": by_source,
                "any": any(by_source.values()), "default": DEFAULT_SPEEDER_PCT, "reference": LEINER, **rule}
    return {"times": completion_seconds(survey), "median": median_seconds(survey),
            "times_all": completion_seconds(survey, False), "median_all": median_seconds(survey, False),
            "default": DEFAULT_SPEEDER_PCT, "reference": LEINER, **rule}


def paths_differ(survey):
    """Not everybody goes the same way through the questionnaire: a branch of the flow, a randomizer that draws
    some of its elements, a loop over some of its rows, or questions, statements and options only some people
    see (app/rows.py). Then the completion time compares people who saw different amounts with one median."""
    from app import flow, rows

    for node, _, _ in flow.walk(flow.tree(survey)):
        if node["t"] in ("branch", "else"):
            return True
        if node["t"] == "rand" and (node.get("k") or 1) < len(node.get("children") or []):
            return True
        if node["t"] == "loop" and (node.get("mode") or "all") != "all":
            return True
    from app import item_condition

    return item_condition.any_in(survey) or any(
        isinstance(found := (item.config or {}).get(field), list) and rows.conditional(found)
        for block in survey.blocks for item in block.items for field in ("statements", "pairs", "options"))


def median_seconds(survey, real_only=True, sources=None, part=None):
    import statistics

    times = completion_seconds(survey, real_only, sources, part)
    return statistics.median(times) if times else None


# Relative speed index (Leiner, 2019, p. 236): per page the median time of the sample divided by the person's own
# time, each factor clipped at 3 ("avoids disqualifying respondents who incidentally skip a single page"), then the
# mean over the pages the person saw. Leiner's pragmatic cut-off: above 2.0 (p. 242).
RSI_CAP = 3.0
RSI_DEFAULT = 20   # tenths: 2.0
RSI_LIMITS = (10, 30)  # tenths: 1.0 .. 3.0 (no factor exceeds 3)
RSI_MIN_N = 10     # a page counts only once this many completed participations sent it (Festlegung von Askurious)


def speeder_rule(survey):
    """"rsi" (relative speed index over the page times) or "duration" (completion time against its median). The
    index needs page times (page_timing) and is then the default; a live vote has the duration rule, whose
    threshold is 0 there (threshold())."""
    if getattr(survey, "live", None) is not None or not getattr(survey, "page_timing", False):
        return "duration"
    return "duration" if getattr(survey, "speeder_rule", "") == "duration" else "rsi"


def rsi_tenths(value):
    """The limit of the speed index in tenths (10..30), from a request value such as "2.0" or "2,5"."""
    try:
        tenths = round(float(str(value).strip().replace(",", ".")) * 10)
        return min(max(tenths, RSI_LIMITS[0]), RSI_LIMITS[1])
    except (TypeError, ValueError, OverflowError):  # also "nan" and "inf"
        return RSI_DEFAULT


def rule_text(survey, lang="de"):
    """The speeder rule of the survey in words, as the pages name it: German for the codebook and the exports,
    lang "en" for a page in English (page_rule_text)."""
    if speeder_rule(survey) == "rsi":
        if lang == "en":
            return f"Relative Speed Index above {units.fixed(rsi_limit(survey), 1)}"
        return f"Relative Speed Index über {units.fixed(rsi_limit(survey), 1).replace('.', ',')}"
    if lang == "en":
        return f"faster than {threshold(survey)}% of the median time"  # English without the space (X82)
    return f"schneller als {threshold(survey)} % der Median-Zeit"


def page_rule_text(survey):
    """rule_text in the language of the page being built."""
    from app.lang import current

    return rule_text(survey, current())


def rsi_limit(survey):
    """The survey's limit of the speed index (e.g. 2.0): above it a participation is a speeder."""
    saved = getattr(survey, "speeder_rsi", None)
    return (saved if type(saved) is int and RSI_LIMITS[0] <= saved <= RSI_LIMITS[1] else RSI_DEFAULT) / 10


def rsi_pages(survey):
    """{page key} of the pages the speed index counts: pages with at least one question (not the consent, not an
    explanation page of a task), without a minimum time or time limit (app/pagetime.py); every loop pass is a
    page of its own."""
    from app import pagetime, paradata

    out = set()
    for _, _, key, block, items in paradata.timing_pages(survey):
        if key.endswith(".x") or pagetime.held(block, items):
            continue
        if any(i.kind.collects_answer and not i.kind.computed and i.type != "consent" for i in items):
            out.add(key)
    return out


def page_medians(survey, times_of):
    """{page key: median seconds} of the pages the index counts that at least RSI_MIN_N of these participations
    sent (`times_of`: their stored page times, rsi_times); a median of 0 says nothing and is left out. A page
    whose questions, statements or options only some people see also gets a median per version seen
    ("<key>|<version>", paradata "_v"; "<key>|" for the whole page), each once RSI_MIN_N people saw it."""
    import statistics

    keys = rsi_pages(survey)
    seen, versions, varied = {}, {}, set()
    for times in times_of:
        times = times or {}
        shown = times.get(VERSIONS) if isinstance(times.get(VERSIONS), dict) else {}
        varied.update(k for k in shown if k in keys)
        for key, seconds in times.items():
            if key in keys and isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and seconds >= 0:
                seen.setdefault(key, []).append(seconds)
                versions.setdefault(f"{key}|{shown.get(key) or ''}", []).append(seconds)
    seen.update((v, values) for v, values in versions.items() if v.split("|", 1)[0] in varied)
    return {key: m for key, values in seen.items() if len(values) >= RSI_MIN_N and (m := statistics.median(values)) > 0}


def rsi(times, medians):
    """The relative speed index of one participation (its page times, rsi_times), or None if it sent none of the
    pages with a median. A page is measured against the median of the version this person saw if that one has
    a median, else against the median of the page (page_medians)."""
    factors = []
    times = times or {}
    shown = times.get(VERSIONS) if isinstance(times.get(VERSIONS), dict) else {}
    for key, median in medians.items():
        if "|" in key:
            continue
        seconds = times.get(key)
        if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and seconds >= 0:
            median = medians.get(f"{key}|{shown.get(key) or ''}", median)
            factors.append(min(median / seconds, RSI_CAP) if seconds > 0 else RSI_CAP)
    return sum(factors) / len(factors) if factors else None


def rsi_note(basis="echte Teilnahmen", lang="de"):
    """How the index is computed, for the codebook (lang "en": in English)."""
    if lang == "en":
        return (f"page median from the completed participations ({basis}); counted are pages with at least one question, "
                f"without a minimum or maximum time, that at least {RSI_MIN_N} people sent (a choice of Askurious); loop "
                "passes per pass; who did not see all questions, statements or options on a page is measured against the "
                f"median of the same version once {RSI_MIN_N} saw it; factor per page = median ÷ own time, at most 3 "
                "(Leiner, 2019); mean over the pages seen; empty = no page counted or not completed; "
                f"{LEINER}")
    return (f"Seitenmedian aus den abgeschlossenen Teilnahmen ({basis}); gezählt werden Seiten mit mindestens einer "
            f"Frage, ohne Mindestzeit oder Höchstzeit, die mindestens {RSI_MIN_N} Personen abschickten (Festlegung von "
            "Askurious); Schleifendurchgänge je Durchgang; wer auf einer Seite nicht alle Fragen, Aussagen oder Optionen "
            f"sah, gegen den Median derselben Fassung, sobald {RSI_MIN_N} sie sahen; Faktor je Seite = Median ÷ eigene Zeit, höchstens "
            "3 (Leiner, 2019); Mittel über die gesehenen Seiten; leer = keine gezählte Seite "
            f"oder nicht abgeschlossen; {LEINER}")


def rounded_rsi(times, medians):
    value = rsi(times, medians)
    return None if value is None else units.round_half_up(value, 2)


VERSIONS = "_v"  # in rsi_times: {page key: version seen} (paradata.shown_version)


def rsi_times(data):
    """The page times of a participation's paradata ("_t") as the speed index reads them: with the versions of
    the pages seen under VERSIONS, where some of a page's content was not shown (paradata "_v")."""
    data = data or {}
    times = data.get("_t") or {}
    return {**times, VERSIONS: data["_v"]} if data.get("_v") and times else times


def _times(response):
    return rsi_times(getattr(response, "paradata", None))


class Speed:
    """The speeder rule of a survey with what it rests on, found once for a data pool: the completion time
    against its median (threshold in percent), or the relative speed index against its limit with the page
    medians. `sources` are those of the median; `responses`: participations already read that are exactly these
    sources (their completed ones give the page medians, nothing is read twice). `medians`: page medians found
    elsewhere (the kept ones of marked_at_end)."""

    def __init__(self, survey, sources=("real",), pct=None, responses=None, medians=None):
        self.rule = speeder_rule(survey)
        self.pct = threshold(survey, pct)
        self.limit = rsi_limit(survey)
        self.timing = bool(getattr(survey, "page_timing", False)) and getattr(survey, "live", None) is None
        self.median = median_seconds(survey, sources=sources)
        self.survey, self.sources, self.parts = survey, sources, {}  # a diary study: the median of each part
        self.medians = {}
        if self.timing:
            if medians is not None:
                self.medians = medians
            elif responses is not None:
                self.medians = page_medians(survey, (_times(r) for r in responses if r.status == Response.COMPLETED))
            else:
                self.medians = page_medians(survey, completed_times(survey, sources))

    def rsi(self, response):
        """The index of a completed participation (two decimals, as exported: the flag can be recomputed from
        the export and matches the histogram), else None."""
        if not self.timing or response.status != Response.COMPLETED:
            return None
        return rounded_rsi(_times(response), self.medians)

    def median_of(self, response):
        """The median a participation is measured against: of a diary study the one of its part (diary_part: the
        entry part and a short module are never one median), else the median of the pool."""
        part = diary_part(response) if self.survey.sampling is not None else None
        if part is None:
            return self.median
        if part not in self.parts:
            self.parts[part] = median_seconds(self.survey, sources=self.sources, part=part)
        return self.parts[part]

    def speeder(self, response):
        if self.rule == "rsi":
            value = self.rsi(response)
            return value is not None and value > self.limit
        return is_speeder(response, self.median_of(response), self.pct)


def completed_times(survey, sources=("real",)):
    """The page times (rsi_times) of the completed participations of these sources: their paradata only."""
    return [rsi_times(p) for p in db.session.scalars(db.select(Response.paradata).filter(
        Response.survey_id == survey.id, Response.source.in_(tuple(sources)), Response.status == Response.COMPLETED))]


def preload(survey, assets=False):
    """The elements of all blocks of a survey (with assets: and their images) in one query each, instead of one query
    per block and per element when an export walks through them (performance audit 2026-10-09b, P12). Fills only
    lists not loaded yet, in the order of the relationships, so nothing already read or changed is replaced."""
    from collections import defaultdict

    from sqlalchemy import inspect
    from sqlalchemy.orm.attributes import set_committed_value

    from app.models import Asset, Item

    blocks = [b for b in survey.blocks if b.id is not None]
    missing = [b for b in blocks if "items" in inspect(b).unloaded]
    if missing:
        found = defaultdict(list)
        for item in db.session.scalars(db.select(Item).where(Item.block_id.in_([b.id for b in missing]))
                                       .order_by(Item.block_id, Item.position, Item.id)):
            found[item.block_id].append(item)
        for b in missing:
            set_committed_value(b, "items", found[b.id])
    if not assets:
        return
    items = [i for b in blocks for i in b.items if i.id is not None and "assets" in inspect(i).unloaded]
    if items:
        found = defaultdict(list)
        for asset in db.session.scalars(db.select(Asset).where(Asset.item_id.in_([i.id for i in items]))
                                        .order_by(Asset.id)):
            found[asset.item_id].append(asset)
        for i in items:
            set_committed_value(i, "assets", found[i.id])


def _items(survey):
    """Items of the main part; items after the split belong to the separated, unlinked export."""
    from app import flow

    separated = set(flow.blocks_after_split(survey))
    return [i for b in survey.blocks if b.id not in separated for i in b.items if i.type != "page_break"]


def _randomized_pages(survey):
    """[(page number (1-based), block id, item ids, loop passes)] of the pages whose item order is randomized;
    loop passes: [(row number, row id)] if the block runs in a loop (the order is drawn per pass), else None."""
    from app import loops

    out, n = [], 0
    for block in survey.blocks:
        passes = ([(k, r["id"]) for k, r in enumerate(loops.config(block)["rows"], start=1)]
                  if loops.active(block) else None)
        for items in block.pages():
            n += 1
            if block.randomize_items:
                out.append((n, block.id, {i.id for i in items}, passes))
    return out


def _page_order(layout_pages, block_id, ids, row=None):
    """Item ids of one static page in the order shown (of the loop pass `row`, if given). The layout follows the
    flow and also holds end/split markers, loop passes and one page per conjoint task, so pages are matched by
    block and items, not by position; a page split around a conjoint or assignment task is joined again."""
    out = []
    for spec in layout_pages:
        if spec.get("block") != block_id or spec.get("row") != row:
            continue
        out += [i for i in spec.get("items", []) if i in ids and i not in out]
    return out


def _randomizers(survey):
    from app import flow

    return [n for n, _, _ in flow.walk(flow.tree(survey)) if n["t"] == "rand"]


def _loop_rows(item):
    """[(row number, row id)] if the item sits in a loop block, else None."""
    from app import loops

    if not loops.active(item.block):
        return None
    return [(n, r["id"]) for n, r in enumerate(loops.config(item.block)["rows"], start=1)]


def _loops(survey):
    """Settings of every loop node with a table (one `<code>_order` column each)."""
    from app import loops

    return [c for c in (loops.config_node(n) for n in loops.nodes(survey)) if c["columns"] and c["rows"]]


def hb_module(item):
    """The module with the hierarchical Bayes model of an item type (MaxDiff: app/maxdiff_hb.py, CBC and
    ACBC: app/conjoint_hb.py), None for all other types."""
    if item.type == "maxdiff":
        from app import maxdiff_hb

        return maxdiff_hb
    if item.type in ("conjoint", "acbc"):
        from app import conjoint_hb

        return conjoint_hb
    if item.type == "paired":
        from app import paired_hb

        return paired_hb
    if item.type == "cva":
        from app import cva_hb

        return cva_hb
    return None


def _hb(item, with_hb=True, sources=None):
    """The current hierarchical Bayes result of an item (see hb_module), else None. sources: those of the export; an
    estimate of a pool without any of them (e.g. of the dummies for an export of the real participations) holds
    nobody of the file, its columns would stay empty (audit 2026-10-05, S23): None then."""
    module = hb_module(item) if with_hb else None
    result = module.current(item) if module else None
    if result and sources is not None and not set((result.get("pool") or {}).get("sources") or ["real"]) & set(sources):
        return None
    return result


def _lc(item, with_hb=True, sources=None):
    """The current latent class result (segments, app/segments.py) of a CBC or MaxDiff question, else None; the
    sources as for _hb."""
    from app import segments

    if not with_hb or item.type not in segments.TYPES:
        return None
    result = segments.current(item)
    if result and sources is not None and not set((result.get("pool") or {}).get("sources") or ["real"]) & set(sources):
        return None
    return result


def model_columns(item, with_hb=True, sources=None):
    """[(module, result)] of the stored models whose columns the export adds after the item: HB, then segments."""
    from app import segments

    out = []
    hb = _hb(item, with_hb, sources)
    if hb:
        out.append((hb_module(item), hb))
    lc = _lc(item, with_hb, sources)
    if lc:
        out.append((segments, lc))
    return out


# the look follows the device (theme with scheme "auto"): which one the person saw
SCHEME_COLUMN = ("scheme", "Gezeigte Darstellung: light | dark | mixed (wechselte während der Teilnahme); leer = nicht erfasst")


def scale_defs(survey):
    """{item id: [(column, label, subscale, codes)]}: the scale scores the export adds on request (app/scales.py)."""
    from app import scales, segments

    items = _items(survey)
    # The names only have to avoid the columns of the file. Those of the stored HB and latent class estimates
    # (<code>_hb_…, <code>_lk…) are in it only while the estimate fits the data, and finding that out builds the
    # whole table of its data pool: 3 s with 2000 participations on the data page (performance audit 2026-10-09,
    # N-6). So first without them; only a scale column that could meet one of them is named with the check.
    prefixes = tuple(p for i in items if hb_module(i) or i.type in segments.TYPES for p in (f"{i.code}_hb_", f"{i.code}_lk"))
    found = scales.export_scales(items, {c for c, _ in columns(survey, with_hb=False)})
    if prefixes and any(col.startswith(prefixes) for _, cols in found for col, *_ in cols):
        found = scales.export_scales(items, {c for c, _ in columns(survey)})
    return {item.id: cols for item, cols in found}


def columns(survey, with_hb=True, scales=False, sources=None, standard=False):
    """[(column, label)] of the data export. scales: with the scale scores after the columns of their item.
    standard: with the KonsortSWD standard variables x_... at the end (app/standard_vars.py).
    sources: those of the export, for the HB columns (_hb)."""
    from app import geoip, loops, paradata

    defs = scale_defs(survey) if scales else {}
    from app import coding

    coded, coded_loops = coding.plan(survey), coding.plan(survey, loops=True)
    cols = meta(survey)
    if survey.paradata:
        cols += paradata.COLUMNS
    if getattr(survey, "page_timing", False):
        cols += [(c, label) for c, label, _ in paradata.timing_columns(survey)]
    if getattr(survey, "focus_paste", False):
        cols += paradata.FOCUS_COLUMNS + (paradata.INPUT_COLUMNS if getattr(survey, "focus_inputs", False) else [])
    if getattr(survey, "geo_ip", False):
        cols += geoip.COLUMNS
    if getattr(survey, "seal", False):  # the seal chain (app/seal.py)
        from app import seal

        cols += seal.COLUMNS
    if survey.live is not None:  # a live vote (app/live/): its runs
        cols += LIVE_COLUMNS
    if survey.sampling is not None:  # a diary study (app/diary.py): person, part and time of each entry
        cols += DIARY_COLUMNS
    from app import tempo

    if tempo.active(survey):
        cols += tempo.COLUMNS
    from app import pagetime, quality

    if pagetime.any_timer(survey):
        cols.append(pagetime.COLUMN)
    if pagetime.any_more(survey):
        cols.append(pagetime.COLUMN_MORE)
    if getattr(survey, "block_load", False):  # blocks loaded at once: page times measured in the browser (app/blockload.py)
        from app import blockload

        cols += blockload.COLUMNS

    cols += quality.columns(_items(survey), tempo.active(survey), bool(getattr(survey, "ai_bait", False)),
                            bool(getattr(survey, "focus_paste", False) and getattr(survey, "focus_inputs", False)))
    if theme.records_scheme(survey):
        cols.append(SCHEME_COLUMN)
    from app import panel

    cols += [(f"url_{name}", f"URL-Parameter {name} (gespeichert, pseudonym)") for name in panel.stored_names(survey)]
    for quota in survey.quotas:
        cols += [(f"quota_{quota.code}", f"Quote „{quota.name}“: Teilquote der Person (Code)"),
                 (f"quota_{quota.code}_res", f"Quote „{quota.name}“: Ergebnis der Quotenberechnung")]
    from app import weights

    if weights.active(survey):  # the weight of a participation (raking, page "Quoten")
        cols.append(weights.COLUMN)
    from app import external

    cols += external.columns(survey)  # external sections: seconds outside and what the web app handed back
    from app import webservice

    cols += webservice.columns(survey)  # web services of the flow: whether the call worked, the values taken
    for loop in _loops(survey):
        cols.append((f"{loop['code']}_order", "Loop: gezeigte Tabellenzeilen in Reihenfolge (? = Zeile inzwischen gelöscht)"))
    for node in _randomizers(survey):
        cols.append((node["code"], f"Bedingung „{node.get('name')}“: gezeigte Elemente in Reihenfolge"
                                   + ("; leer, wo die Person den Zweig nicht erreichte" if _behind_branch(survey, node) else "")))
    for item in _items(survey):
        rows = _loop_rows(item)
        if rows:
            for n, rid in rows:
                caption = loops.row_caption(item.block, rid, n)  # what _n stands for: the values of that table row
                cols += [(f"{c}_{n}", f"{label} [{caption}]") for c, label in _plain_columns(item)]
                cols += [(f"{c}_{n}", f"{label} [{caption}]") for c, label in coding.export_columns(coded_loops.get(item.id, []))]
                cols += [(f"{c}_{n}", f"{label} [{caption}]") for c, label, _, _ in defs.get(item.id, [])]
                if item.kind.order_codes(item):
                    cols.append((f"{item.code}_order_{n}", f"Gezeigte Reihenfolge (Variablennamen) [{caption}]"))
                if item.kind.kept_start_label:
                    cols.append((f"{item.code}_unchanged_{n}", f"{item.kind.kept_start_label} [{caption}]"))
            continue
        cols += _plain_columns(item)
        cols += coding.export_columns(coded.get(item.id, []))  # categories of open answers (app/coding.py)
        cols += [(c, label) for c, label, _, _ in defs.get(item.id, [])]
        for module, result in model_columns(item, with_hb, sources):
            cols += module.export_columns(item, result)
        if item.kind.order_codes(item):
            cols.append((f"{item.code}_order", "Gezeigte Reihenfolge (Variablennamen)"))
        if item.kind.kept_start_label:  # the starting state confirmed as it was (UI/UX audit 2026-10-16, 105)
            cols.append((f"{item.code}_unchanged", item.kind.kept_start_label))
    for n, block_id, _, passes in _randomized_pages(survey):
        if passes:
            from app import loops
            from app.models import Block

            block = db.session.get(Block, block_id)
            cols += [(f"page{n}_order_{k}", f"Reihenfolge der Elemente auf Seite {n} [{loops.row_caption(block, rid, k)}]")
                     for k, rid in passes]
        else:
            cols.append((f"page{n}_order", f"Reihenfolge der Elemente auf Seite {n}"))
    if standard:
        from app import standard_vars

        cols += standard_vars.columns(survey)
    return cols


def _plain_columns(item):
    """Export columns of an item with labels as plain text (question texts may carry **bold** and the like)."""
    from app.items.base import plain

    return [(col, plain(label)) for col, label in item.kind.export_columns(item)]


def _fmt_time(dt):
    loc = to_local(dt)
    return loc.strftime("%Y-%m-%d %H:%M:%S") if loc else ""


def _responses(survey, include_incomplete, sources):
    """The participations of a data pool, oldest first, filtered by the database: before, every participation of
    the source came with all its answers, the broken-off ones too, and was left out only in Python (performance
    audit 2026-10-18, PERF-09: the answers of the objects are most of an export's memory, 20 KB per participation)."""
    query = (db.select(Response).filter(Response.survey_id == survey.id, Response.source.in_(sources or ("real",)),
                                        Response.status != "review",
                                        *(() if include_incomplete else (Response.status == Response.COMPLETED,)))
             .order_by(Response.id))
    return list(db.session.scalars(query))


class Table(list):
    """The rows of rows(): a list of dicts, which also knows the participations it was built from and keeps
    what an analysis derived from it. The results page reads the participations once; the choice models used
    to fetch them again by pid, and conjoint and MaxDiff built their choice sets twice (model and HB status)."""

    def __init__(self, rows=(), responses=None, numbered=None):
        super().__init__(rows)
        self.responses = responses if responses is not None else {}  # {pid: Response}
        self.numbered = numbered if numbered is not None else {}  # {resp_id: Response}, also without a pid
        self._kept = {}

    def keep(self, test):
        """The rows that pass `test`, again as a Table of the same participations."""
        return Table([r for r in self if test(r)], self.responses, self.numbered)

    def once(self, key, build):
        """build(), computed once per table and key. The result is shared: callers must not change it."""
        if key not in self._kept:
            self._kept[key] = build()
        return self._kept[key]


def rows(survey, include_incomplete=False, sources=("real",), speeder=None, speed_real=True,
         speed_sources=None, with_hb=True, progress=None, drop_speeders=False, with_quality=True, scales=False,
         standard=False):
    """One dict per response (a Table), see _rows. What the element types prepare once per element and not per
    participation is kept for this call (items.base.export_plan; code review 2026-10-07, R4)."""
    from app.items.base import export_plan

    from app import export_tables

    options = {"include_incomplete": include_incomplete, "sources": sources, "speeder": speeder,
               "speed_real": speed_real, "speed_sources": speed_sources, "with_hb": with_hb,
               "drop_speeders": drop_speeders, "with_quality": with_quality, "scales": scales, "standard": standard}
    with export_plan():
        return export_tables.table(survey, options, lambda: _rows(
            survey, include_incomplete, sources, speeder, speed_real, speed_sources, with_hb, progress,
            drop_speeders, with_quality, scales, standard))


def _rows(survey, include_incomplete=False, sources=("real",), speeder=None, speed_real=True,
          speed_sources=None, with_hb=True, progress=None, drop_speeders=False, with_quality=True, scales=False,
          standard=False):
    """One dict per response (a Table). with_hb: add the columns of current hierarchical Bayes results.
    with_quality: add the response patterns (qc_..., app/quality.py); pages that only summarise the answers
    leave them out, they are more than half of the time of this function. qc_marked is always there: where the
    survey's marking rule can mark someone besides speeders (quality.can_mark), the patterns are computed for it.
    progress(done, total): called now and then while the responses are read (results page).
    drop_speeders: leave out what quality control marks (qc_marked: speeders and participations with enough
    conspicuous things, app/quality.py); resp_id stays the number in the whole pool.
    scales: add the scale scores (app/scales.py), the mean per person as on the results page.
    standard: add the KonsortSWD standard variables x_... (app/standard_vars.py)."""
    speeder = threshold(survey, speeder)
    responses = _responses(survey, include_incomplete, sources)
    speed_from = tuple(speed_sources) if speed_sources else (("real",) if speed_real else SOURCES)
    # the page times of the pool are read with it; the median comes from another pool only if one is named
    speed = Speed(survey, speed_from, speeder, responses=responses if set(speed_from) == set(sources) else None)
    from app import paradata as _pd

    timing = _pd.timing_columns(survey) if getattr(survey, "page_timing", False) else []
    scheme_recorded = theme.records_scheme(survey)
    from app import loops, tempo

    # the tempo hint names a page like its timing column, without the "t_"
    tempo_pages = {key: c[2:] for c, _, key in _pd.timing_columns(survey)} if tempo.active(survey) else None
    from app import pagetime

    timer, more = pagetime.any_timer(survey), pagetime.any_more(survey)
    from app import blockload

    block_load = bool(getattr(survey, "block_load", False))
    items = _items(survey)
    by_id = {i.id: i for i in items}
    from app import quality

    patterns = quality.context(survey, items)
    marking = quality.can_mark(patterns)
    if (with_quality or marking) and patterns["open"]:  # open answers that several participations of the pool gave
        quality.index_texts(patterns, responses)
    random_pages = _randomized_pages(survey)
    hb_values = [(module.export_values(i, res), [c for c, _ in module.export_columns(i, res)])
                 for i in items for module, res in model_columns(i, with_hb, sources)]
    from app.quotas import RES as quota_res

    quota_cells = {cell.id: cell.code for quota in survey.quotas for cell in quota.cells}
    from app import flow

    # What is the same for every participation, found once: randomizers, loops and per item its type, its loop
    # rows and its order codes. Each of them walks the flow tree or builds a table of codes, and asking again
    # for every participation was a third of the time of this function.
    randomizers = [(node, {flow.node_id(c): c for c in node.get("children", [])}) for node in _randomizers(survey)]
    loop_nodes = _loops(survey)
    from app import external

    ext_nodes = external.nodes(survey)
    from app import webservice

    ws_nodes = webservice.nodes(survey)
    per_item = [(item, item.kind, str(item.id), _loop_rows(item), item.kind.order_codes(item), item.code) for item in items]
    from app import coding

    coded = coding.plan(survey)  # categories of open answers, after the columns they belong to
    coded_loops = coding.plan(survey, loops=True)  # the same per pass of a loop
    from app import scales as scale_logic

    score_items = [(by_id[i], found, [n for n, _ in (_loop_rows(by_id[i]) or [])])
                   for i, found in (scale_defs(survey) if scales else {}).items()]
    relayout_col = RELAYOUT_COLUMN in meta(survey)
    all_runs = None
    if RUN_COLUMN in meta(survey):
        from app import lifecycle

        all_runs = lifecycle.runs(survey)
    lang_col = LANG_SWITCH_COLUMN in meta(survey)
    mode_col = MODE_COLUMN in meta(survey)
    if getattr(survey, "seal", False):
        from app import seal

        sealed = seal.export_map(survey)
    persons, triggers = {}, {}
    if survey.sampling is not None:  # the person ids of a diary study, in one query
        from app.models import Enrolment

        persons = dict(db.session.execute(db.select(Enrolment.id, Enrolment.pid).filter_by(survey_id=survey.id)).all())
        from app import diary

        triggers = {m["key"]: (m.get("trigger") or {}).get("t") for m in diary.modules(survey)}
    from app import standard_vars

    standard_from = standard_vars.sources(survey) if standard else None
    out = []
    for n, r in enumerate(responses, start=1):
        if progress and n % 20 == 1:
            progress(n, len(responses))
        answers = r.answers or {}
        end = r.completed_at or (r.last_active_at if r.status in (Response.DECLINED, *Response.ENDED_EARLY) else None)
        row = {
            "resp_id": n,
            "pid": r.pid,
            "source": r.source,
            "qversion": r.qversion or 1,
            "status": r.status,
            "lang": r.language,
            "assignment_network_starts": (r.paradata or {}).get("_assignment_network_starts", ""),
            "started": _fmt_time(r.started_at),
            "finished": _fmt_time(end),
            # when a participation that did not end was last active: the moment of breaking off (T-6)
            "last_active": _fmt_time(r.last_active_at) if r.status == Response.ACTIVE and r.channel != "paper" else "",
            "duration_s": int((end - r.started_at).total_seconds()) if end and r.channel != "paper" else "",
            "last_page": (r.position or 0) + 1,
        }
        if mode_col:
            row[MODE_COLUMN[0]] = "paper" if r.channel == "paper" else "online"
        if lang_col:
            row[LANG_SWITCH_COLUMN[0]] = _pd.language_switched(r)
        if all_runs is not None:
            row[RUN_COLUMN[0]] = lifecycle.run_of(all_runs, r.started_at)
        if relayout_col:
            row[RELAYOUT_COLUMN[0]] = int((r.paradata or {}).get("_relayout") or 0)
        # a diary study: against the median of the entry's part, as everywhere else (audit 2026-10-12, E-1)
        median = speed.median_of(r) if r.status == Response.COMPLETED else None
        if r.status == Response.COMPLETED and end and median and row["duration_s"] != "" and row["duration_s"] >= 0:  # negative: a clock error
            # whole seconds, as in duration_s and in the median: the flag can be recomputed from the
            # export and matches the slider on the page (which sees the same whole seconds)
            rel = row["duration_s"] / median
            row["duration_rel"] = units.round_half_up(rel, 3)
            if speed.rule == "duration":
                row["speeder"] = int(rel < speeder / 100)
        if speed.timing and r.status == Response.COMPLETED:
            value = speed.rsi(r)
            row[RSI_COLUMN[0]] = "" if value is None else value
            if speed.rule == "rsi":
                row["speeder"] = int(value is not None and value > speed.limit)
        if survey.paradata:
            row.update({k: v for k, v in (r.paradata or {}).items() if not k.startswith("_")})
        if getattr(survey, "page_timing", False):
            times = (r.paradata or {}).get("_t") or {}
            row.update({c: times.get(key) for c, _, key in timing})
        if getattr(survey, "focus_paste", False):
            row.update(_pd.focus_values(r))
        if getattr(survey, "geo_ip", False):
            row.update({"geo_country": r.geo_country or "", "geo_state": r.geo_state or ""})
        if getattr(survey, "seal", False):
            case, digest = sealed.get(r.id, ("", ""))
            row.update({"seal_seq": case, "seal_hash": digest})
        if survey.live is not None:
            row["durchgang"] = (r.params or {}).get("live_run_name", "")
            row["durchgang_id"] = (r.params or {}).get("live_run", 0)
            # the run it was given in, also after its run was merged into another one (live.control._merge_run)
            row["durchgang_ursprung"] = (r.params or {}).get("live_run_origin_name", row["durchgang"])
            row["durchgang_ursprung_id"] = (r.params or {}).get("live_run_origin", row["durchgang_id"])
            row["weiteres_wort"] = int(bool((r.params or {}).get("live_extra")))
            row["hauptteilnahme"] = (r.params or {}).get("live_main", "") if row["weiteres_wort"] else ""
        if survey.sampling is not None:
            row.update(_diary_values(r, persons, triggers))
        if tempo_pages is not None:
            row.update(tempo.export_values(survey, r, tempo_pages))
        if timer and r.source != "dummy":
            row[pagetime.COLUMN[0]] = pagetime.export_value(r)
        if more and r.source != "dummy":
            row[pagetime.COLUMN_MORE[0]] = pagetime.export_more(r)
        if block_load and r.source != "dummy":
            row["pages_browser"], row["times_cut"] = blockload.pages_timed(r), blockload.times_cut(r)
        # the marking needs the patterns even where the columns themselves are left out
        if with_quality or marking:  # once per request (quality.request_values): exports read the pool more than once
            found = quality.request_values(survey, r, patterns)
            row.update({k: v for k, v in found.items() if k != "_notable"} if with_quality else {"qc_flags": found["qc_flags"]})
        row["qc_marked"] = int(quality.marked(row, patterns["rules"]["min"]))
        if scheme_recorded:
            row["scheme"] = (r.paradata or {}).get("_scheme", "")
        row.update({f"url_{k}": v for k, v in (r.params or {}).items()})
        for quota in survey.quotas:  # what the quota calculation decided for this participation (app/quotas.py)
            entry = (r.quota or {}).get(str(quota.id))
            if entry:
                row[f"quota_{quota.code}"] = quota_cells.get(entry.get("cell"), "")
                row[f"quota_{quota.code}_res"] = quota_res[entry.get("res")][0] if entry.get("res") in quota_res else ""
        if ext_nodes:
            row.update(external.export_values(ext_nodes, r))
        if ws_nodes:
            row.update(webservice.export_values(ws_nodes, r))
        layout = r.layout or {}
        cj, stmt = layout.get("cj") or {}, layout.get("stmt") or {}
        drawn = flow.reached_conditions(r.layout)  # empty for a randomizer behind a branch the person never reached
        for node, children in randomizers:
            picked = drawn.get(node["id"], [])
            row[node["code"]] = "|".join(flow.child_label(survey, children[c]) for c in picked if c in children)
        for loop in loop_nodes:
            shown = (layout.get("loops") or {}).get(loop["id"], [])
            # "?": a row taken out of the table since (before rows with real answers stayed, loops.update_from_form)
            row[f"{loop['code']}_order"] = "|".join(str(loops.row_number(loop, rid) or "?") for rid in shown)
        for item, kind, key, loop_rows, codes, code in per_item:
            if loop_rows:
                for n, rid in loop_rows:
                    answer = answers.get(f"{key}#{rid}")
                    values = {**kind.export_values(item, answer),
                              **kind.export_response_values(item, answer, r)}
                    row.update({f"{k}_{n}": v for k, v in values.items()})
                    if item.id in coded_loops:
                        row.update({f"{k}_{n}": v for k, v in coding.export_values(item, coded_loops[item.id], r, rid).items()})
                    if codes:
                        order = stmt.get(f"{key}#{rid}") or []
                        row[f"{code}_order_{n}"] = "|".join(f"{codes[sid]}_{n}" if sid in codes else "?" for sid in order)
                    if kind.kept_start_label:
                        row[f"{code}_unchanged_{n}"] = kind.kept_start(item, answer, stmt.get(f"{key}#{rid}") or [])
                continue
            answer = answers.get(key)
            row.update(kind.export_values(item, answer))
            row.update(kind.export_response_values(item, answer, r))
            row.update(kind.export_state_values(item, cj.get(key) or {}))
            if item.id in coded:
                row.update(coding.export_values(item, coded[item.id], r))
            if codes:
                order = stmt.get(key) or []
                row[f"{code}_order"] = "|".join(codes.get(sid, "?") for sid in order)
            if kind.kept_start_label:
                row[f"{code}_unchanged"] = kind.kept_start(item, answer, stmt.get(key) or [])
        for item, found, passes in score_items:
            row.update(scale_logic.export_values(item, found, row, passes))
        for values, cols in hb_values:  # persons outside the HB result stay empty
            row.update(values.get(r.pid) or dict.fromkeys(cols))
        if standard_from is not None:
            row.update(standard_vars.values(survey, standard_from, r))
        pages = layout.get("pages") or []
        for page_no, block_id, ids, passes in random_pages:
            if passes:  # one order per pass, with the variable names of that pass
                for k, rid in passes:
                    row[f"page{page_no}_order_{k}"] = "|".join(
                        f"{by_id[i].code}_{k}" for i in _page_order(pages, block_id, ids, rid) if i in by_id)
                continue
            row[f"page{page_no}_order"] = "|".join(
                by_id[i].code for i in _page_order(pages, block_id, ids) if i in by_id
            )
        out.append(_writable(row))
    table = Table(out, {r.pid: r for r in responses if r.pid}, dict(enumerate(responses, start=1)))
    from app import weights

    weights.fill_column(survey, table)  # before leaving out the marked: they have no weight anyway
    return table.keep(lambda r: r.get("qc_marked") != 1) if drop_speeders else table


def _writable(row):
    """The row with texts every file format can write: an answer stored with a lone surrogate (before they were
    refused on reading, audit 2026-10-13 TN-20) made CSV, SAV, RDS, OMV and SPSS fail with a 500."""
    for key, value in row.items():
        if isinstance(value, str):
            clean = drop_surrogates(value)
            if clean is not value:
                row[key] = clean
    return row


def _cell(value, decimal_comma, safe=True):
    if value is None:
        return ""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, float):
        # whole numbers without ".0" and in full: "%g" would round 1234567 to 1.23457e+06
        text = str(int(value)) if value.is_integer() and abs(value) < 1e15 else repr(value)
        return text.replace(".", ",") if decimal_comma else text
    value = drop_surrogates(value)  # long formats read the stored answers themselves (audit 2026-10-13, TN-20)
    return safe_text(value) if safe else value


def _label(text):
    """A value label inside the codebook's "code = label; ..." list: the ";" separates entries."""
    return str(text or "").replace(";", ",")


FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def safe_text(value):
    """Text that Excel or LibreOffice would run as a formula (=, +, -, @ at the start, e.g. a free-text answer
    "=HYPERLINK(...)") gets a leading apostrophe; numbers written as text ("-3", "+1.5") stay as they are. Spaces
    and other whitespace before such a sign do not hide it (" =cmd|..."; OWASP CSV injection; audit
    Datenspeicherung + Berechnungen 2026-10-21, C4)."""
    if not isinstance(value, str) or not (value.startswith(FORMULA_START) or value.lstrip().startswith(FORMULA_START)):
        return value
    try:
        float(value.replace(",", "."))
        return value
    except ValueError:
        return "'" + value


# a codebook entry of values that starts with a negative code: "-3 = stimme überhaupt nicht zu", "-90 bis 90"
_NEGATIVE_CODE = re.compile(r"-\d+(?:[.,]\d+)? (?:=|bis|to) ")


def codebook_text(value):
    """safe_text for the columns "values" and "notes" of the codebook CSV: an entry that starts with a negative code
    (-3 = …, -90 bis 90, -97 = nicht bearbeitbar) keeps its minus without an apostrophe, so R, Python and LibreOffice
    read it as written (audit 2026-10-12, E-4); with "(" or "|" (a function, a DDE call) it is guarded as before."""
    if isinstance(value, str) and _NEGATIVE_CODE.match(value) and not any(c in value for c in "(|"):
        return value
    return safe_text(value)


# Published data (Zenodo) leaves out what could single out a person together with other sources: the
# participation id, stored URL parameters (panel ids) and the device information; start and end are
# reduced to the month.
PUBLIC_TIME_COLUMNS = ("started", "finished", "last_active", "faellig", "benachrichtigt", "eingang")
# a diary study (app/diary.py) keeps its persons apart in published data, but under new numbers (1, 2, ...) in a
# random order that exists only in that file: the pseudonymous person id stays behind (review 2026-10-11, R9)
PUBLIC_PERSON_NOTE = ("zufällige Nummer der Person, nur in diesem veröffentlichten Datensatz gültig",
                      "random number of the person, valid only in this published data set")


def public_persons(rows):
    """{person id: 1..n} in a random order, for the rows of a published diary study."""
    import random

    ids = sorted({r.get("person") for r in rows if r.get("person")})
    random.SystemRandom().shuffle(ids)
    return {pid: n for n, pid in enumerate(ids, start=1)}


def is_public_column(code):
    from app import paradata

    return (code not in ("pid", "hauptteilnahme", "assignment_network_starts") and not code.startswith("url_") and code not in {c for c, _ in paradata.COLUMNS}
            and code != SCHEME_COLUMN[0])  # a setting of the device, like the technical information


def open_columns(survey):
    """Columns with free text, exact map positions or postal codes: published only after an explicit
    confirmation (they can name or locate a person)."""
    out = set()
    for item in _items(survey):
        picked = open_item_columns(item)
        if not picked:
            continue
        rows = _loop_rows(item)
        out.update(f"{c}_{n}" for n, _ in rows for c in picked) if rows else out.update(picked)
    from app import external

    for node in external.nodes(survey):  # free text a web app handed back from an external section
        out.update(f"{node['code']}_{spec['name']}" for spec in external.values(node) if spec.get("kind") == "text")
    from app import webservice

    for node in webservice.nodes(survey):  # text a web service answered (it may repeat what was sent)
        out.update(f"{node['code']}_{spec['name']}" for spec in webservice.values(node) if spec.get("kind") == "text")
    return out


def open_item_columns(item):
    """The export columns of one question (without a loop suffix) that open_columns() counts as open answers;
    [] for a question without any. Also what the vignette long format leaves out without the tick. Each element type
    names them itself (ItemType.open_columns), so a new type with free text cannot slip through."""
    return list(item.kind.open_columns(item)) + list(item.kind.place_columns(item))  # places like PLZ (audit 2026-10-10)


def csv_chunks(survey, include_incomplete=False, separator="comma", sources=("real",), speeder=None,
           speed_real=True, speed_sources=None, public=False, open_answers=False, drop_speeders=False, scales=False,
           standard=False):
    semicolon = separator == "semicolon"
    buf = io.StringIO()
    buf.write("﻿")  # BOM: Excel opens UTF-8 correctly
    writer = csv.writer(buf, delimiter=";" if semicolon else ",", lineterminator="\r\n")
    from app import vignette_positions

    hidden = vignette_positions.hidden(survey, open_columns(survey)) if public and not open_answers else set()
    cols = [c for c in vignette_positions.columns(survey, columns(survey, scales=scales, sources=sources,
                                                                  standard=standard))
            if not public or (is_public_column(c[0]) and c[0] not in hidden)]
    writer.writerow([c for c, _ in cols])
    data = vignette_positions.rows(survey, rows(survey, include_incomplete, sources, speeder, speed_real, speed_sources,
                                                drop_speeders=drop_speeders, scales=scales, standard=standard))
    if survey.sampling is not None:  # a diary study: the prompts nobody answered, as rows without answers
        from app import diary

        data = list(data) + diary.missed_rows(survey, data, sources)
    persons = public_persons(data) if public and survey.sampling is not None else {}
    def write_chunks():
        for row in data:
            if public:
                row.update({c: row[c][:7] for c in PUBLIC_TIME_COLUMNS if row.get(c)})  # YYYY-MM
                if "person" in row:
                    row["person"] = persons.get(row["person"], "")
            writer.writerow([_cell(row.get(c), semicolon) for c, _ in cols])
            if buf.tell() >= 65536:
                yield buf.getvalue()
                buf.seek(0)
                buf.truncate(0)
        if buf.tell():
            yield buf.getvalue()
    return write_chunks()



def to_csv(survey, include_incomplete=False, separator="comma", sources=("real",), speeder=None,
           speed_real=True, speed_sources=None, public=False, open_answers=False, drop_speeders=False, scales=False,
           standard=False):
    """String API for archives/snapshots. Web/API downloads use csv_file instead of duplicating the full file."""
    return "".join(csv_chunks(survey, include_incomplete, separator, sources, speeder, speed_real, speed_sources,
                              public, open_answers, drop_speeders, scales, standard))


def csv_file(survey, include_incomplete=False, separator="comma", sources=("real",), speeder=None,
             speed_real=True, speed_sources=None, public=False, open_answers=False, drop_speeders=False, scales=False,
             standard=False):
    """Encode in chunks and stream from a private temporary file. Errors happen before HTTP headers are sent.
    Only the first 2 MB stay in memory; rolled files are private/unlinked and closed with the response."""
    import tempfile

    out = tempfile.SpooledTemporaryFile(max_size=2 * 1024 * 1024, mode="w+b", suffix=".csv")
    try:
        for chunk in csv_chunks(survey, include_incomplete, separator, sources, speeder, speed_real, speed_sources,
                                public, open_answers, drop_speeders, scales, standard):
            out.write(chunk.encode("utf-8"))
        out.seek(0)
        return out
    except BaseException:
        out.close()
        raise


def resp_id_note(lang=None, public=False):
    """The codebook note of resp_id (audit Datenspeicherung + Berechnungen 2026-10-21): a running number of this export
    that depends on its pool, so pid joins exports (C6; a public package has no pid); and what an empty cell of a whole
    question means (C3). lang: the reader's language (app.items.base.reader)."""
    from app.items.base import in_lang, reader

    ui = reader(lang)
    join = (in_lang(ui, "Dateien verschiedener Exporte nicht über resp_id zusammenführen",
                    "do not join files of different exports by resp_id") if public else
            in_lang(ui, "zum Zusammenführen verschiedener Exporte pid verwenden", "to join different exports use pid"))
    return in_lang(ui, "laufende Nummer in diesem Export: Hängt von der Auswahl (Quellen, unvollständige Teilnahmen) ab "
                       "und ändert sich nach Löschungen; {join}. Allgemein: Bei ganzen Fragen ist „nicht gezeigt“ ebenso "
                       "leer wie „gesehen, nicht beantwortet“; {n} = „nicht gezeigt“ gibt es nur bei einzelnen Zeilen "
                       "und Optionen",
                   "running number in this export: it depends on the selection (sources, incomplete participations) and "
                   "changes after deletions; {join}. In general: for whole questions “not shown” is empty just like "
                   "“seen, not answered”; {n} = “not shown” exists only for single rows and options",
                   join=join, n=row_logic.NOT_SHOWN)


def codebook(survey, speeder=None, speed_real=True, speed_sources=None, drop_speeders=False, scales=False, cols=None,
             lang=None, standard=False):
    """Rows: variable, label, type, values, notes. drop_speeders: the data file this belongs to leaves them out.
    scales: the data file has the scale scores (app/scales.py). cols: columns(survey) if the caller has them.
    standard: the data file has the KonsortSWD standard variables (app/standard_vars.py), listed at the end.
    lang: the language of the person the codebook is written for (None: the page's, German outside a request;
    concept "Mehrsprachige Oberfläche", E8): the labels of Askurious's own columns, the notes and the describing values
    follow it; variable names, the labels of the questions and the value labels stay as they are, and the type stays
    German, a key that _spss_dictionary reads (codebook_csv names it with TYPES_EN)."""
    from app.items.base import LEVEL_LABELS_EN, in_lang, reader

    ui = reader(lang)
    en = ui == "en"

    def t(de, en_text, **values):
        return in_lang(ui, de, en_text, **values)

    levels = LEVEL_LABELS_EN if en else LEVEL_LABELS
    preload(survey)
    constructs.prefetch(survey)  # constructs.info for every element without a query each (P12)
    speeder = threshold(survey, speeder)
    lang = survey.default_language  # from here on: the survey language of the value labels
    from app import loops, paradata

    median = median_seconds(survey, speed_real, speed_sources)
    names = SOURCE_NAMES_EN if en else SOURCE_NAMES
    basis = (" + ".join(names[x] for x in speed_sources) if speed_sources
             else names["real"] if speed_real else t("echte, Test- und Dummy-Teilnahmen (verfälscht)",
                                                     "real, test and dummy participations (distorted)"))
    dropped = (t("; markierte Teilnahmen (qc_marked) sind ausgeschlossen", "; marked participations (qc_marked) are left out")
               if drop_speeders else t("; nur markiert, nichts entfernt", "; only marked, nothing removed"))
    notes = {
        "duration_rel": (t("Median: {m} s, Basis: {basis}", "median: {m} s, basis: {basis}", m=units.round_half_up(median), basis=basis)
                         if median else t("noch keine abgeschlossenen Teilnahmen", "no completed participations yet")),
        "speeder": t("Schwelle: {pct} % des Medians", "threshold: {pct} % of the median", pct=speeder)
                   + (f" = {units.round_half_up(median * speeder / 100)} s" if median else "")
                   + dropped + f"; {LEINER}",
        RSI_COLUMN[0]: rsi_note(basis, ui),
    }
    if getattr(survey, "sampling", None) is not None and median:
        # a diary study measures every entry against the median of its part (Speed.median_of; audit 2026-10-12, E-1)
        from app import diary

        parts = [(k, median_seconds(survey, speed_real, speed_sources, part=k))
                 for k in ["entry"] + [m["key"] for m in diary.modules(survey)] + ["exit"]]
        parts = ", ".join(f"{k} {units.round_half_up(m)} s" for k, m in parts if m)
        notes["duration_rel"] = t("Median je Teil: {parts}; Basis: {basis}", "median per part: {parts}; basis: {basis}",
                                  parts=parts, basis=basis)
        notes["speeder"] = (t("Schwelle: {pct} % des Medians des Teils", "threshold: {pct} % of the median of the part",
                              pct=speeder) + dropped + f"; {LEINER}")
    if speeder_rule(survey) == "rsi":
        limit = units.fixed(rsi_limit(survey), 1)
        notes["speeder"] = (t("Regel: Relative Speed Index (qc_rsi) über {limit}", "rule: Relative Speed Index (qc_rsi) above {limit}",
                              limit=limit if en else limit.replace('.', ','))
                            + dropped + f"; {LEINER}")
    if survey.live is not None:  # threshold(): no speeders in a live vote
        notes["speeder"] = t("Live-Abstimmung: keine Speeder-Markierung (die Dauer reicht vom Beitritt bis zur letzten Antwort)",
                             "live poll: no speeder marking (the duration runs from joining to the last answer)")
    notes["resp_id"] = resp_id_note(ui)
    notes[RELAYOUT_COLUMN[0]] = t("Antworten von vor dem Neubeginn bleiben gespeichert, soweit ihre Fragen noch "
                                  "vorkommen; qversion nennt die Fassung, auf der die Teilnahme endete",
                                  "answers from before the restart stay stored as far as their questions still exist; "
                                  "qversion names the revision on which the participation ended")
    if has_runs(survey):
        from app import lifecycle

        notes[RUN_COLUMN[0]] = "; ".join(
            f"{r['n']} = {r['name']} ({t('ab', 'from')} {_fmt_time(r['since']) if r['since'] else t('Start', 'start')})"
            for r in lifecycle.runs(survey))
    out = [(c, label, "meta", "0 | 1" if c == "speeder" else t("zwei Nachkommastellen", "two decimal places") if c == RSI_COLUMN[0]
            else "0, 1, 2, …" if c in (RELAYOUT_COLUMN[0], "assignment_network_starts") else "", notes.get(c, "")) for c, label in meta(survey)]
    from app import scales as scale_logic

    defs = scale_defs(survey) if scales else {}
    count, seconds = t("Anzahl", "count"), t("Sekunden", "seconds")
    counted_in_browser = t("im Browser gezählt, mit jeder Seite gesendet; nur Zahlen, nie Inhalte; ",
                           "counted in the browser, sent with every page; numbers only, never content; ")
    if survey.paradata:
        out += [(c, label, "Paradaten", "", t("grob, ohne Versionen; einmal zu Beginn erfasst",
                                              "coarse, without versions; recorded once at the start")) for c, label in paradata.COLUMNS]
    if getattr(survey, "page_timing", False):
        out += [(c, label, "Paradaten", seconds, t("auf dem Server gemessen: vom Senden der Seite bis zum nächsten "
                                                   "Absenden; höchstens 3600 je Besuch",
                                                   "measured on the server: from sending the page to the next submit; at "
                                                   "most 3600 per visit")) for c, label, _ in paradata.timing_columns(survey)]
    if getattr(survey, "focus_paste", False):
        out += [(c, label, "Paradaten", count, counted_in_browser + t(
            "Verlassen und Zurückkehren zählt einmal; leer = keine Seite mit Zählung gesendet (z. B. ohne "
            "JavaScript, Dummy-Daten). {source}",
            "leaving and coming back counts once; empty = no page with counts sent (e.g. without JavaScript, dummy "
            "data). {source}", source=paradata.DIEDENHOFEN)) for c, label in paradata.FOCUS_COLUMNS]
        if getattr(survey, "focus_inputs", False):
            out += [(c, label, "Paradaten", count, counted_in_browser + t(
                "Eingaben: jeder Tastendruck, jedes Wort der Bildschirmtastatur oder des Diktats in einem Textfeld, "
                "ohne Einfügen und Löschen; leer = keine Seite mit Zählung gesendet",
                "inputs: every key typed, every word of the phone keyboard or of dictation in a text field, without "
                "pasting and deleting; empty = no page with counts sent")) for c, label in paradata.INPUT_COLUMNS]
    if getattr(survey, "geo_ip", False):
        from app import geoip

        out += [(c, label, "Paradaten", "", geoip.codebook_note(ui)) for c, label in geoip.COLUMNS]
    if getattr(survey, "seal", False):
        from app import seal

        out += [(c, label, "meta", "", t("Siegelkette: prüfbar mit seal.json und seal_verify.py aus dem Reiter "
                                          "„Export“", "seal chain: checkable with seal.json and seal_verify.py from "
                                          "the tab “Export”"))
                for c, label in seal.COLUMNS]
    if survey.live is not None:
        live_notes = LIVE_NOTES_EN if en else LIVE_NOTES
        out += [(c, label, "meta", "0 | 1" if c == "weiteres_wort" else "", live_notes.get(c, "")) for c, label in LIVE_COLUMNS]
    if survey.sampling is not None:
        diary_notes = DIARY_NOTES_EN if en else DIARY_NOTES
        values = {"ausloeser": "fixed | random | self", "abfrage": "beantwortet | zur_pruefung | abgebrochen | verpasst", "erinnert": "0 | 1", "nachgereicht": "0 | 1"}
        out += [(c, label, "meta", values.get(c, ""), diary_notes.get(c, "")) for c, label in DIARY_COLUMNS]
    from app import tempo

    if tempo.active(survey):
        note = tempo.codebook_note(survey, ui)
        out += [(c, label, "Paradaten", "", note if c == "tempo_checked" else t(
            "siehe tempo_checked; leer = keine Seite geprüft (Dummy-Daten)",
            "see tempo_checked; empty = no page checked (dummy data)")) for c, label in tempo.COLUMNS]
    from app import pagetime, quality

    if pagetime.any_timer(survey):
        out.append((*pagetime.COLUMN, "Paradaten", count, pagetime.codebook_note(survey, ui=ui)
                    + t(" Leer = Dummy-Daten.", " Empty = dummy data.")))
    if pagetime.any_more(survey):
        out.append((*pagetime.COLUMN_MORE, "Paradaten", count, t(
            "„Mehr Zeit“ (bis zu zehnmal je Seite; mindestens 30 Sekunden je Zeitabschnitt) gibt es auf Seiten mit Höchstzeit, deren Zeit nicht zum "
            "Versuch gehört (Barrierefreiheit, WCAG 2.2.1). 0 = auf keiner Seite genommen. Leer = Dummy-Daten.",
            "“More time” (up to ten times per page; at least 30 seconds per interval) is offered on pages with a time limit whose time is not part "
            "of the experiment (accessibility, WCAG 2.2.1). 0 = taken on no page. Empty = dummy data.")))
    if getattr(survey, "block_load", False):
        from app import blockload

        out.append((*blockload.COLUMNS[0], "Paradaten", count, t(
            "Seiten einfacher Blöcke kommen am Stück in den Browser (Einstellung „Schneller blättern, auch bei schwachem Netz“); ihre "
            "Zeit misst der Browser, der Server prüft die Summe je Block gegen seine eigene Uhr. 0 = keine solche Seite. "
            "Leer = Dummy-Daten.",
            "Pages of simple blocks come to the browser in one piece (setting “Page through faster, even on a weak connection”); the browser "
            "measures their time, the server checks the sum per block against its own clock. 0 = no such page. "
            "Empty = dummy data.")))
        out.append((*blockload.COLUMNS[1], "Paradaten", t("0 oder 1", "0 or 1"), t(
            "1 = die Summe der Seitenzeiten eines Blocks war länger als die Zeit, die der Server zwischen dem Senden des "
            "Blocks und seinem Ende gemessen hat; die Seitenzeiten dieses Blocks wurden anteilig auf die Zeit des "
            "Servers gekürzt (die Spalten t_ und die Speeder-Prüfung rechnen mit den gekürzten Zeiten). Leer = Dummy-Daten.",
            "1 = the sum of a block's page times was longer than the time the server measured between sending the block "
            "and its end; the page times of that block were cut down in proportion to the server's time (the t_ columns "
            "and the speeder check use the cut times). Empty = dummy data.")))

    pattern_notes = quality.codebook_notes(_items(survey), survey, ui)
    out += [(c, label, "Qualität", t("0 bis 1", "0 to 1") if c in ("qc_straight", "qc_irv", "qc_missing", "qc_tempo")
             else t("−1 bis 1", "−1 to 1") if c == "qc_evenodd" else count, pattern_notes[c])
            for c, label in quality.columns(_items(survey), tempo.active(survey), bool(getattr(survey, "ai_bait", False)),
                                            bool(getattr(survey, "focus_paste", False) and getattr(survey, "focus_inputs", False)))]
    if theme.records_scheme(survey):
        out.append((*SCHEME_COLUMN, "Paradaten", "light | dark | mixed",
                    t("das Erscheinungsbild folgt der Einstellung des Geräts (hell oder dunkel); beim Absenden jeder Seite erfasst",
                      "the appearance follows the setting of the device (light or dark); recorded when each page is sent")))
    from app import flow, panel

    out += [(f"url_{name}", f"URL-Parameter {name} (gespeichert, pseudonym)", "URL-Parameter", "",
             t("aus dem Teilnahmelink übernommen", "taken from the participation link")) for name in panel.stored_names(survey)]
    from app import quotas as quota_mod

    for quota in survey.quotas:
        items_by_id = {i.id: i for i in _items(survey)}
        cells = "; ".join(f"{c.code} = {_label(c.name)} [{_label(quota_mod.cell_text(survey, c, lang, items_by_id))}, "
                          + t("Soll {n}]", "target {n}]", n=c.target) for c in quota.cells)
        kind = (quota_mod.MODES_EN if en else quota_mod.MODES).get(quota.mode, quota.mode)
        if quota.mode == "soft" and quota.tolerance:
            kind += t(", Toleranz {n}{unit}", ", tolerance {n}{unit}", n=quota.tolerance,
                      unit=' %' if quota.tolerance_pct else t(' Plätze', ' places'))
        if quota.count_at == "check":
            kind = t("zählt sofort an der Quotenberechnung (auch bei späterem Abbruch oder Screen-out)",
                     "counts at once at the quota check (also if the person drops out or is screened out later)")
        out.append((f"quota_{quota.code}", f"Quote „{quota.name}“: Teilquote der Person (Code)", "Quote", cells,
                    kind + t("; leer = die Quotenberechnung nicht erreicht oder keine Teilquote; es zählt die ",
                             "; empty = the quota check not reached or no sub-quota; what counts is ")
                    + (t("am wenigsten gefüllte passende Teilquote", "the least filled matching sub-quota") if quota.pick == "least"
                       else t("erste passende Teilquote", "the first matching sub-quota"))))
        out.append((f"quota_{quota.code}_res", f"Quote „{quota.name}“: Ergebnis der Quotenberechnung", "Quote",
                    "; ".join(f"{n} = {text}" for n, text in sorted(quota_mod.RES.values())),
                    t("einmal an der Quotenberechnung entschieden; leer = die Berechnung nicht erreicht",
                      "decided once at the quota check; empty = the check not reached")))
    from app import weights

    if weights.active(survey):
        out.append((*weights.COLUMN, "Gewicht", "", weights.codebook_note(survey, ui)))
    from app import external

    out += external.codebook(survey, ui)
    from app import webservice

    out += webservice.codebook(survey, ui)
    for node in _randomizers(survey):
        labels = [flow.child_label(survey, c) for c in node.get("children", [])]
        out.append((node["code"], f"Randomisierung „{node.get('name')}“", "Bedingung", " | ".join(labels),
                    t("{k} von {n}; {how}", "{k} of {n}; {how}", k=node.get('k', 1), n=len(labels),
                      how=t('balanciert', 'balanced') if node.get('balanced', True) else t('zufällig', 'random'))
                    + (t("; Reihenfolge zufällig", "; random order") if node.get("shuffle") else "")
                    + (t("; hinter einer Verzweigung: leer bei nicht erreichtem Zweig, balanciert wird unter denen, die "
                         "ihn erreichten",
                         "; behind a branch: empty where the branch was not reached, balanced among those who reached it")
                       if _behind_branch(survey, node) else "")))
    not_shown = t("{n} = nicht gezeigt", "{n} = not shown", n=row_logic.NOT_SHOWN)
    shown_only = t("nur gezeigt, wenn {cond}; {n} zählt als fehlend, leer = gezeigt und nicht beantwortet",
                   "shown only if {cond}; {n} counts as missing, empty = shown and not answered", cond="{cond}", n=row_logic.NOT_SHOWN)
    reversed_note = t("umgepolt, im Export bereits rekodiert", "reverse-coded, already recoded in the export")
    dont_know = t("{code} = weiß nicht", "{code} = don't know", code=DONT_KNOW_CODE)
    dont_know_note = t("{code} = „weiß nicht“, zählt als fehlend", "{code} = “don't know”, counts as missing", code=DONT_KNOW_CODE)
    from app import coding

    coded, coded_loops = coding.plan(survey), coding.plan(survey, loops=True)
    for item in _items(survey):
        kind, c = item.kind, item.cfg  # cfg: a setting the row lacks reads as its default
        shown_if_item = (t("Frage nur gezeigt, wenn {cond}; leer = nicht gezeigt oder nicht beantwortet",
                           "question shown only if {cond}; empty = not shown or not answered",
                           cond=item_condition.text(item, lang, ui)) if item_condition.of(item) else "")
        for col, label in _plain_columns(item):
            values, notes, typ = "", [], item.type
            info = kind.codebook_info(item, col, lang, ui)
            if info:
                typ, values, extra = info
                notes += extra
            elif item.type == "single_choice":
                values = "; ".join(f"{o['code']} = {_label(o['label'])}" for o in kind.options(item, lang))
                gone = kind.missing_codes(item, col)
                if gone:
                    notes.append(t("als fehlend gewertet: ", "counted as missing: ") + ", ".join(gone))
                    if spss_short(gone):  # C2: what SPSS cannot declare (spss_missing)
                        notes.append(t("in SPSS (.sav, .sps) sind nur die ersten drei als fehlend deklariert",
                                       "in SPSS (.sav, .sps) only the first three are declared missing"))
            elif item.type == "multiple_choice":
                values = t("0 = nicht gewählt; 1 = gewählt", "0 = not chosen; 1 = chosen")
                if kind.skipped_when_empty(item):  # with "keine davon" nothing ticked is skipped (B-23)
                    notes.append(t("nichts angekreuzt = fehlend (übersprungen)", "nothing ticked = missing (skipped)"))
                if col in kind.partial_columns(item) | kind.since_columns(item):  # carried, or added later (N-5)
                    values += "; " + not_shown
                    notes += kind._carry_note(item, col, ui)
            elif item.type == "likert":
                labels = kind.labels(item, lang)
                stmt = next((s for s in kind.statements(item, lang) if s["export_code"] == col), None)
                if stmt and stmt.get("reverse"):
                    labels = labels[::-1]  # the export is recoded, so the labels flip with it
                    notes.append(reversed_note)
                if stmt and stmt["id"] in kind.checks(item):
                    notes.append(t("Aufmerksamkeitsprüfung: Richtig ist {check}; zählt nicht in Skalenwert und "
                                   "Reliabilität (siehe attention_failed)",
                                   "attention check: the right answer is {check}; not part of the scale score and the "
                                   "reliability (see attention_failed)", check=stmt['check']))
                values = "; ".join(f"{n} = {_label(l)}" if l else str(n) for n, l in enumerate(labels, start=1))
                if c.get("dont_know"):
                    values += "; " + dont_know
                    notes.append(dont_know_note)
                if stmt and row_logic.partial(stmt):  # a condition, or a statement carried from an earlier question
                    values += "; " + not_shown
                    if stmt.get("when"):
                        shown_if = row_logic.text(item, stmt, lang, ui)
                    else:
                        src = next((i for i in _items(survey) if i.id == stmt["src"].get("item")), None)
                        chosen = stmt["src"].get("which") == "chosen"
                        shown_if = t("in {src} {how} (aus einer früheren Frage übernommene Aussage)",
                                     "{how} in {src} (statement carried from an earlier question)",
                                     src=src.code if src else t('der früheren Frage', 'the earlier question'),
                                     how=t("gewählt", "chosen") if chosen else t("nicht gewählt", "not chosen"))
                    notes.append(shown_only.replace("{cond}", shown_if))
            elif item.type == "semantic":
                pair = next((p for p in kind.pairs(item, lang) if p["export_code"] == col), None)
                if pair:
                    left, right = ((pair["right"], pair["left"]) if pair.get("reverse")  # recoded: poles flip
                                   else (pair["left"], pair["right"]))
                    values = f"1 = {_label(left)}; {c['points']} = {_label(right)}"
                if pair and pair.get("reverse"):
                    notes.append(reversed_note)
                if pair and pair.get("when"):
                    values += "; " + not_shown
                    notes.append(shown_only.replace("{cond}", row_logic.text(item, pair, lang, ui)))
            elif item.type == "rating":
                lo, hi = kind.bounds(item)
                values = t("{lo} bis {hi} ({style})", "{lo} to {hi} ({style})", lo=f"{lo:g}", hi=f"{hi:g}", style=c['style'])
                if c.get("reverse"):
                    notes.append(reversed_note)
                if c.get("dont_know"):
                    values += "; " + dont_know
                    notes.append(dont_know_note)
            elif item.type == "text_input":
                v = c.get("validation", "none")
                typ = {"integer": "Ganzzahl", "decimal": "Zahl", "plz_de": "Postleitzahl"}.get(v, "Text")
                lo, hi = c.get("min"), c.get("max")
                if v in ("integer", "decimal") and (lo is not None or hi is not None):
                    # whole limits without ".0"; a limit left open is not named ("None bis 100.0")
                    lo, hi = (None if x is None else int(x) if float(x).is_integer() else x for x in (lo, hi))
                    values = (t("{lo} bis {hi}", "{lo} to {hi}", lo=lo, hi=hi) if lo is not None and hi is not None
                              else t("ab {lo}", "from {lo}", lo=lo) if hi is None else t("bis {hi}", "up to {hi}", hi=hi))
                if v == "regex":
                    notes.append(t("Format: ", "format: ") + f"{c.get('pattern')}")
            elif item.type == "consent":
                values = t("1 = zugestimmt; 0 = abgelehnt", "1 = agreed; 0 = declined")
            elif item.type == "psm" and "_nms_" in col:
                typ = "Kaufwahrscheinlichkeit"
                w = kind.nms_weights(item)
                values = "; ".join(f"{n} = {_label(lab)}" for n, lab in enumerate(kind.nms_labels(item, lang), start=1))
                notes.append(t("Newton-Miller-Smith (Newton et al., 1993); Auswertung mit ",
                               "Newton-Miller-Smith (Newton et al., 1993); analysed with ")
                             + " / ".join(f"{x:g} %" for x in w))
            elif item.type == "psm":
                typ = "Zahl"
                unit = kind.unit_label(item, lang)
                values = (f"{c.get('min') if c.get('min') is not None else 0:g}" + t(" bis ", " to ")
                          + f"{t('offen', 'open') if c.get('max') is None else format(c['max'], 'g')}" + (f" {unit}" if unit else ""))
                consistency = {"required": ("erzwungen", "enforced"), "warn": ("nur Hinweis, Verletzungen möglich",
                                                                                 "hint only, violations possible"),
                               "off": ("nicht geprüft", "not checked")}[c.get("consistency", "required")]
                notes.append(t("Van-Westendorp-PSM (Van Westendorp, 1976); Konsistenz TC ≤ C ≤ E ≤ TE: ",
                               "Van Westendorp PSM (Van Westendorp, 1976); consistency TC ≤ C ≤ E ≤ TE: ")
                             + t(*consistency) + t("; gefragt ", "; asked ")
                             + ("TE → TC" if c.get("order") == "desc" else "TC → TE"))
                changes = kind.wording_changes(item)
                if changes:
                    notes.append(t("Wortlaut vom Standard abweichend (", "wording differs from the standard (") + "; ".join(changes) + ")")
            if shown_if_item:  # "Nur zeigen, wenn …" (app/item_condition.py): empty as behind a condition of the flow
                notes.append(shown_if_item)
            level = kind.column_level(item, col)
            if level:
                notes.append(t("Messniveau ", "level of measurement ") + levels[level])
            if typ == "Text":
                notes.append(OMV_TEXT_NOTE_EN if en else OMV_TEXT_NOTE)
                notes.append(CSV_GUARD_NOTE_EN if en else CSV_GUARD_NOTE)
            mode = {"required": ("Pflicht", "required"), "nudge": ("Nachfragen", "request"),
                    "optional": ("freiwillig", "optional")}[item.answer_mode]
            mode = t(*mode)
            # a map: the follow-up questions name their own mode, the question's own is about setting markers
            notes.append(t("Marker setzen: {mode}", "setting markers: {mode}", mode=mode) if item.type == "map_marker" else mode)
            construct = constructs.info(item)
            if construct and construct["linked"]:  # app/linked.py: the same question in other surveys
                mods = construct["modifications"]
                notes.append(t("Verknüpftes Element {label}, Fassung {v}", "linked element {label}, revision {v}",
                               label=construct['label'], v=construct['version'] or 1)
                             + (f" ({t('ANGEPASST', 'ADAPTED')}{': ' + '; '.join(mods) if mods else ''})" if mods or construct["unlocked"] else ""))
            elif construct:
                mods = construct["modifications"]
                notes.append((t("VERÄNDERTES Konstrukt ", "MODIFIED construct ") if mods else t("Konstrukt ", "construct "))
                             + f"{construct['label']}: {construct['citation']}"
                             + (f" ({t('Änderungen', 'changes')}: {'; '.join(mods)})" if mods else ""))
            loop_rows = _loop_rows(item)
            if loop_rows:  # one column per loop row, as in columns()
                out += [(f"{col}_{n}", f"{label} [{loops.row_caption(item.block, rid, n)}]", typ, values, "; ".join(notes))
                        for n, rid in loop_rows]
            else:
                out.append((col, label, typ, values, "; ".join(notes)))
        out += coding.codebook_rows(coded.get(item.id, []), t)  # categories of open answers (app/coding.py)
        for n, rid in (_loop_rows(item) if item.id in coded_loops else []):  # per pass, as the answers
            caption = loops.row_caption(item.block, rid, n)
            out += [(f"{c}_{n}", f"{label} [{caption}]", typ, values, note)
                    for c, label, typ, values, note in coding.codebook_rows(coded_loops[item.id], t)]
        for col, label, _, codes in defs.get(item.id, []):
            note = f"{scale_logic.codebook_note(item, codes, ui)}; " + t("Messniveau ", "level of measurement ") + levels['metric']
            loop_rows = _loop_rows(item)
            if loop_rows:
                out += [(f"{col}_{n}", f"{label} [{loops.row_caption(item.block, rid, n)}]", "Skalenwert", "",
                         note.replace("Aussagen ", "Aussagen (je Durchgang) ", 1)) for n, rid in loop_rows]
            else:
                out.append((col, label, "Skalenwert", "", note))
        for module, result in model_columns(item, sources=speed_sources):
            for col, label in module.export_columns(item, result):
                typ, values, notes = module.codebook_info(item, col, result, ui)
                level = levels["nominal"] if typ == "Segment" else levels["metric"]
                out.append((col, label, typ, values, "; ".join(notes + [t("Messniveau ", "level of measurement ") + level])))
    for loop in _loops(survey):
        lang = survey.default_language
        mapping = "; ".join(  # ";" separates the entries (and the value labels of .sps and .sav)
            f"{n} = " + ", ".join(f"{_label(c['name'])}: {_label(loops.value(loop, r, c, lang, lang))}" for c in loop["columns"])
            for n, r in enumerate(loop["rows"], start=1))
        internal = [c["name"] for c in loop["columns"] if c.get("hidden")]
        # the file first: who opens the codebook in Excel finds the table before the long note (decisions.md,
        # "Schleifentabelle auffindbar", 2026-10-10)
        table = (t("Tabelle mit einer Spalte je Tabellenspalte: {file} (Reiter Export)",
                   "table with a column per table column: {file} (tab Export)", file=loop_table_name(loop))
                 + (t(" (nur intern: {names})", " (internal only: {names})", names=", ".join(internal)) if internal else "")
                 + "; ")
        if loop.get("source") == "factors":  # vignettes (app/vignettes.py): the plan instead of the selection
            from app import vignettes

            out.append((f"{loop['code']}_order", "Vignetten in gezeigter Reihenfolge", "Loop", mapping,
                        table + vignettes.codebook_note(survey, loop, ui)
                        + t("; Antworten je Vignette in Spalten mit Suffix _<Vignette>",
                            "; answers per vignette in columns with the suffix _<vignette>")))
            continue
        out.append((f"{loop['code']}_order", "Loop-Zeilen in gezeigter Reihenfolge", "Loop", mapping,
                    table + loops.selection_text(survey, loop, en=en)
                    + (t("; zufällige Reihenfolge", "; random order") if loop["order"] == "random" else "")
                    + (t("; balanciert", "; balanced") if loop["balanced"] and loop["mode"] in ("k", "level") else "")
                    + t("; Antworten je Zeile in Spalten mit Suffix _<Zeile>", "; answers per row in columns with the suffix _<row>")))
    listed = {row[0] for row in out} | {f"{lp['code']}_order" for lp in _loops(survey)}
    # only the order columns are read here: no HB or segment columns needed (V1, performance audit 2026-10-09b)
    for col, label in (columns(survey, with_hb=False) if cols is None else cols)[len(meta(survey)):]:
        if re.search(r"_order(_\d+)?$", col) and col not in listed:
            out.append((col, label, "Reihenfolge", t("Variablennamen, getrennt durch |", "variable names, separated by |"),
                        t("randomisiert", "randomised")))
        elif re.search(r"_unchanged(_\d+)?$", col) and col not in listed:  # UI/UX audit 2026-10-16, 105
            out.append((col, label, "Kennzeichen", t("1 = ja, 0 = geändert", "1 = yes, 0 = changed"),
                        t("1: Die gezeigte Startreihenfolge bzw. Startverteilung wurde ohne Änderung bestätigt (Voreinstellung "
                          "übernommen); leer = keine Antwort",
                          "1: the starting order or split shown was confirmed without a change (default kept); "
                          "empty = no answer")))
    if en:  # the labels of Askurious's own columns (the question texts stay as they are)
        out = [(row[0], label_en(row[0], row[1], row[2]), *row[2:]) for row in out]
    if standard:  # in the reader's language already
        from app import standard_vars

        out += standard_vars.codebook(survey, ui)
    return out


# the codebook's labels of Askurious's own columns in English, by column (the data files keep the German labels of
# columns(): they are variable labels inside SPSS, R and jamovi files)
LABELS_EN = {
    "resp_id": "Running number of the participation",
    "pid": "Participation ID ({{pid}}: survey code and random code), e.g. to link with an external application",
    "source": "real = real participation | test = test run (preview) | dummy = simulated data",
    "qversion": "Version of the questionnaire (rises when a paused or closed survey is edited again)",
    "run": "Run in which the participation began (1 = from the start; a new one begins with “New run” on the page "
                 "Data collection; crowd, map and comparisons counted only the current one)",
    "status": "completed | screenout | quotafull (quota full) | excluded (excluded in the flow) | active (not finished); "
              "declines are not stored",
    "lang": "Language",
    "assignment_network_starts": "Earlier new assignments from the same network in this UTC hour; "
                                 "does not identify the same person or prove fraud (e.g. a shared connection)",
    "started": "Start (local time)",
    "finished": "End (local time): completion, screen-out or quota full; empty while not finished",
    "duration_s": "Completion time in seconds",
    "duration_rel": "Completion time relative to the median of the completed real participations (1 = median)",
    "speeder": "1 = speeder by the chosen rule: completion time below the chosen share of the median or Relative Speed "
               "Index above the chosen limit (Leiner, 2019)",
    "last_active": "Last active (local time): last page sent of a participation that did not end (the moment of breaking "
                   "off); empty for ended ones",
    "lang_switched": "1 = language changed during the participation (lang is the last one chosen), 0 = not changed; "
                     "recorded since October 2026, older participations 0",
    "last_page": "Page the participation was on last (position in the flow of this participation, not the farthest: after "
                 "“Back” the earlier page; tasks with pages of their own as well as quota and end points in the flow "
                 "count singly)",
    "qc_rsi": "Relative Speed Index (Leiner, 2019): median page time ÷ own page time, at most 3 per page, averaged over "
              "the pages seen (1 = typical speed, 2 = twice as fast)",
    "mode": "Mode of data collection: online | paper (paper questionnaire, scanned; without completion time)",
    "relayout": "Restart after a change of the questionnaire: how often this participation began again on the first page "
                "because the questionnaire was changed during the participation (0 = never)",
    "durchgang": "Run of the live poll (name, given when the run was started)",
    "person": "Person ID of the diary survey: all entries of a person have the same one",
    "teil": "Part: entry (entry survey), exit (exit survey) or the key of the short survey",
    "eintrag": "Number of the person's entry in this part (1, 2, …)",
    "tag": "Day of the person's period (1 = first day; empty in the entry survey)",
    "ausloeser": "Trigger: fixed (prompt at a fixed time), random (prompt at a random time) or self (started by the person)",
    "faellig": "Time of the prompt (UTC); empty when the person started the entry",
    "benachrichtigt": "Time the phone showed the prompt (UTC)",
    "verzoegerung_s": "Seconds from the prompt to the start of the entry",
    "ortszeit": "Start of the entry in the person's local time (HH:MM)",
    "wochentag": "Weekday of the start in local time (1 = Monday … 7 = Sunday)",
    "abfrage": "State: beantwortet (answered), zur_pruefung (under review), abgebrochen (broken off) or verpasst (prompt without an entry; CSV file only)",
    "erinnert": "1 = opened after the reminder",
    "nachgereicht": "1 = answered without a connection and sent later (submitted late): start and end are what the phone says",
    "eingang": "Time the server received the entry (UTC)",
    "durchgang_id": "Number of the run of the live poll (unique, also for the same name)",
    "durchgang_ursprung": "Run the participation was given in (before runs were merged)",
    "durchgang_ursprung_id": "Number of the run the participation was given in",
    "weiteres_wort": "Further word of a device (“allow several answers one after the other”): no further person",
    "hauptteilnahme": "Participation ID (pid) of the participation of the same device that the further word belongs to",
    "device": "Device type: mobile | tablet | desktop",
    "os": "Operating system (without version)",
    "browser": "Browser (without version)",
    "viewport": "Window width in classes (px)",
    "orientation": "portrait | landscape",
    "touch": "Touchscreen: 1 = yes, 0 = no",
    "focus_lost": "Window or tab left: how often in all",
    "focus_pages": "Window or tab left: on how many pages sent",
    "pasted": "Pasted into a text field: how often in all",
    "pasted_chars": "Pasted into a text field: characters in all",
    "text_inputs": "Inputs in text fields (key typed, word of the phone keyboard, dictation): how many in all",
    "scheme": "Appearance shown: light | dark | mixed (changed during the participation); empty = not recorded",
}
OWN_TYPES = ("meta", "Paradaten", "Qualität", "Gewicht")  # the types of the columns of LABELS_EN and the like
# the labels of columns named by a pattern: German pattern, English replacement
LABEL_PATTERNS_EN = [
    (re.compile(r"^Zeit auf der Seite ab „(.*?)“ in Sekunden \(Summe aller Besuche\)"),
     r"Time on the page from “\1” in seconds (sum of all visits)"),
    (re.compile(r"^URL-Parameter (\S+) \(gespeichert, pseudonym\)$"), r"URL parameter \1 (stored, pseudonymous)"),
    (re.compile(r"^Quote „(.*)“: Teilquote der Person \(Code\)$"), r"Quota “\1”: sub-quota of the person (code)"),
    (re.compile(r"^Quote „(.*)“: Ergebnis der Quotenberechnung$"), r"Quota “\1”: result of the quota check"),
    (re.compile(r"^Randomisierung „(.*)“$"), r"Randomisation “\1”"),
    (re.compile(r"^Vignetten in gezeigter Reihenfolge$"), "Vignettes in the order shown"),
    (re.compile(r"^Loop-Zeilen in gezeigter Reihenfolge$"), "Loop rows in the order shown"),
    (re.compile(r"^Gezeigte Reihenfolge \(Variablennamen\)"), "Order shown (variable names)"),
    (re.compile(r"^Startreihenfolge unverändert bestätigt \(1 = ja, 0 = geändert\)"), "Starting order confirmed unchanged (1 = yes, 0 = changed)"),
    (re.compile(r"^Startverteilung unverändert bestätigt \(1 = ja, 0 = geändert\)"), "Starting split confirmed unchanged (1 = yes, 0 = changed)"),
    (re.compile(r"^Reihenfolge der Elemente auf Seite (\d+)"), r"Order of the elements on page \1"),
    (re.compile(r"^Bedingung „(.*)“: gezeigte Elemente in Reihenfolge"), r"Condition “\1”: elements shown in order"),
    (re.compile(r"; leer, wo die Person den Zweig nicht erreichte$"), "; empty where the person did not reach the branch"),
    (re.compile(r", Durchgang Zeile (\d+)$"), r", pass row \1"),
    (re.compile(r"\[Durchgang (\d+)"), r"[pass \1"),
]
# the pages of a task named in the timing labels (paradata.timing_pages: ", <page>")
TIMING_PAGES_EN = {"Erklärungsseite": "explanation page", "Aufgabe": "task", "Holdout-Aufgabe": "holdout task",
                   "Holdout-Set": "holdout set", "Ankerfrage": "anchor question", "Wunschvariante": "build your own",
                   "Preisgrenze": "price limit", "Ausschlussgründe": "unacceptables", "Unverzichtbar": "must-haves",
                   "Rangfolge": "ranking"}


def label_en(column, label, typ=None):
    """The codebook label of a column in English: Askurious's own columns (types of OWN_TYPES) by LABELS_EN and the other
    modules' tables, generated ones by LABEL_PATTERNS_EN; the labels of the questions (researchers' text) stay."""
    from app import geoip, pagetime, quality, seal, tempo, weights

    from app import blockload

    fixed = {**LABELS_EN, **geoip.COLUMNS_EN, **seal.COLUMNS_EN, **tempo.COLUMNS_EN, **quality.COLUMNS_EN, **blockload.COLUMNS_EN,
             pagetime.COLUMN[0]: pagetime.COLUMN_EN, pagetime.COLUMN_MORE[0]: pagetime.COLUMN_MORE_EN,
             weights.COLUMN[0]: weights.COLUMN_EN}
    if column in fixed and (typ is None or typ in OWN_TYPES):
        return fixed[column]
    text = str(label)
    for pattern, replacement in LABEL_PATTERNS_EN:
        text = pattern.sub(replacement, text)
    if text.startswith("Time on the page from "):
        text = re.sub(r", (Holdout-Aufgabe|Holdout-Set|Aufgabe|Erklärungsseite|Ankerfrage|Wunschvariante|Preisgrenze|"
                      r"Ausschlussgründe|Unverzichtbar|Rangfolge)\b", lambda m: ", " + TIMING_PAGES_EN[m.group(1)], text)
    return text


# the codebook's column "type" in English (codebook_csv); the German names are keys inside the code
TYPES_EN = {
    "meta": "meta", "Paradaten": "paradata", "Qualität": "quality", "URL-Parameter": "URL parameter", "Quote": "quota",
    "Gewicht": "weight", "Bedingung": "condition", "Loop": "loop", "Reihenfolge": "order", "Skalenwert": "scale score",
    "Ganzzahl": "integer", "Zahl": "number", "Postleitzahl": "postal code", "Text": "text",
    "Kaufwahrscheinlichkeit": "purchase probability", "Skala": "scale", "Auswahl": "choice", "Kategorie": "category",
    "Rang": "rank", "Version des Versuchsplans": "version of the experimental design", "Anker": "anchor", "Kennzeichen": "flag", "weiß nicht": "don't know",
    "Bundesland": "federal state", "Externer Abschnitt": "external section", "Jahr": "year", "Monat und Jahr": "month and year",
    "Datum": "date", "Uhrzeit": "time", "Datum und Uhrzeit": "date and time",
}


def type_text(typ, lang=None):
    """The codebook type of a column in the reader's language (TYPES_EN; element type keys stay)."""
    from app.items.base import reader

    return TYPES_EN.get(typ, typ) if reader(lang) == "en" else typ


SOURCE_NAMES = {"real": "echte Teilnahmen", "test": "Testdurchläufe", "dummy": "Dummy-Daten"}
SOURCE_NAMES_EN = {"real": "real participations", "test": "test runs", "dummy": "dummy data"}
LIVE_NOTES_EN = {"weiteres_wort": "1 = further word of a device in a run, no further person: for the number of "
                                  "participants, missing shares and relations between questions use only rows with 0; "
                                  "for the word cloud all rows count",
                 "durchgang_ursprung": "same as durchgang, unless the run was merged into another one",
                 "hauptteilnahme": "only with weiteres_wort = 1; empty for words from before this column"}


OMV_TEXT_NOTE = "jamovi (.omv) liest höchstens 511 Byte je Text, längere Texte sind dort gekürzt"
OMV_TEXT_NOTE_EN = "jamovi (.omv) reads at most 511 bytes per text, longer texts are cut there"
# safe_text: what the formula guard of the CSV does to a typed text (audit Datenspeicherung + Berechnungen 2026-10-21, C4)
CSV_GUARD_NOTE = ("in der CSV steht vor einem Text, der (auch nach Leerzeichen) mit = + - oder @ beginnt, ein Apostroph "
                  "(Schutz vor Formeln in Excel und LibreOffice); beim Einlesen in R oder Python ggf. entfernen; "
                  "SPSS-, R- und jamovi-Dateien haben ihn nicht")
CSV_GUARD_NOTE_EN = ("in the CSV a text that starts (also after spaces) with = + - or @ has an apostrophe in front (protection "
                     "against formulas in Excel and LibreOffice); remove it when reading into R or Python if needed; "
                     "SPSS, R and jamovi files do not have it")


def spss_notes(book, lang=None):
    """The codebook rows with a note where SPSS (.sav, .sps) names a variable differently (sav.spss_names):
    reserved words such as TO or BY, names over 64 characters, names SPSS takes for the same. lang: the reader's
    language of the note (None: the page's; app.items.base.reader)."""
    from app import sav
    from app.items.base import reader

    en = reader(lang) == "en"
    book = list(book)
    names = sav.spss_names([row[0] for row in book])
    out = []
    for row in book:
        name = names.get(row[0], row[0])
        if name != row[0]:
            if en:
                why = ("SPSS keyword" if row[0].upper() in sav.RESERVED else "SPSS allows at most 64 characters"
                       if len(row[0].encode("utf-8")) > 64 else "SPSS does not tell upper and lower case apart")
                note = f"in SPSS (.sav, .sps) as “{name}” ({why})"
            else:
                why = ("SPSS-Schlüsselwort" if row[0].upper() in sav.RESERVED else "SPSS erlaubt höchstens 64 Zeichen"
                       if len(row[0].encode("utf-8")) > 64 else "SPSS unterscheidet nicht nach Groß- und Kleinschreibung")
                note = f"in SPSS (.sav, .sps) als „{name}“ ({why})"
            row = (*row[:4], f"{row[4]}; {note}" if row[4] else note)
        out.append(row)
    return out


def codebook_csv(survey, separator="comma", speeder=None, speed_real=True, speed_sources=None,
                 public=False, open_answers=False, drop_speeders=False, scales=False, lang=None, standard=False):
    """The codebook as CSV. lang: the language of the person it is written for (None: the page's, German outside a
    request): labels of Askurious's own columns, types, notes and describing values (codebook()); the header row and
    the variable names stay, as do the question texts and the value labels."""
    from app.items.base import reader

    lang = reader(lang)
    buf = io.StringIO()
    buf.write("﻿")
    writer = csv.writer(buf, delimiter=";" if separator == "semicolon" else ",", lineterminator="\r\n")
    writer.writerow(["variable", "label", "type", "values", "notes"])
    from app import vignette_positions

    hidden = vignette_positions.hidden(survey, open_columns(survey)) if public and not open_answers else set()
    kept_back = withheld_columns(survey, scales=scales) if public else {}
    for row in spss_notes(vignette_positions.codebook(survey, codebook(survey, speeder, speed_real, speed_sources,
                                                                      drop_speeders, scales, lang=lang,
                                                                      standard=standard), lang), lang):
        if public and (not is_public_column(row[0]) or row[0] in hidden):
            continue
        if public and row[0] in PUBLIC_TIME_COLUMNS:
            row = (*row[:4], "only the month (YYYY-MM)" if lang == "en" else "nur Monat (JJJJ-MM)")
        if public and row[0] == "resp_id":
            row = (*row[:4], resp_id_note(lang, public=True))
        if public and row[0] == "person":
            row = (*row[:4], PUBLIC_PERSON_NOTE[1] if lang == "en" else PUBLIC_PERSON_NOTE[0])
        if row[0] in kept_back:
            # Value anchors and condition/modification notes can also contain protected wording. The numeric
            # response codes stay useful without redistributing those texts, including loop and score columns.
            row = (row[0], row[0], row[2], kept_back[row[0]], WITHHELD)
        row = (row[0], row[1], type_text(row[2], lang), *row[3:])
        writer.writerow([safe_text(v) if k < 3 else codebook_text(v) for k, v in enumerate(row)])
    return buf.getvalue()


def used_constructs(survey, linked=False):
    """[(Construct, [items])] in questionnaire order: the constructs of the toolbox, or with `linked` the linked
    elements (app/linked.py)."""
    found = {}
    for b in survey.blocks:
        for i in b.items:
            key = ((i.config or {}).get("construct") or {}).get("key")
            if key and constructs.is_linked_key(key) == linked:
                found.setdefault(key, []).append(i)
    out = []
    constructs.prefetch(survey)
    for key, items in found.items():
        c = constructs.find(key)
        if c:
            out.append((c, items))
    return out


WITHHELD = "wording not included: no general publication permission is documented (see constructs.md)"


def withheld(survey):
    """Ids whose recorded provenance forbids public wording, even without a current catalogue record."""
    return {i.id for b in survey.blocks for i in b.items if constructs.wording_withheld(i)}


def withheld_columns(survey, scales=False):
    """Public variable IDs and their numeric values, without any instrument wording or custom score labels."""
    defs = scale_defs(survey) if scales else {}
    out = {}
    for item in _items(survey):
        if not constructs.wording_withheld(item):
            continue
        own = {}
        for col, _ in _plain_columns(item):
            codes = (list(range(1, item.config["points"] + 1)) if item.type in ("likert", "semantic")
                     else list(item.kind.scale_values(item, col) or []))
            codes += [c for c in item.kind.missing_codes(item, col) if c not in codes]
            own[col] = " | ".join(str(c) for c in codes)
        own.update({col: "" for col, *_ in defs.get(item.id, [])})
        if item.kind.order_codes(item):
            own[f"{item.code}_order"] = ""
        if item.kind.kept_start_label:
            own[f"{item.code}_unchanged"] = "0 | 1"
        passes = _loop_rows(item)
        out.update({f"{col}_{n}": values for n, _ in passes for col, values in own.items()} if passes else own)
    if not out:
        return {}
    # Vignette files use positions (_p1, ...) instead of the internal per-vignette suffixes. Apply exactly the
    # same renaming as the codebook rather than guessing a column's owner from its (researcher-editable) prefix.
    from app import vignette_positions

    renamed = vignette_positions.codebook(survey, [(col, col, "", values, WITHHELD) for col, values in out.items()])
    return {col: values for col, _, _, values, note in renamed if note == WITHHELD}


def constructs_md(survey, public=False):
    """All validated constructs of the survey with variables, wording, scoring, and references. public: for a
    deposit (app/zenodo.package), without the wording of constructs in constructs.NOT_SHARED."""
    used = used_constructs(survey)
    lang = survey.default_language
    lines = [f"# Validated instruments: {survey.title}", "",
             "Instruments from the construct toolbox used in this survey.",
             "Reverse-coded items are already recoded in `data.csv`.",
             "Instruments marked as MODIFIED differ from the validated version (listed per item);",
             "their psychometric properties have to be checked before comparing them with other studies.",
             ""]
    if not used:
        lines += ["No instruments from the construct toolbox were used.", ""]
    for c, items in used:
        ref = c.citation + (f" https://doi.org/{c.doi}" if c.doi else "")
        lines += [f"## {c.name}", "", f"- Reference: {ref}"]
        if c.scoring:
            lines.append(f"- Scoring: {c.scoring}")
        if c.notes:
            lines.append(f"- Terms of use: {c.notes}")
        for item in items:
            meta = item.config["construct"]
            mods = constructs.modifications(item, c)
            state = "MODIFIED" if mods else "unchanged"
            lines.append(f"- Item `{item.code}` (toolbox version {meta.get('version', '?')}, {state})")
            if mods:
                lines.append("  - Modifications: " + "; ".join(mods))
            if public and c.key in constructs.NOT_SHARED:
                lines.append("  - Variables: " + ", ".join(f"`{col}`" for col, _ in _plain_columns(item)))
                lines.append("  - Wording not included: no general publication permission is documented. "
                             "See the reference above.")
                continue
            lt = item.t(lang)
            if lt.get("question"):
                lines.append(f"  - Instruction: {lt['question']}")
            if item.type == "likert":
                labels = item.kind.labels(item, lang)  # "nur Endpunkte verbalisiert": the steps in between stay empty
                lines.append("  - Scale: " + "; ".join(f"{n} = {l}" for n, l in enumerate(labels, 1)))
                for n, st in enumerate(item.config.get("statements", [])):
                    code = st.get("code") or f"{item.code}_{n + 1}"
                    rev = " (reverse coded)" if st.get("reverse") else ""
                    if st["id"] in item.kind.checks(item):
                        rev = f" (attention check: the answer to choose is {st['check']}; not part of the scale)"
                    lines.append(f"  - `{code}`: {lt.get('statements', {}).get(st['id'], '')}{rev}")
            elif item.type == "single_choice":
                for o in item.config.get("options", []):
                    lines.append(f"  - `{o['code']}` = {lt.get('options', {}).get(o['id'], '')}")
            elif item.type == "psm":
                unit = item.kind.unit_label(item, lang)
                lines.append(f"  - Unit: {unit or '-'}; consistency check: {item.config.get('consistency', 'required')}")
                lines += [f"  - `{item.code}_{k}`: {lt.get('q_' + k, '')}" for k in item.kind.sequence(item)]
        lines.append("")
    if used:
        lines += ["## References", ""]
        lines += [f"- {c.citation}" + (f" https://doi.org/{c.doi}" if c.doi else "")
                  for c in sorted((c for c, _ in used), key=lambda c: c.citation)]
        lines.append("")
    lines += linked_md(survey)
    return "\n".join(lines)


def linked_md(survey):
    """Lines on the linked elements of the survey (app/linked.py): questions asked the same way in other surveys."""
    from app import linked

    used = used_constructs(survey, linked=True)
    if not used:
        return []
    lines = ["# Linked elements", "",
             "Own questions asked in the same form in several surveys (not validated instruments).",
             "Their answers can be analysed together with those of the other surveys; ADAPTED marks a question changed in this survey.",
             ""]
    for c, items in used:
        others = len({s.id for _, s in linked.uses(c)} - {survey.id})
        for item in items:
            meta = item.config["construct"]
            mods = constructs.modifications(item, c)
            state = "ADAPTED" if mods or meta.get("unlocked") else "unchanged"
            lines.append(f"- `{item.code}`: {c.name} (version {meta.get('version', '?')}, {state}; also used in "
                         f"{others} other {'survey' if others == 1 else 'surveys'})")
            if mods:
                lines.append("  - Modifications: " + "; ".join(mods))
    lines.append("")
    return lines


def slug(title):
    """The title as the start of a file name. The name travels in a header of the answer, and headers hold Latin-1
    only: letters outside it (ł, ş, Cyrillic, Chinese) lose their marks where they have any and go otherwise, or
    the download would end in a server error."""
    import unicodedata

    def latin(ch):
        if not ch.isalnum():
            return "-"
        try:
            ch.encode("latin-1")
            return ch
        except UnicodeEncodeError:
            plain = "".join(c for c in unicodedata.normalize("NFKD", {"ł": "l", "đ": "d", "ı": "i"}.get(ch, ch)) if c.isascii() and c.isalnum())
            return plain or "-"

    return "".join(latin(ch) for ch in (title or "").lower()).strip("-")[:40].strip("-") or "survey"


def filename(survey, kind):
    from app.timeutil import local_today  # the date in Europe/Berlin, not the container's UTC (G-07)
    return f"{slug(survey.title)}-{kind}-{local_today():%Y%m%d}.csv"


def long_csv(survey, item, include_incomplete=False, separator="comma", sources=("real",), drop_speeders=False):
    """Long format (item types with long_export): one row per response and shown alternative for choice
    models, e.g. conjoint: response x task x alternative (+ "none"); one row per marker for a map. drop_speeders: without the
    participations the data file marks as speeders (same median, same threshold)."""
    return _long(survey, item.kind.long_columns(item), lambda r: item.kind.long_rows(item, r), include_incomplete,
                 separator, sources, drop_speeders)


def vignettes_long_csv(survey, node, include_incomplete=False, separator="comma", sources=("real",), drop_speeders=False,
                       open_answers=True):
    """Long format of a vignette loop (app/vignettes.py): one row per person and vignette shown. open_answers=False
    (a public package without the tick): without the columns of open answers, as data.csv leaves them out."""
    from app import vignettes

    hidden = set()
    if not open_answers:
        for it in vignettes._answer_items(survey, node):
            hidden.update(open_item_columns(it))
    return _long(survey, vignettes.long_columns(survey, node), lambda r: vignettes.long_rows(survey, node, r),
                 include_incomplete, separator, sources, drop_speeders, hidden)


def loop_long_nodes(survey):
    """The loops with a long format of their own: every loop with a table except vignette loops (those have theirs,
    vignettes_long_csv), in flow order."""
    from app import vignettes

    return [n for n in loop_tables(survey) if not vignettes.is_vignettes(n)]


def _loop_answer_items(survey, node):
    from app import loops

    return [it for b in loops.blocks_in(survey, node) for it in b.items if it.kind.collects_answer]


def loop_long_columns(survey, node):
    """position (of the pass for this person), zeile (number of the table row, as the suffix _n of the data file),
    every column of the table in the default language, then the answer columns of the loop's questions without the
    suffix. A table column named like another column gets _2, _3 …"""
    cols = ["position", "zeile"]
    answers = [c for it in _loop_answer_items(survey, node) for c, _ in it.kind.export_columns(it)]
    taken = {"resp_id", "source", *cols, *answers}
    for c in node.get("columns") or []:
        name, k = c["name"], 2
        while name in taken:
            name = f"{c['name']}_{k}"
            k += 1
        taken.add(name)
        cols.append(name)
    return cols + answers


def loop_long_rows(survey, node, response):
    """One row per pass the person went through, in the order shown (`<code>_order` of the data file): position,
    row number, the row's values, the answers given in that pass. A row taken out of the table since keeps its
    answers, with "?" as row number and empty values."""
    from app import loops

    loop = loops.config_node(node)
    columns = loop["columns"]
    default = survey.default_language
    by_id = {r["id"]: r for r in loop["rows"]}
    shown = ((response.layout or {}).get("loops") or {}).get(node["id"]) or []
    items = _loop_answer_items(survey, node)
    answers = response.answers or {}
    for pos, rid in enumerate(shown, start=1):
        row = by_id.get(rid)
        line = [pos, loops.row_number(loop, rid) or "?"]
        line += [loops.value(loop, row, c, default, default) if row else "" for c in columns]
        for it in items:
            answer = answers.get(f"{it.id}#{rid}")
            values = {**it.kind.export_values(it, answer), **it.kind.export_response_values(it, answer, response)}
            line += [values.get(c) for c, _ in it.kind.export_columns(it)]
        yield line


def loop_long_csv(survey, node, include_incomplete=False, separator="comma", sources=("real",), drop_speeders=False,
                  open_answers=True):
    """Long format of a loop (not a vignette loop): one row per person and pass. open_answers=False (a public
    package without the tick): without the columns of open answers, as data.csv leaves them out."""
    hidden = set()
    if not open_answers:
        for it in _loop_answer_items(survey, node):
            hidden.update(open_item_columns(it))
    return _long(survey, loop_long_columns(survey, node), lambda r: loop_long_rows(survey, node, r),
                 include_incomplete, separator, sources, drop_speeders, hidden)


def loop_table_name(loop):
    """The file name of a loop's table in the packages (Zenodo, pre-registration) and in the codebook's note."""
    return f"{loop['code']}_table.csv"


def loop_table_rows(survey, loop, languages=True):
    """The table of a loop node as [header, row, ...]: one row per pass,
    "zeile" first (the number of the suffix _n and of <code>_order), then every column in table order, the
    internal ones too, in the default language. A current plan adds "im_plan" (0/1) and "deck" (D-optimal subset,
    app/loop_plan.py), a vignette loop "deck"; every further language adds the visible columns as <name>_<lang>,
    empty where nothing is translated (decisions.md, "Schleifentabelle als eigene Datei", 2026-10-08).
    languages=False: without the translations (the copy button of the Export tab)."""
    from app import loop_plan, loops, vignettes

    default = survey.default_language
    columns = loop.get("columns") or []
    rows = loop.get("rows") or []
    others = [lg for lg in survey.languages if lg != default] if languages else []
    shown = [c for c in columns if not c.get("hidden")]
    decks = []
    plan = vignettes.is_vignettes(loop)
    if plan:
        decks = (loop.get("plan") or {}).get("decks_rows") or [] if vignettes.plan_current(survey, loop) else []
    elif loop.get("mode") == "plan" and loop_plan.current(survey, loop):
        decks = loop_plan.settings(loop).get("decks_rows") or []
    deck_of = {rid: n for n, deck in enumerate(decks, start=1) for rid in deck}
    head = ["zeile"] + [c["name"] for c in columns]
    if decks and not plan:
        head.append("im_plan")
    if decks:
        head.append("deck")
    head += [f"{c['name']}_{lg}" for lg in others for c in shown]
    seen = set()  # a column named like one of these (zeile, deck, kurz_en): the later one gets _2, _3 …
    for i, name in enumerate(head):
        n, unique = 1, name
        while unique in seen:
            n += 1
            unique = f"{name}_{n}"
        head[i] = unique
        seen.add(unique)
    out = [head]
    for n, row in enumerate(rows, start=1):
        line = [n] + [loops.value(loop, row, c, default, default) for c in columns]
        if decks and not plan:
            line.append(1 if row["id"] in deck_of else 0)
        if decks:
            line.append(deck_of.get(row["id"], ""))
        line += [(row.get("values") or {}).get(c["id"], {}).get(lg, "") for lg in others for c in shown]
        out.append(line)
    return out


def loop_table_csv(survey, loop, separator="comma"):
    """loop_table_rows() as CSV in the writing of the data and the codebook (UTF-8 with BOM, CRLF)."""
    buf = io.StringIO()
    buf.write("﻿")
    writer = csv.writer(buf, delimiter=";" if separator == "semicolon" else ",", lineterminator="\r\n")
    for row in loop_table_rows(survey, loop):
        writer.writerow([safe_text(v) for v in row])
    return buf.getvalue()


def loop_table_tsv(survey, loop):
    """loop_table_rows() without the translations as tab separated text, as Excel puts cells on the clipboard: pastes
    into Excel and Word as a table whatever the separator of the CSV files (decisions.md, "Schleifentabelle
    auffindbar", 2026-10-10)."""
    def cell(v):
        v = str(v)
        return '"' + v.replace('"', '""') + '"' if any(ch in v for ch in '\t\n\r"') else v

    return "".join("\t".join(cell(safe_text(v)) for v in row) + "\r\n"
                   for row in loop_table_rows(survey, loop, languages=False))


def loop_tables(survey):
    """The loop nodes with a table to export, in flow order."""
    from app import loops

    return [n for n in loops.nodes(survey) if n.get("columns") and n.get("rows")]


def long_pool(survey, include_incomplete=False, sources=("real",), drop_speeders=False, *, responses=None):
    """[(resp_id, response)] of the long formats: resp_id counts the whole pool, so it stays the same with and
    without the speeders."""
    from app import quality

    pool = responses if responses is not None else _responses(survey, include_incomplete, sources)
    speed = Speed(survey, sources, responses=pool) if drop_speeders else None
    patterns = quality.context(survey, _items(survey), pool) if drop_speeders else None
    out = []
    for n, r in enumerate(pool, start=1):
        if drop_speeders and (speed.speeder(r)
                              or quality.request_values(survey, r, patterns)["qc_flags"] >= patterns["rules"]["min"]):
            continue  # marked by quality control, as qc_marked in the data file
        out.append((n, r))
    return out


def _long(survey, columns, rows_of, include_incomplete, separator, sources, drop_speeders, hidden=()):
    semicolon = separator == "semicolon"
    buf = io.StringIO()
    buf.write("\ufeff")
    writer = csv.writer(buf, delimiter=";" if semicolon else ",", lineterminator="\r\n")
    columns = list(columns)
    keep = [i for i, c in enumerate(columns) if c not in hidden]  # hidden: columns a public file leaves out
    writer.writerow(["resp_id", "source"] + [columns[i] for i in keep])
    for n, r in long_pool(survey, include_incomplete, sources, drop_speeders):
        for row in rows_of(r):
            row = list(row)
            writer.writerow([n, r.source] + [_cell(row[i], semicolon) for i in keep])
    return buf.getvalue()


def questionnaire_md(survey, public=False):
    """The questionnaire as Markdown (default language): flow, loops, pages, items with codes. public: for a deposit
    (app/zenodo.package), without the wording of constructs in constructs.NOT_SHARED (withheld())."""
    from app import flow, loops

    kept_back = withheld(survey) if public else set()
    lang = survey.default_language
    badges = flow.badges(survey, "en")  # the Markdown is written in English
    out = [f"# {survey.title}", "", f"Language: {lang}. Variable names in `code` font.", "",
           f"Look of the pages (questionnaire version {survey.qversion or 1}): {_look(survey)}.", ""]
    for n, block in enumerate(survey.blocks, start=survey.block_start):  # the consent block is block 0
        out += [f"## Block {n}: {block.title() or 'untitled'}", ""]
        for b in badges.get(block.id, []):
            out.append(f"*Flow:* {b}  ")
        if block.randomize_items:
            out.append("*Items shown in random order within each page.*  ")
        loop = loops.config(block)
        first_in_loop = loops.active(block) and loops.blocks_in(survey, loops.node_of(block))[0].id == block.id
        if loops.active(block) and not first_in_loop:
            out += ["", f"*Part of the loop* `{loop['code']}` (table above).  "]
        if first_in_loop:
            out += ["", f"**Loop** `{loop['code']}` over "
                    + ", ".join(b.title() or "untitled" for b in loops.blocks_in(survey, loops.node_of(block))) + ": "
                    + loops.selection_text(survey, loop, en=True)
                    + (", random order" if loop["order"] == "random" else ""), "",
                    "| # | " + " | ".join(c["name"] for c in loop["columns"]) + " |",
                    "|---|" + "---|" * len(loop["columns"])]
            for r_no, row in enumerate(loop["rows"], start=1):
                out.append(f"| {r_no} | " + " | ".join(loops.value(loop, row, c, lang, lang) for c in loop["columns"]) + " |")
        out.append("")
        for p_no, page in enumerate(block.pages(), start=1):
            out += [f"### Page {p_no}", ""]
            for item in page:
                kind, t = item.kind, item.t(lang)
                head = f"- `{item.code}` ({kind.label}, {item.answer_mode}{', drop-down list' if kind.dropdown(item) else ', search field' if kind.search(item) else ''})"
                if item.id in kept_back:
                    out.append(f"{head}: {WITHHELD}")
                    out += [f"  - `{col}`" for col, _ in _plain_columns(item)]
                    continue
                question = (t.get("question") or "").replace("\n", " ")
                out.append(head + (f": {question}" if question else ""))
                if item_condition.of(item):  # as the conditions of the flow above ("*Flow:*")
                    out.append(f"  - *Shown only if:* {item_condition.text(item, lang, 'en')}")
                if item.type in ("single_choice", "multiple_choice"):
                    out += [f"  - `{o['code']}` {o['label']}"
                            + (f" (with a text field: `{kind.text_column(item, o)}`)" if o.get("text") else "")
                            for o in kind.options(item, lang)]
                elif item.type == "likert":
                    labels = kind.labels(item, lang)
                    out.append(f"  - Scale 1–{item.config['points']}: " + " / ".join(l for l in labels if l))
                    out += [f"  - `{s['export_code']}` {s['text']}" + (" (reverse coded)" if s.get("reverse") else "")
                            + (f" (shown only if {row_logic.text(item, s, lang, 'en')})" if s.get("when") else "")
                            for s in kind.statements(item, lang)]
                    if item.config.get("dont_know"):
                        out.append(f"  - Plus \"don't know\" (exported as {DONT_KNOW_CODE}, counts as missing)")
                elif item.type == "semantic":
                    out.append(f"  - Scale 1–{item.config['points']}")
                    out += [f"  - `{p['export_code']}` {p['left']} – {p['right']}" + (" (reverse coded)" if p.get("reverse") else "")
                            + (f" (shown only if {row_logic.text(item, p, lang, 'en')})" if p.get("when") else "")
                            for p in kind.pairs(item, lang)]
                elif item.type == "rating":
                    lo, hi = kind.bounds(item)
                    out.append(f"  - {item.config['style']}, {lo:g}–{hi:g}")
                    if item.config.get("dont_know"):
                        out.append(f"  - Plus \"don't know\" (exported as {DONT_KNOW_CODE}, counts as missing)")
                elif item.type == "text_input" and item.config.get("validation", "none") != "none":
                    out.append(f"  - Validation: {item.config['validation']}")
                elif kind.questionnaire_lines(item, lang):
                    out += kind.questionnaire_lines(item, lang)
                elif item.type == "psm":
                    if t.get("product"):
                        out += ["", "  > " + t["product"].replace("\n", "\n  > "), ""]
                    out += [f"  - `{item.code}_{k}` {t.get('q_' + k, '')}" for k in kind.sequence(item)]
                    if kind.nms(item):
                        out += [f"  - `{item.code}_nms_{k}` {t.get('nms_' + k, '')} (1–5: "
                                + " / ".join(kind.nms_labels(item, lang)) + ")" for k in ("c", "e")]
                    out.append(f"  - Unit: {kind.unit_label(item, lang) or '-'}, consistency: {item.config.get('consistency', 'required')}")
                elif item.type == "consent":
                    out += ["", "  > " + (t.get("body") or "").replace("\n", "\n  > ")]
                    pool = kind.pool_notice(item, lang)
                    if pool:
                        out += ["  >", "  > " + pool.replace("\n", "\n  > ")]
            out.append("")
    return "\n".join(out) + "\n"


def _look(survey, responses=None):
    """The theme of the survey pages in words (app/theme.py); with the counts of the shown look if it followed
    the device."""
    look = theme.of(survey)
    shown = None
    if theme.is_auto(look) and responses is not None:
        shown = {}
        for r in responses:
            key = (r.paradata or {}).get("_scheme")
            if key:
                shown[key] = shown.get(key, 0) + 1
    return theme.describe(look, "en", shown)


def _identifiers_sentence(survey, open_answers):
    """What of a person the platform stored, from the columns there are (audit 2026-10-10, E-6): no IP address ever;
    identifiers from the link only where the survey stores URL parameters; a personal code where it asks one."""
    from app import panel

    out = "no IP addresses were stored."
    stored = panel.stored_names(survey)
    if stored:
        out += (" Identifiers from the link were stored with the participations on the platform (URL parameter"
                + ("s " if len(stored) > 1 else " ") + ", ".join(f"`{n}`" for n in stored)
                + ", e.g. ids of a panel; pseudonymous); they are not in this dataset.")
    else:
        out += " No identifiers from the link were stored."
    codes = [i.code for b in survey.blocks for i in b.items if i.type == "personal_code"]
    if codes:
        out += (" A self-generated personal code to link waves was asked (" + ", ".join(f"`{c}`" for c in codes) + "); "
                + ("it is included." if open_answers else "it is not included."))
    return out


def readme_md(survey, open_answers=False, include_incomplete=False):
    """include_incomplete: data.csv and the long formats hold the unfinished participations too (Coscine)."""
    from app import pagetime, paradata, tempo, vignettes
    from app.timeutil import fmt

    # the two small columns it needs, not every participation with all its answers (audit 2026-10-06, P11)
    completed = [SimpleNamespace(params=params, paradata=data) for params, data in db.session.execute(
        db.select(Response.params, Response.paradata).filter(
            Response.survey_id == survey.id, Response.source == "real", Response.status == Response.COMPLETED))]
    # a live vote: a further word of a device is a row, not a person (T-19)
    extra_words = sum(1 for r in completed if (r.params or {}).get("live_extra")) if survey.live is not None else 0
    completed = [r for r in completed if not (survey.live is not None and (r.params or {}).get("live_extra"))]
    lines = [
        f"# Data and materials: {survey.title}", "",
        (survey.description or "").strip(), "",
        "## Files", "",
        ("- `data.csv`: one row per participation, unfinished ones included (column `status`; UTF-8, comma separated). "
         if include_incomplete else "- `data.csv`: one row per completed participation (UTF-8, comma separated). ")
        + "Reverse-coded items are already recoded; randomized orders are in `*_order` columns. A typed text that "
        "starts with =, +, - or @ (also after spaces) has an apostrophe in front, as protection against formulas in "
        "spreadsheets; remove it when reading the file into R or Python if needed.",
        "- `codebook.csv`: variables, labels, value labels, and notes.",
        "- `questionnaire.md`: the questionnaire with flow, randomization, and loops.",
        "- `survey.json`: the questionnaire with all its settings as a survey file of Askurious (format "
        "`askurious-survey`); it can be read in again as a new survey. Images are not in it (see `images.zip`); the "
        "wording of instruments without a general publication permission is left out, as in `questionnaire.md`.",
        "- `survey-material.pdf`: the questionnaire as participants saw it (part 1) and with notes beside each element "
        "on display logic, conditions, randomisation, required answers and checks (part 2).",
    ]
    for note in dict.fromkeys(i.kind.long_export_note for b in survey.blocks for i in b.items
                              if i.kind.long_export and (open_answers or not i.kind.long_private)):
        lines.append(note)
    from app import coding

    coded = bool(coding.guide_md(survey, examples=False))  # zenodo.package adds kodierleitfaden.md then
    if coded:
        lines.append("- `kodierleitfaden.md`: the coding guide of the open answers: per coded answer column its "
                     "categories with definitions and how far it is coded" + ("" if open_answers else
                     " (without anchor examples, which may quote answers)") + ".")
    if loop_tables(survey):
        lines.append("- `*_table.csv`: the table of a loop, one row per pass: `zeile` (the number in the suffix `_n` of "
                     "the answers and in `<loop>_order`), then every column of the table in the default language, "
                     "translations as `<column>_<language>`; with a design also `deck`.")
    if vignettes.nodes(survey):
        lines.append("- `vignettes_*_long.csv`: long format of a vignette loop (participant × vignette shown): deck, "
                     "position, the level of every factor, then the answers to the questions of the loop.")
    if loop_long_nodes(survey):
        lines.append("- `loop_*_long.csv`: long format of a loop (participant × pass): `position` (order of the "
                     "pass), `zeile` (the table row, as in the suffix `_n` of `data.csv`), the values of that row, then "
                     "the answers given in that pass.")
    if include_incomplete and (lines[-1].startswith(("- `vignettes_", "- `loop_")) or any(
            i.kind.long_export and (open_answers or not i.kind.long_private) for b in survey.blocks for i in b.items)):
        lines.append("- The long formats hold the same participations as `data.csv`; `resp_id` joins them.")
    from app.models import Asset

    if any(a.status == Asset.APPROVED for b in survey.blocks for i in b.items if i.type != "media" for a in i.assets):
        lines.append("- `images.zip`: the images shown in the questionnaire (`image_<variable>_<n>.*`) and `images.csv`, "
                     "which names each image's variable, variant, source (upload or AI) and, for generated images, "
                     "model, style and prompt. Its rights_* columns record image-specific attribution, terms and "
                     "any researcher's declaration allowing public archiving. These declarations are not a new licence "
                     "grant or legal verification. The data-set licence does not replace the stated image terms.")
    if extra_words:
        lines.append(f"- Live vote with several answers per device: {extra_words} rows of `data.csv` are further words "
                     "of a device (`weiteres_wort` = 1), not further people; count people, missing shares and "
                     "relations between questions on the rows with `weiteres_wort` = 0.")
    lines += ["", "## Collection", "",
              f"- Completed participations: {len(completed)}",
              f"- Field period: {fmt(survey.opened_at, False) or '–'} to {fmt(survey.closed_at, False) or 'ongoing'}",
              f"- Declined consents (not stored): {survey.declined_count or 0}",
              "- Identifiers: " + _identifiers_sentence(survey, open_answers)
              + " This dataset leaves out participation ids and URL parameters; start and end are given as month only."
              + (" Coarse technical information was recorded (" + ", ".join(c for c, _ in paradata.COLUMNS)
                 + ") but is not included here." if survey.paradata else "")
              + (" Free-text answers, exact map positions, postal codes and exact dates are not included." if not open_answers
                 and open_columns(survey) else "")
              + (" The categories the researchers assigned to free-text answers are included (columns `<column>_k<n>`, "
                 "one 0/1 column per category, or `<column>_kat`, one category per answer; see `kodierleitfaden.md`)."
                 if coded else "")
              + (" The time spent on each page was recorded (columns t_…, seconds)."
                 if getattr(survey, "page_timing", False) else "")
              + (" It was counted how often a participant left the window or tab and how often text was pasted into "
                 "a text field (columns focus_lost, focus_pages, pasted; counts only)."
                 if getattr(survey, "focus_paste", False) else "")
              + (" Also counted were the characters pasted and the input steps in text fields (columns pasted_chars, "
                 "text_inputs; counts only)." if getattr(survey, "focus_paste", False) and getattr(survey, "focus_inputs", False) else "")
              + (" The country and, within Germany, the federal state were estimated once from the IP address with a "
                 "table on the server (columns geo_country, geo_state; the address itself was never stored; the state "
                 "is an estimate)." if getattr(survey, "geo_ip", False) else "")
              + (f" Pages sent faster than {tempo.MS_PER_WORD} ms per word of their question texts were shown once "
                 f"more with a note asking to check the answers ({'once per participation' if tempo.dose(survey) == 'once' else 'every time'}; "
                 "Conrad et al., 2017; columns tempo_…)." if tempo.active(survey) else "")
              + (" The pages of some blocks had a minimum viewing time before “Next” worked, or a time limit after "
                 "which the page went on automatically with the answers given by then (see the codebook, column time_up)."
                 if pagetime.any_time(survey) else ""),
              "- Look of the survey pages: " + _look(survey, completed) + ".",
              ""]
    consent = survey.consent_item
    if consent is not None and consent.config.get("ethics"):
        ref = consent.config.get("ethics_ref")
        lines[-1:-1] = ["- Ethics: approved by an ethics committee" + (f" ({ref})" if ref else "") + "."]
    used = {}
    for b in survey.blocks:
        for i in b.items:
            c = constructs.info(i)
            if c:
                # a construct without a source (the standard question on age) by its name (audit 2026-10-10, E-7)
                cite = (c["citation"] or "").strip() or f"{c['label']} (no published source)."
                used.setdefault(cite, []).append(f"`{i.code}`" + (" (modified)" if c["modifications"] else ""))
    if used:
        lines += ["## Instruments", "", "Details, wording, and references: `constructs.md`. The wording of these "
                  "instruments is not covered by the licence of this deposit: it stays with its authors and publishers "
                  "and follows the terms of use given in `constructs.md`.", ""]
        lines += [f"- {cite} Variables: {', '.join(codes)}" for cite, codes in used.items()] + [""]
    methods = {}
    for b in survey.blocks:
        for i in b.items:
            for cite in i.kind.references(i):
                methods.setdefault(cite, []).append(f"`{i.code}`")
    if methods:
        lines += ["## Methods", ""]
        lines += [f"- {cite} Variables: {', '.join(codes)}" for cite, codes in methods.items()] + [""]
    lines += ["## Authors", ""]
    lines += [f"- {m.display_name}" + (f", {m.affiliation}" if m.affiliation else "")
              + (f" (https://orcid.org/{m.orcid_id})" if m.orcid_id else "") for m in survey.members]
    return "\n".join(lines) + "\n"


# --- SPSS: the data as CSV plus a syntax file that reads it and sets labels --------------------

_SPS_BREAKS = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")
_SPS_LINE_BREAKS = re.compile(r"\r\n|[\r\n\v\f\x85\u2028\u2029]")  # what can end a case in the CSV for SPSS


def _sps_quote(text):
    """A label as an SPSS string: control characters and line breaks (also \\r of a text field's \\r\\n, and tabs)
    become spaces, the text is cut to 250 characters and only then are its quotes doubled, so the cut can never
    split a doubled quote and leave the string open."""
    return "'" + _SPS_BREAKS.sub(" ", str(text or ""))[:250].replace("'", "''") + "'"


def _spss_dictionary(survey, include_incomplete, sources, speeder, speed_real, speed_sources, drop_speeders=False,
                     scales=False, standard=False):
    """Columns, rows and per column: type ("int" | "num" | "str"), width, value labels, measurement level.
    Types come from the data: a column is numeric if every value is a number."""
    from app import vignette_positions  # vignette loops by position (concept V8)

    base = columns(survey, scales=scales, sources=sources, standard=standard)
    cols = vignette_positions.columns(survey, base)
    data = vignette_positions.rows(survey, rows(survey, include_incomplete, sources, speeder, speed_real, speed_sources,
                                                drop_speeders=drop_speeders, scales=scales, standard=standard))
    book = {row[0]: row for row in vignette_positions.codebook(  # German: its types and values are read as keys here
        survey, codebook(survey, speeder, speed_real, speed_sources, drop_speeders, scales, cols=base, lang="de",
                         standard=standard), "de")}

    def number(v):
        if v in (None, "") or isinstance(v, bool):
            return True
        try:
            float(str(v))
            return True
        except ValueError:
            return False

    def integer(v):
        return v in (None, "") or isinstance(v, bool) or float(str(v)).is_integer()

    def places(v):
        """Decimals a number is written with (at most MAX_DECIMALS): 84.5 has 1, 0.1234 has 4."""
        text = str(v)
        if "e" in text.lower():
            return MAX_DECIMALS
        return min(len(text.partition(".")[2].rstrip("0")), MAX_DECIMALS)

    value_re = re.compile(r"(-?\d+(?:\.\d+)?)\s*=\s*([^;]+)")
    code_re = re.compile(r"\s*([A-Za-z0-9_]+)\s*=\s*(.+?)\s*")  # "w = weiblich": an answer code of letters

    def text_pairs(values, given):
        """[(code, label)] of a codebook list "w = weiblich; m = männlich" (labels hold no ";", see _label) for a
        text column of answer codes: every entry is such a pair and every value in the data one of the codes."""
        found = [code_re.fullmatch(part) for part in values.split(";")] if values else []
        if not found or not all(found):
            return []
        pairs = [(f.group(1), f.group(2)) for f in found]
        return pairs if {str(v) for v in given} <= {c for c, _ in pairs} else []
    spss_levels = {"nominal": "NOMINAL", "ordinal": "ORDINAL", "metric": "SCALE"}
    item_levels = {}  # measurement level per question column (ItemType.column_level), loop passes included
    item_missing = {}  # codes that count as missing per question column (ItemType.missing_codes)
    item_dates = {}  # columns with dates or times as ISO text (ItemType.date_kind)
    for item in _items(survey):
        passes = [n for n, _ in (_loop_rows(item) or [])]
        for c, _ in item.kind.export_columns(item):
            level = item.kind.column_level(item, c)
            gone = item.kind.missing_codes(item, c)
            for col in ([f"{c}_{n}" for n in passes] or [c]):
                item_levels[col] = spss_levels.get(level)
                item_missing[col] = gone
                item_dates[col] = item.kind.date_kind(item, c)
    if standard:  # the KonsortSWD variables: their missing codes −1 … −6 (SPSS takes the first three)
        from app import standard_vars

        for c in standard_vars.NAMES:
            item_missing[c] = standard_vars.MISSING_CODES
            item_levels[c] = "SCALE" if c in standard_vars.NUMBERS else "NOMINAL"
    meta = {}
    for c, _ in cols:
        values = [r.get(c) for r in data]
        kind = ("int" if all(integer(v) for v in values) else "num") if all(number(v) for v in values) else "str"
        entry = book.get(c)
        free = bool((entry and entry[2] in ("Postleitzahl", "Text")) or c in ("pid", "hauptteilnahme") or c.startswith("url_"))
        # answer codes with a leading zero (AGS "07337047", KldB): text, so the key keeps its zero (audit 2026-10-10, N-10)
        zero = bool(entry and entry[3] and re.search(r"(?:^|;)\s*0\d+\s*=", entry[3]))
        if free or item_dates.get(c) or zero:
            # text by the kind of the question, not by what happens to be in the column: codes and ids of digits
            # keep their leading zeros and long digit strings ("01067", a panel id), and the type of a free-text
            # column does not change with the answers given so far
            kind = "str"
        pairs = [(v, lab.strip()) for v, lab in value_re.findall(entry[3])] if entry and entry[3] else []
        typ = (entry[2] if entry else "") or ""
        # answer codes of letters ("w", "m", "d"): value labels on the text column, as far as a format has them
        coded = (text_pairs(entry[3], [v for v in values if v not in (None, "")])
                 if kind == "str" and entry and not free and not item_dates.get(c) else [])
        if kind == "str":
            level = "NOMINAL"
        elif item_levels.get(c):
            level = item_levels[c]
        elif typ in ("likert", "semantic", "rating", "Skala", "Kaufwahrscheinlichkeit") and pairs:
            level = "ORDINAL"
        elif pairs or typ in ("single_choice", "multiple_choice", "Bedingung", "consent"):
            level = "NOMINAL"
        else:
            level = "SCALE"
        # SPSS takes up to three discrete missing values, or a range and one more (spss_missing)
        every = [float(m) for m in item_missing.get(c, ()) if kind != "str" and number(m)]
        gone, span = spss_missing(every, [float(v) for v in values if v not in (None, "") and number(v)]
                                            + [float(v) for v, _ in pairs])
        # answer codes of letters that count as missing ("k" = keine Angabe): declared on the text column too, in
        # SPSS at most three of up to 8 bytes (audit Datenspeicherung + Berechnungen 2026-10-21, C1)
        texts = ([str(m) for m in item_missing.get(c, ()) if str(m) != ""]
                 if kind == "str" and not item_dates.get(c) and not free else [])
        # numbers: as many decimals as the data have (prices and computed values up to four), as wide as the
        # longest of them (SPSS display format Fw.d; F8.2 showed 84.5678 as 84.57 and could not hold 100000.00)
        given = [v for v in values if v not in (None, "") and not isinstance(v, bool)] if kind != "str" else []
        dec = 0 if kind != "num" else max([places(v) for v in given] + [0])
        width = max([len(f"{float(str(v)):.{dec}f}") for v in given] + [8, dec + 2])
        meta[c] = {"kind": kind, "level": level, "pairs": pairs if kind != "str" else [], "missing": gone, "free": free,
                   "text_pairs": coded,
                   "decimals": dec, "num_width": min(width, 40),
                   "missing_all": every,  # for files without SPSS's limit (R)
                   "missing_range": span,  # (low, high) of spss_missing, low None = LOWEST
                   "missing_text": [m for m in texts if len(m.encode("utf-8")) <= 8][:3],  # SPSS
                   "missing_text_all": texts,  # R, jamovi
                   "date": item_dates.get(c),
                   "width": max([len(str(v)) for v in values if v not in (None, "")] + [8]),
                   "bytes": max([len(str(v).encode("utf-8")) for v in values if v not in (None, "")] + [1])}
    return cols, data, meta


def spss_missing(codes, values):
    """(discrete, range) of the missing codes of a number column as SPSS can declare them: up to three discrete
    values, or a range (low, high) plus at most one discrete value. More than three codes that are all negative
    (the KonsortSWD codes -1 ... -6 of the x_ variables, -77, -98) become a range, as long as no other value in
    `values` (data and value labels) lies inside it; low is None (LOWEST) when no such value lies below the range.
    Otherwise SPSS gets the first three, and the .sps and the codebook say so (audit Datenspeicherung + Berechnungen
    2026-10-21, C2)."""
    if len(codes) <= 3:
        return list(codes), None
    gone = set(codes)
    others = [v for v in values if v not in gone]
    if all(m < 0 for m in codes):
        def clean(low, high):
            return not any(low <= v <= high for v in others)

        low, high = min(codes), max(codes)
        if clean(low, high):
            return [], (None if not any(v < low for v in others) else low, high)
        for one in codes:  # a range over all but one code, and that one discrete
            rest = [m for m in codes if m != one]
            low, high = min(rest), max(rest)
            if clean(low, high):
                return [one], (None if not any(v < low for v in others) else low, high)
    return list(codes[:3]), None


def spss_short(codes):
    """True if SPSS cannot declare all these missing codes of a question (spss_missing): more than three that are not
    all negative numbers, or codes of letters over 8 bytes or more than three of them."""
    def num(v):
        try:
            float(v)
            return True
        except (TypeError, ValueError):
            return False

    codes = [str(c) for c in codes]
    if all(num(c) for c in codes):
        return len(codes) > 3 and not all(float(c) < 0 for c in codes)
    return len(codes) > 3 or any(len(c.encode("utf-8")) > 8 for c in codes)


def spss_missing_line(name, m):
    """The MISSING VALUES line of a column of the .sps (meta of _spss_dictionary), or None."""
    parts = []
    if m.get("missing_range"):
        low, high = m["missing_range"]
        parts.append(f"{'LOWEST' if low is None else format(low, 'g')} THRU {high:g}")
    parts += [f"{v:g}" for v in m["missing"]]
    if m["kind"] == "str":
        parts = [_sps_quote(v) for v in m.get("missing_text") or []]
    return f"MISSING VALUES {name} ({', '.join(parts)})." if parts else None


def spss_zip(survey, include_incomplete=False, sources=("real",), speeder=None, speed_real=True,
             speed_sources=None, drop_speeders=False, scales=False, standard=False):
    """A ZIP with <name>.dat and <name>.sps (SPSS syntax: GET DATA, variable and value labels,
    measurement levels). Variable names as in the .sav file (sav.spss_names: reserved words and names over 64
    bytes renamed, listed at the top of the syntax and in the codebook). GET DATA reads one case per line
    (/DELCASE=LINE), so line breaks in typed texts become spaces in this file; the data export keeps them.
    The data file holds the typed answers as they are, for SPSS (an apostrophe before "=…" would be part of the
    value there), so it is called .dat, not .csv: a double click does not open it in Excel or LibreOffice, which
    read "=HYPERLINK(…)" as a formula (audit 2026-10-18, INJ-2). The comma-separated content is unchanged."""
    import zipfile

    from app import sav

    cols, data, meta = _spss_dictionary(survey, include_incomplete, sources, speeder, speed_real, speed_sources, drop_speeders,
                                        scales, standard)
    base = filename(survey, "spss")[:-4]
    spss = sav.spss_names([c for c, _ in cols])

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=",", lineterminator="\r\n")
    writer.writerow([spss[c] for c, _ in cols])
    for row in data:
        # read by SPSS, not a spreadsheet (hence .dat): no guard against formulas, one line per case
        writer.writerow([_SPS_LINE_BREAKS.sub(" ", v) if isinstance(v, str) else v
                         for v in (_cell(row.get(c), False, safe=False) for c, _ in cols)])

    lines = ["* SPSS syntax for the " + brand.name() + " export (encoding UTF-8).",
             "* Put this file next to the .dat file and run it (Run > All); adjust the path if needed.",
             # the .dat carries typed answers as they are (no guard against formulas): said where the researcher reads it
             "* Die .dat-Datei (durch Kommas getrennt) ist für SPSS gedacht: Excel oder LibreOffice lesen getippte Antworten, die mit = + - oder @ beginnen, als Formeln.",
             "* Für eine Tabellenkalkulation bitte den Datenexport (CSV) verwenden.",
             "* Zeilenumbrüche in getippten Texten stehen in dieser Datei als Leerzeichen (SPSS liest je Zeile einen Fall);"
             " vollständig im Datenexport (CSV) und in der .sav-Datei."]
    renamed = [(c, spss[c]) for c, _ in cols if spss[c] != c]
    if renamed:
        lines += ["* Für SPSS umbenannt (Schlüsselwort, über 64 Zeichen oder doppelt; siehe Codebuch):"]
        lines += [f"*   {c} -> {name}." for c, name in renamed]
    lines += ["SET DECIMAL=DOT UNICODE=ON.", "",
             "GET DATA /TYPE=TXT", f"  /FILE='{base}.dat'", "  /ENCODING='UTF8'", "  /DELCASE=LINE",
             "  /DELIMITERS=\",\"", "  /QUALIFIER='\"'", "  /ARRANGEMENT=DELIMITED", "  /FIRSTCASE=2", "  /VARIABLES="]
    # "1987-05-23" and "14:30" are read as a date and a time (SPSS then computes with them); a month and a date
    # with a time stay text, SPSS has no input format for "1987-05" and "1987-05-23T14:30"
    date_fmt = {"date": "SDATE10", "time": "TIME5"}
    for n, (c, _) in enumerate(cols):
        m = meta[c]
        if m["date"] in date_fmt:
            m["level"] = "SCALE"
        # string widths count bytes (umlauts take two), so the longest answer is never cut
        number_fmt = f"F{m['num_width']}.{m['decimals']}" if m["kind"] != "str" else None
        lines.append(f"    {spss[c]} " + (date_fmt.get(m["date"]) or number_fmt or f"A{min(max(m['bytes'], 8), 32767)}")
                     + ("." if n == len(cols) - 1 else ""))
    lines += ["", "VARIABLE LABELS"]
    labels = [f"  {spss[c]} {_sps_quote(label)}" for c, label in cols if label]
    lines += [(l + ("." if n == len(labels) - 1 else "")) for n, l in enumerate(labels)] or ["  resp_id 'resp_id'."]
    value_blocks = [f"  /{spss[c]} " + " ".join(f"{v} {_sps_quote(lab)}" for v, lab in meta[c]["pairs"])
                    for c, _ in cols if meta[c]["pairs"]]
    # answer codes of letters: the values quoted, as SPSS wants them for a string variable
    value_blocks += [f"  /{spss[c]} " + " ".join(f"{_sps_quote(v)} {_sps_quote(lab)}" for v, lab in meta[c]["text_pairs"])
                     for c, _ in cols if meta[c]["text_pairs"]]
    levels = {"NOMINAL": [], "ORDINAL": [], "SCALE": []}
    for c, _ in cols:
        levels[meta[c]["level"]].append(spss[c])
    if value_blocks:
        value_blocks[-1] += "."
        lines += ["", "VALUE LABELS"] + value_blocks
    for level, names in levels.items():
        if names:
            lines += ["", f"VARIABLE LEVEL {' '.join(names)} ({level})."]
    gone = [line for line in (spss_missing_line(spss[c], meta[c]) for c, _ in cols) if line]
    if gone:
        lines += [""] + gone
    # more missing codes than SPSS can declare (C2): said where the researcher reads the syntax
    short = [spss[c] for c, _ in cols if len(meta[c]["missing_all"]) > len(meta[c]["missing"]) and not meta[c]["missing_range"]]
    short += [spss[c] for c, _ in cols if len(meta[c]["missing_text_all"]) > len(meta[c]["missing_text"])]
    if short:
        lines += ["* Mehr Fehlend-Codes, als SPSS deklarieren kann (höchstens drei; siehe Codebuch): " + ", ".join(short) + "."]
    lines += ["", "EXECUTE.", ""]

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{base}.dat", "\ufeff" + buf.getvalue())
        z.writestr(f"{base}.sps", "\ufeff" + "\n".join(lines))
    return out.getvalue()


def spss_sav(survey, include_incomplete=False, sources=("real",), speeder=None, speed_real=True,
             speed_sources=None, drop_speeders=False, scales=False, standard=False):
    """The data as an SPSS system file (.sav) with variable labels, value labels and measurement levels.
    Variable names follow the export codes (made valid for SPSS where needed, sav.spss_names); texts are kept up to
    32767 bytes (very long strings), longer ones are cut with a note in the label, JSON lists over that (marker
    positions, drawn lines) are left empty instead: cut JSON cannot be read."""
    cols, data, meta = _spss_dictionary(survey, include_incomplete, sources, speeder, speed_real, speed_sources, drop_speeders,
                                        scales, standard)
    return sav_file(cols, data, meta, survey.title)


def sav_file(cols, data, meta, file_label=""):
    """The .sav file of spss_sav from columns, rows and their dictionary (_spss_dictionary); also for the merged waves
    (app/waves.py). Changes `data` where SPSS needs other values (dates, texts too long)."""
    from app import sav

    spss = sav.spss_names([c for c, _ in cols])

    def too_long(v):
        return isinstance(v, str) and len(v.encode("utf-8")) > sav.MAX_LONG

    variables = []
    for c, label in cols:
        m = meta[c]
        if m["kind"] == "str" and any(too_long(r.get(c)) for r in data):
            json_cut = False
            for r in data:
                if too_long(r.get(c)) and r[c][:1] in ("[", "{"):
                    r[c], json_cut = None, True
            label = (f"{label or c} [leer, wo länger als {sav.MAX_LONG} Byte: vollständig in der CSV]" if json_cut
                     else f"{label or c} [Texte über {sav.MAX_LONG} Byte gekürzt: vollständig in der CSV]")
        var = {"name": spss[c], "label": label or "", "measure": m["level"].lower(),
               "kind": "str" if m["kind"] == "str" else "num", "decimals": m["decimals"], "print_width": m["num_width"]}
        if m["date"]:  # a date variable: a number with a date format, so SPSS sorts and computes with it
            for r in data:
                r[c] = sav.date_number(m["date"], r.get(c))
            var.update(kind="num", decimals=0, measure="scale", date=m["date"])
        elif m["kind"] == "str":
            codes = [len(v.encode("utf-8")) for v, _ in m["text_pairs"]]
            var["width"] = min(max([m["bytes"], *codes]), sav.MAX_LONG)
            var["value_labels"] = dict(m["text_pairs"])
            var["missing"] = m.get("missing_text") or []  # answer codes of letters (C1)
        else:
            var["value_labels"] = {float(v): lab for v, lab in m["pairs"]}
            var["missing"] = m["missing"]
            var["missing_range"] = m.get("missing_range")  # more than three negative codes (C2)
        variables.append(var)
    return sav.write(variables, [[r.get(c) for c, _ in cols] for r in data], file_label=file_label)


def r_rds(survey, include_incomplete=False, sources=("real",), speeder=None, speed_real=True,
          speed_sources=None, drop_speeders=False, scales=False, standard=False):
    """The data as an R file (.rds, read with readRDS()): a data frame whose columns carry their description
    (attribute "label") and, for coded answers, their value labels in the form R packages for SPSS files expect (class
    haven_labelled). Types and value labels are those of the SPSS export. Codes that count as missing are NA, the usual
    reading of declared missing values in R (the CSV keeps them); ISO dates become Date, texts stay whole (R has no
    limit on their length)."""
    cols, data, meta = _spss_dictionary(survey, include_incomplete, sources, speeder, speed_real, speed_sources, drop_speeders,
                                        scales, standard)
    return rds_file(cols, data, meta)


def rds_file(cols, data, meta):
    """The .rds file of r_rds from columns, rows and their dictionary (_spss_dictionary); also for the merged waves
    (app/waves.py). Codes that count as missing become None in `data`."""
    from app import rds

    variables = []
    for c, label in cols:
        m = meta[c]
        var = {"name": c, "label": label or "", "kind": "date" if m["date"] == "date" else m["kind"]}
        if m["kind"] == "str" and m["text_pairs"] and not m["date"]:
            # a labelled character column (haven_labelled); codes that count as missing are NA, as for numbers (C1)
            var["value_labels"] = {v: lab for v, lab in m["text_pairs"] if v not in m["missing_text_all"]}
        if m["kind"] == "str" and m["missing_text_all"]:
            for r in data:
                if r.get(c) in m["missing_text_all"]:
                    r[c] = None
        if m["kind"] != "str":
            # every code that counts as missing (also "not shown", -77), not only the three SPSS can declare
            var["value_labels"] = {float(v): lab for v, lab in m["pairs"] if float(v) not in m["missing_all"]}
            if m["missing_all"]:
                for r in data:
                    if r.get(c) not in (None, "") and float(r[c]) in m["missing_all"]:
                        r[c] = None
        variables.append(var)
    return rds.write(variables, [[r.get(c) for c, _ in cols] for r in data])


MAX_DECIMALS = 4  # the most decimals a number column of the SPSS export is shown with (prices, computed values)


OMV_FACTOR_LEVELS = 30  # a text column with at most this many distinct values becomes a nominal factor in jamovi


def jamovi_omv(survey, include_incomplete=False, sources=("real",), speeder=None, speed_real=True,
               speed_sources=None, drop_speeders=False, scales=False, standard=False):
    """The data as a jamovi file (.omv) with descriptions, levels (value labels), measurement levels and
    missing values. Types and levels are those of the SPSS export; answer codes of letters become a nominal text
    column whose levels show the labels. Free texts, ids and text columns with many distinct values become ID
    columns. jamovi reads at most 511 bytes of a text: longer texts are cut with a note in the description, JSON
    lists over that (marker positions, drawn lines) are left empty instead."""
    from app import omv

    cols, data, meta = _spss_dictionary(survey, include_incomplete, sources, speeder, speed_real, speed_sources, drop_speeders,
                                        scales, standard)

    def too_long_json(v):
        return isinstance(v, str) and v[:1] in ("[", "{") and len(v.encode("utf-8")) > omv.MAX_STRING

    variables = []
    for c, label in cols:
        m = meta[c]
        var = {"name": c, "label": label or "", "measure": {"SCALE": "continuous"}.get(m["level"], m["level"].lower())}
        if m["kind"] == "str":
            if any(too_long_json(r.get(c)) for r in data):
                for r in data:
                    if too_long_json(r.get(c)):
                        r[c] = None
                var["label"] = f"{label or c} [leer, wo länger als {omv.MAX_STRING} Byte: vollständig in der CSV]"
            if any(isinstance(r.get(c), str) and len(r[c].encode("utf-8")) > omv.MAX_STRING for r in data):
                var["label"] = f"{var['label'] or c} [Texte über {omv.MAX_STRING} Byte gekürzt (Grenze von jamovi): vollständig in der CSV]"
            distinct = {r.get(c) for r in data if r.get(c) not in (None, "")}
            coded = bool(m["text_pairs"]) and len(m["text_pairs"]) <= OMV_FACTOR_LEVELS
            var["kind"] = ("text" if coded else "id" if m["free"] or c in PUBLIC_TIME_COLUMNS
                           or len(distinct) > OMV_FACTOR_LEVELS else "text")
            if coded:
                var["value_labels"] = dict(m["text_pairs"])
            var["missing"] = m["missing_text_all"]  # answer codes of letters that count as missing (C1)
        else:
            var["kind"] = m["kind"]
            var["value_labels"] = {int(float(v)): lab for v, lab in m["pairs"] if float(v).is_integer()}
            var["missing"] = m["missing_all"]  # jamovi has no limit of three missing values, unlike SPSS
        variables.append(var)
    return omv.write(variables, [[r.get(c) for c, _ in cols] for r in data])


# Only file-format consumers need flat rows; analysis consumers retain their associated Response objects.
from app.export_tables import file_format as _file_format

to_csv = _file_format(to_csv)
csv_file = _file_format(csv_file)
_spss_dictionary = _file_format(_spss_dictionary)
