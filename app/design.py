"""Pre-launch check: everything that must be fixed before a survey can start.

M5 extends this with the randomization planner (asset space, coverage per row).
"""
import re
from dataclasses import dataclass

from app import item_condition
from app.lang import de_en
from app.units import fixed

ERROR, WARNING = "error", "warning"


@dataclass
class Finding:
    severity: str
    message: str
    item_id: int | None = None
    block_id: int | None = None
    # a one-click fix beside the message: (button text, endpoint of the editor, form fields), e.g. a level (V17)
    fix: tuple | None = None
    # a page the message leads to when it names no element or block: an endpoint taking survey_id, e.g. the quota
    # page for answers no cell fits (quotas.Gap); it goes before the element where the fix is on that page (the
    # research plan, the settings of the consent: UI/UX audit 2026-10-07, W9, W17)
    link: str | None = None
    anchor: str | None = None  # the place on that page ("h2", "einwilligung")
    # a warning in the draft that is an error at the start: shown like an error, so nobody is surprised (UI audit
    # 2026-10-12, U9)
    blocks_start: bool = False


def named(item):
    """How a finding names an element: its kind, the start of its question and the variable name, e.g.
    Conjoint (CBC) „Welche der folgenden …“ (Q5); the variable name alone says little to whoever reads the check."""
    from app.items.base import plain

    text = " ".join(plain(item.text("question", item.survey.default_language) or "").split())
    if len(text) > 40:
        text = text[:38].rstrip() + " …"
    if text:
        return de_en("{kind} „{text}“ ({code})", "{kind} “{text}” ({code})", kind=item.kind.label, text=text, code=item.code)
    return f"{item.kind.label} ({item.code})"


def _lang_name(lang):
    """A language as the check names it: "Deutsch (du)", not "DE-DU" (Glossar: language names)."""
    from app.surveys import LANGUAGE_NAMES

    return LANGUAGE_NAMES.get(lang, lang)


# types whose question can stay empty: another text of theirs carries what is asked (the gap text, the passage,
# the four price questions, the instructions of a reaction task, the questions of the map)
OWN_TEXTS = {"cloze", "highlight", "psm", "iat", "assign", "discounting", "map_marker", "association"}
# types with rows (statements, pairs): an empty question is fine while every row has a text
ROWS = {"likert": ("statements", "statements", None), "semantic": ("pairs", "pairs", ("left", "right")),
        "slider": ("rows", "rows", None)}


def empty_texts(item):
    """What a participant would see without a text (UI/UX audit 2026-10-05, V11): [problem] for an element that
    collects an answer and has no question in the default language, or a row of a scale without its text."""
    from app.items.base import plain

    if not item.kind.collects_answer or item.type in ("consent", "page_break") or item.type in OWN_TEXTS:
        return []
    if "question" not in item.kind.default_texts(item.survey.default_language):
        return []
    lang = item.survey.default_language
    question = plain(item.text("question", lang) or "").strip()
    out = []
    if item.type in ROWS:
        key, text_key, parts = ROWS[item.type]
        rows = (item.config or {}).get(key) or []
        texts = item.text(text_key, lang) or {}

        def filled(r):
            t = texts.get(r.get("id"))
            if parts:
                return isinstance(t, dict) and all(plain(t.get(p) or "").strip() for p in parts)
            return bool(plain(t or "").strip()) if isinstance(t, str) else False

        empty = [r for r in rows if not r.get("src") and "check" not in r and not filled(r)]
        # several rows without a text are refused by the type itself (validate); one row needs the question
        if empty and not question:
            out.append(de_en("Fragetext und Text der Aussage fehlen; Teilnehmende sähen eine leere Zeile.",
                             "Question text and statement text are missing; participants would see an empty row."))
        return out
    if not question:
        out.append(de_en("Fragetext fehlt; Teilnehmende sähen nur die Antwortmöglichkeiten.",
                         "Question text is missing; participants would see only the answer options."))
    return out


# questions that take a phone screen or more each (V14): from three of them on one page the check suggests a break
HEAVY = {"ranking", "constant_sum", "map_marker", "image_mark", "highlight", "position2d", "card_sort", "q_sort", "maxdiff", "paired", "conjoint", "acbc", "cva", "aca"}
HEAVY_ROWS = {"likert": "statements", "semantic": "pairs", "slider": "rows"}
HEAVY_PER_PAGE = 3


def is_heavy(item):
    if item.type in HEAVY:
        return True
    key = HEAVY_ROWS.get(item.type)
    return bool(key) and len((item.config or {}).get(key) or []) >= 2


MISSING_LABEL = re.compile(r"^\s*(keine angabe|k\.\s*a\.|weiß nicht|weiss nicht|kann ich nicht sagen|möchte ich nicht "
                           r"sagen|keine antwort|don'?t know|prefer not to (say|answer)|no answer)\s*\.?\s*$", re.I)
NUMBER = re.compile(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:,\d+)?")  # 1.500 or 2,5 or 20 (_number_steps)
SCALE_WORD = re.compile(r"\b(nie|selten|manchmal|oft|häufig|immer|sehr|eher|ziemlich|etwas|kaum|gar nicht|überhaupt "
                        r"nicht|völlig|teils|teilweise|mittel|mittelmäßig|wenig|bestimmt|wahrscheinlich|never|rarely|"
                        r"sometimes|often|always|very|rather|somewhat|fairly|slightly|not at all)\b", re.I)


def looks_ordered(item, options=None, label=None):
    """The shown answers of a choice measured as nominal whose wording has an order (ja / eher ja / eher nein / nein),
    or []: the pre-launch check offers "ordinal" for them, the results page names it under the list (UI/UX audit
    2026-10-07, W66)."""
    from app.items.base import plain

    if not getattr(item.kind, "supports_missing", False) or item.kind.level(item) != "nominal"             or "ordinal" not in item.kind.level_choices(item):
        return []
    if options is None:
        options = [o for o in (item.config or {}).get("options") or [] if not o.get("src")]
        texts = item.text("options", item.survey.default_language) or {}
        label = {o["id"]: plain(texts.get(o["id"], "") or "").strip() for o in options if o.get("id")}
    shown = [label[o["id"]] for o in options if not o.get("missing") and label.get(o.get("id"))]
    if len(shown) >= 3 and (any(t.lower().startswith("eher ") for t in shown)
                            or sum(1 for t in shown if SCALE_WORD.search(t)) >= max(3, len(shown) - 1)
                            or _number_steps(shown)):
        return shown
    return []


def _number_steps(shown):
    """Answers that are ranges of numbers in order ("unter 5 GB", "5 bis 20 GB", "20 bis 50 GB", "mehr als 50 GB"):
    every answer has a number, and the first numbers rise (or fall) step by step (UI audit 2026-10-12, U22)."""
    firsts = []
    for t in shown:
        m = NUMBER.search(t)
        if not m:
            return False
        firsts.append(float(m.group().replace(".", "").replace(",", ".")))
    rising = all(a <= b for a, b in zip(firsts, firsts[1:]))
    falling = all(a >= b for a, b in zip(firsts, firsts[1:]))
    return (rising or falling) and len(set(firsts)) >= len(firsts) - 1


def option_findings(item):
    """Hints on the answer options of a choice (UI/UX audit 2026-10-05, V16 and V17): an option like "keine Angabe"
    that is not marked "fehlend", and ordered answers (ja / eher ja / eher nein / nein) measured as nominal, which
    the results then sort by frequency; the latter with a one-click fix."""
    from app.items.base import plain

    if not getattr(item.kind, "supports_missing", False):
        return []
    options = [o for o in (item.config or {}).get("options") or [] if not o.get("src")]
    texts = item.text("options", item.survey.default_language) or {}
    label = {o["id"]: plain(texts.get(o["id"], "") or "").strip() for o in options if o.get("id")}
    out = []
    unmarked = [label[o["id"]] for o in options if not o.get("missing") and MISSING_LABEL.match(label.get(o.get("id"), ""))]
    if unmarked:
        out.append(Finding(WARNING, de_en("{named}: „{option}“ ist nicht als „fehlend“ markiert und zählt so in "
                                          "Anteile, Median und Gruppenvergleiche mit. Im Element bei dieser Option „fehlend“ "
                                          "ankreuzen.",
                                          "{named}: “{option}” is not marked as “missing” and so counts in shares, median and "
                                          "group comparisons. Tick “missing” for this option in the element.",
                                          named=named(item), option=unmarked[0]), item_id=item.id, block_id=item.block_id))
    shown = looks_ordered(item, options, label)
    if shown:
        out.append(Finding(WARNING, de_en("{named}: Die Antworten haben eine Reihenfolge ({order}), das Messniveau ist "
                                          "aber „nominal“; die Ergebnisse sortieren sie dann nach Häufigkeit.",
                                          "{named}: the answers have an order ({order}), but the level of measurement is "
                                          "“nominal”; the results then sort them by frequency.",
                                          named=named(item), order=" / ".join(shown[:5])),
                           item_id=item.id, block_id=item.block_id,
                           fix=(de_en("Auf „ordinal“ stellen", "Set to “ordinal”"), "editor.set_level",
                                {"item_id": item.id, "level": "ordinal"})))
    return out


# a sentence that says how long taking part takes ("Die Befragung dauert etwa acht Minuten", "The survey takes about 5
# minutes"): a number or a number word, maybe a range, before "Minuten"; the sentence speaks of the survey and of its
# length, so "Fahren Sie mehr als 30 Minuten?" is not one (UI audit 2026-10-12, U43)
NUMBER_WORDS = {"eine": 1, "einer": 1, "ein": 1, "zwei": 2, "drei": 3, "vier": 4, "fünf": 5, "sechs": 6, "sieben": 7,
                "acht": 8, "neun": 9, "zehn": 10, "elf": 11, "zwölf": 12, "fünfzehn": 15, "zwanzig": 20, "dreißig": 30,
                "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
                "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30}
_NUM = r"\b(\d+|" + "|".join(sorted(NUMBER_WORDS, key=len, reverse=True)) + r")"
_MINUTES = re.compile(_NUM + r"(?:\s*(?:-|–|bis|to|or|oder)\s*" + _NUM + r")?\s*(?:minuten\b|minutes\b|mins?\b|min\.)",
                      re.I)
_LENGTH_WORDS = ("dauer", "in anspruch", "beansprucht", "brauchen sie", "benötigen sie", "takes", "take ", "will last",
                 "duration", "zeit")
_SURVEY_WORDS = ("befragung", "umfrage", "teilnahme", "fragebogen", "studie", "ausfüllen", "survey", "questionnaire",
                 "study", "participation", "taking part", "complete")


def _number(word):
    return int(word) if word.isdigit() else NUMBER_WORDS.get(word.lower())


def stated_minutes(text):
    """The durations a text states for taking part, as (low, high) in minutes: [(8, 8)] for "Die Befragung dauert
    etwa acht Minuten.", [(10, 15)] for "... 10 bis 15 Minuten ...". Only sentences that speak of the survey and its
    length count."""
    from app.items.base import plain

    found = []
    # a sentence ends before a capital letter, so "ca. 5 Min." stays one
    for sentence in re.split(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ])|\n+", plain(text or "")):
        low = sentence.lower()
        if not (any(w in low for w in _LENGTH_WORDS) and any(w in low for w in _SURVEY_WORDS)):
            continue
        for m in _MINUTES.finditer(sentence):
            a, b = _number(m.group(1)), _number(m.group(2)) if m.group(2) else None
            if a is not None:
                found.append((min(a, b or a), max(a, b or a)))
    return found


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def duration_findings(survey, consent, all_items):
    """Durations named in texts (a welcome text, the description) that contradict the one of the consent, or the
    estimate when the consent names none: participants read two lengths in one survey (UI audit 2026-10-12, U43)."""
    from app import duration

    stated = (consent.config or {}).get("duration") if consent is not None else None
    if not stated and not any(i.kind.collects_answer for i in all_items):
        return []
    reference = stated or duration.minutes(duration.estimate(survey)["typical"])
    if not reference:
        return []
    lang = survey.default_language

    def off(low, high):
        slack = max(1, 0.25 * reference)
        return reference < low - slack or reference > high + slack

    def said(low, high):
        return str(low) if low == high else f"{low}–{high}"

    out = []
    texts = [(de_en("Beschreibung der Befragung", "Description of the survey"), survey.description, None)]
    texts += [(named(i), (i.texts or {}).get(lang), i) for i in all_items]
    for name, value, item in texts:
        wrong = [(lo, hi) for text in _strings(value) for lo, hi in stated_minutes(text) if off(lo, hi)]
        if not wrong:
            continue
        message = (de_en("{named}: Nennt {found} Minuten, die Einwilligung etwa {ref} Minuten. Bitte eine der Angaben "
                         "anpassen, damit Teilnehmende nicht zwei Dauern lesen.",
                         "{named}: states {found} minutes, the consent about {ref} minutes. Please adjust one of them, so "
                         "participants do not read two durations.", named=name, found=said(*wrong[0]), ref=reference)
                   if stated else
                   de_en("{named}: Nennt {found} Minuten, geschätzt sind etwa {ref} Minuten. Bitte die Angabe prüfen.",
                         "{named}: states {found} minutes, the estimate is about {ref} minutes. Please check it.",
                         named=name, found=said(*wrong[0]), ref=reference))
        out.append(Finding(WARNING, message, item_id=item.id if item is not None else None,
                           block_id=item.block_id if item is not None else None,
                           link=None if item is not None else "surveys.detail"))
    return out


def title_findings(survey):
    """Blocks that show their name as the heading of their pages next to blocks that do not: some pages of a survey
    have a heading, others none, which reads as uneven (UI audit 2026-10-12, U41). Blocks with only the consent
    count on neither side. A hint: hidden names may be on purpose (experimental conditions)."""
    if survey.live is not None or survey.interview is not None:
        return []
    shown, hidden = [], []
    for block in survey.blocks:
        if not block.title() or not any(i.type not in ("consent", "page_break") for i in block.items):
            continue
        (shown if block.show_title else hidden).append(block)
    if not shown or not hidden:
        return []
    names = ", ".join(de_en("„{t}“", "“{t}”", t=b.title()) for b in hidden[:4]) + (" …" if len(hidden) > 4 else "")
    return [Finding(WARNING, de_en(
        "Überschriften: {n} von {m} Blöcken zeigen ihren Namen als Überschrift, diese nicht: {names}. Teilnehmende sehen "
        "dann mal eine Überschrift, mal keine. Häkchen „Namen auch Teilnehmenden zeigen“ möglichst einheitlich setzen "
        "(Versuchsbedingungen bleiben besser ohne).",
        "Headings: {n} of {m} blocks show their name as a heading, these do not: {names}. Participants then see a heading "
        "on some pages and none on others. Set the tick “Show the name to participants too” as evenly as you can "
        "(experimental conditions are better left without).",
        n=len(shown), m=len(shown) + len(hidden), names=names), block_id=hidden[0].id)]


def check(survey, start=False):
    """The findings of the pre-launch check. start: checked for "Befragung starten" (lifecycle.start and the start box
    of the page Datenerhebung); a few things only a start must not do without are errors then and warnings in the
    draft, so the draft stays editable and testable (dummy data) without them."""
    findings = []
    all_items = [i for b in survey.blocks for i in b.items]
    if not [i for i in all_items if i.type not in ("consent", "page_break")]:
        findings.append(Finding(ERROR, de_en("Der Fragebogen enthält außer der Einwilligung noch keine Elemente.",
                                             "The questionnaire has no elements yet apart from the consent.")))
    words = len((survey.description or "").split())
    if words < 15:
        why = de_en(": Sie erscheint in der Einwilligung für Teilnehmende („Worum geht es?“) und dient der "
                    "KI-Unterstützung als Kontext. Zwei bis vier Sätze in den Einstellungen unter „Befragung“.",
                    ": it appears in the consent for participants (“What is it about?”) and gives the AI support its "
                    "context. Two to four sentences in the settings under “Survey”.")
        findings.append(Finding(WARNING, (de_en("Beschreibung der Befragung fehlt", "The description of the survey is missing")
                                          if not words else
                                          de_en("Beschreibung der Befragung ist sehr kurz ({n} Wörter)",
                                                "The description of the survey is very short ({n} words)", n=words)) + why))
    if survey.sampling is not None:  # a diary study: a short survey without questions ends every entry at once (M9)
        from app import diary

        for m in diary.modules(survey):
            if not any(i.type != "page_break" for b in survey.blocks if diary.part_of(b) == m.get("key") for i in b.items):
                findings.append(Finding(ERROR, de_en(
                    "Kurzbefragung „{name}“ hat noch keine Fragen: Legen Sie im Reiter „Fragebogen“ einen Block mit Fragen "
                    "in diesen Teil oder entfernen Sie die Kurzbefragung im Reiter „Zeitplan“. Nach dem Start ist der "
                    "Aufbau fest.",
                    "Short survey “{name}” has no questions yet: put a block with questions into this part on the tab "
                    "“Questionnaire”, or remove the short survey on the tab “Schedule”. After the start the structure is "
                    "fixed.", name=diary.text_of(m.get("name"), survey.default_language, survey) or m.get("key"))))
    columns = {}
    for block in survey.blocks:
        if not block.pages():
            findings.append(Finding(WARNING, de_en("Block „{title}“ ist leer.", "Block “{title}” is empty.",
                                                   title=block.title() or de_en("ohne Titel", "untitled")), block_id=block.id))
        for n, page in enumerate(block.pages(), 1):
            heavy = [i for i in page if is_heavy(i)]
            if len(heavy) >= HEAVY_PER_PAGE:  # UI/UX audit 2026-10-05, V14
                findings.append(Finding(WARNING, de_en(
                    "Block „{title}“, Seite {page}: {n} aufwendige Fragen (Matrix, Rangfolge, Punkteverteilung, Karte o. Ä.) "
                    "auf einer Seite; am Handy wird sie sehr lang. Seitenumbruch erwägen.",
                    "Block “{title}”, page {page}: {n} demanding questions (matrix, ranking, constant sum, map or similar) "
                    "on one page; on a phone it gets very long. Consider a page break.",
                    title=block.title() or de_en("ohne Titel", "untitled"), page=n, n=len(heavy)),
                    item_id=heavy[0].id, block_id=block.id))
        for item in block.items:
            in_settings = item.kind.settings_problems() if item.type == "consent" else ()
            no_email = item.type == "consent" and not (item.config or {}).get("contact_email")
            for problem in item.kind.validate(item):
                if no_email and in_settings and problem == in_settings[0]:
                    continue  # said together with the missing e-mail below (W9)
                # the person and the e-mail of the consent are set in the settings, not in the element (W9)
                where = {"link": "surveys.detail", "anchor": "einwilligung"} if problem in in_settings else {}
                findings.append(Finding(ERROR, f"{named(item)}: {problem}", item_id=item.id, block_id=block.id, **where))
            for problem in empty_texts(item):
                findings.append(Finding(ERROR, f"{named(item)}: {problem}", item_id=item.id, block_id=block.id))
            # "Nur zeigen, wenn …" on a question that is not before it (or gone): it would never be read right
            for problem in item_condition.problems(item):
                findings.append(Finding(ERROR, f"{named(item)}: {problem}", item_id=item.id, block_id=block.id))
            findings.extend(option_findings(item))
            for hint in item.kind.check_warnings(item):
                findings.append(Finding(WARNING, f"{named(item)}: {hint}", item_id=item.id, block_id=block.id))
            for col, _ in item.kind.export_columns(item):
                columns.setdefault(col, []).append(item)
            if item.kind.order_codes(item):  # the shown order (export.columns)
                columns.setdefault(f"{item.code}_order", []).append(item)
    for item in all_items:
        if (item.config or {}).get("ai") and not item.kind.ai_scale:  # drafted texts of another element (AInstein)
            findings.append(Finding(WARNING, de_en("{named}: Texte von einer KI entworfen: bitte prüfen und anpassen.",
                                                   "{named}: texts drafted by an AI: please check and adapt them.",
                                                   named=named(item)), item_id=item.id, block_id=item.block_id))
        elif (item.config or {}).get("ai"):
            findings.append(Finding(WARNING, de_en("{named}: KI-generierte Skala, nicht validiert: Items prüfen und die "
                                                   "Reliabilität im Pretest untersuchen.",
                                                   "{named}: AI-generated scale, not validated: check the items and examine "
                                                   "the reliability in the pretest.", named=named(item)),
                                    item_id=item.id, block_id=item.block_id))
    from flask import current_app

    from app import translations

    for severity, message in (translations.findings(survey) if current_app.config.get("TRANSLATION_CHECK", True) else []):
        findings.append(Finding(ERROR if severity == "error" else WARNING, message))
    consent = survey.consent_item
    if consent is None:
        findings.append(Finding(WARNING, de_en("Keine Einwilligung: nur sinnvoll, wenn sie vorher eingeholt wurde (z. B. in "
                                               "einem vorgeschalteten Fragebogen). Einfügen über „Elemente → Einwilligung“.",
                                               "No consent: only sensible if it was obtained beforehand (e.g. in a preceding "
                                               "questionnaire). Insert it via “Elements → Consent”.")))
    for extra in [i for i in all_items if i.type == "consent" and i is not consent]:
        findings.append(Finding(ERROR, de_en("{named}: Mehr als eine Einwilligung. Dieses überzählige Element bitte löschen.",
                                             "{named}: more than one consent. Please delete this surplus element.",
                                             named=named(extra)), item_id=extra.id, block_id=extra.block_id))
    if consent is not None and not (consent.config or {}).get("contact_email"):
        # participants who want to ask or have their answers deleted need a way (UI/UX audit 2026-10-05, V13)
        # staged: without an e-mail a warning; without an e-mail and an institution a warning in the draft and an
        # error at the start (there is then no way at all to reach the study)
        people = consent.kind.researchers(consent)
        institution = any((m.affiliation or "").strip() for m in people)
        if not people:  # one message for the one gap: person and e-mail (UI/UX audit 2026-10-07, W9)
            findings.append(Finding(
                ERROR, de_en("Einwilligung: Verantwortliche Person und Kontakt-E-Mail fehlen (Einstellungen › Einwilligung "
                             "und Datenschutz). Teilnehmende wissen sonst nicht, wohin sie sich mit Fragen oder einer Bitte "
                             "um Löschung wenden.",
                             "Consent: responsible person and contact email are missing (Settings › Consent and data "
                             "protection). Otherwise participants do not know where to turn with questions or a request "
                             "for deletion."),
                item_id=consent.id, link="surveys.detail", anchor="einwilligung"))
        else:
            findings.append(Finding(
            ERROR if start and not institution else WARNING,
            de_en("Einwilligung: keine Kontakt-E-Mail. Teilnehmende wissen sonst nicht, wohin sie sich mit Fragen oder einer "
                  "Bitte um Löschung wenden. Bitte unter Einstellungen › Einwilligung und Datenschutz ergänzen.",
                  "Consent: no contact email. Otherwise participants do not know where to turn with questions or a request "
                  "for deletion. Please add it under Settings › Consent and data protection.") if institution else
            de_en("Einwilligung: keine Kontakt-E-Mail und keine Einrichtung der verantwortlichen Personen. Teilnehmende "
                  "wissen sonst nicht, wohin sie sich mit Fragen oder einer Bitte um Löschung wenden. Bitte unter "
                  "Einstellungen › Einwilligung und Datenschutz ergänzen; ohne eine der beiden Angaben lässt sich die "
                  "Befragung nicht starten.",
                  "Consent: no contact email and no institution of the people responsible. Otherwise participants do "
                  "not know where to turn with questions or a request for deletion. Please add it under Settings › "
                  "Consent and data protection; without one of the two the survey cannot be started."),
            item_id=consent.id, link="surveys.detail", anchor="einwilligung", blocks_start=not institution))
    if consent is not None and (consent.config or {}).get("special_categories"):
        # special categories (Art. 9 GDPR; comparison with the RWTH template 2026-10-20, L8)
        findings.append(Finding(
            WARNING, de_en("Einwilligung: Die Befragung erhebt besondere Kategorien (Art. 9 DSGVO). Prüfen Sie mit Ihrer "
                           "Datenschutzbeauftragten, ob eine Datenschutz-Folgenabschätzung (Art. 35 DSGVO) nötig ist.",
                           "Consent: the survey collects special categories (Art. 9 GDPR). Check with your data protection "
                           "officer whether a data protection impact assessment (Art. 35 GDPR) is needed."),
            item_id=consent.id, link="surveys.detail", anchor="einwilligung"))
        body = consent.text("body") or ""
        if "Art. 9" not in body and "9(2)" not in body:
            findings.append(Finding(
                WARNING, de_en("Einwilligung: Der Text nennt die besonderen Kategorien und Art. 9 Abs. 2 lit. a DSGVO nicht. "
                               "Bitte den Standardtext einsetzen oder den Satz ergänzen.",
                               "Consent: the text does not mention the special categories and Art. 9(2)(a) GDPR. Please "
                               "insert the standard text or add the sentence."), item_id=consent.id))
    if consent is not None:
        before = [i for i in all_items[: all_items.index(consent)] if i.kind.collects_answer]
        if before:
            findings.append(Finding(
                WARNING, de_en("{n} Frage(n) vor der Einwilligung: Bei Ablehnung werden diese Antworten verworfen.",
                               "{n} question(s) before the consent: if it is declined, these answers are discarded.", n=len(before)),
                item_id=consent.id))
    from app import constructs

    # toolbox constructs need no check of the text: the consent shows a fixed paragraph on them (Consent.pool_notice)
    if consent is not None and any(constructs.is_linked_key(((i.config or {}).get("construct") or {}).get("key")) for i in all_items):
        for lang in survey.languages:
            body = ((consent.texts or {}).get(lang) or {}).get("body", "").lower()
            if body and not any(w in body for w in ("mehreren", "gleicher form", "several", "same form")):
                findings.append(Finding(
                    WARNING, de_en("Einwilligung in {lang}: Verknüpfte Elemente werden mit den Antworten anderer "
                                   "Befragungen zusammen ausgewertet. Bitte die Teilnehmenden darauf hinweisen (der Standardtext tut "
                                   "das von selbst), z. B. Text neu mit KI formulieren.",
                                   "Consent in {lang}: linked elements are analysed together with the answers of other "
                                   "surveys. Please tell the participants (the standard text already does so), e.g. "
                                   "reword the text with AI.", lang=_lang_name(lang)), item_id=consent.id))
    # month and year of birth with other features that narrow people down: recognisable in small samples (concept
    # Demografie-Standards 2026-10-15, risks; the construct card of ALT_1 says the same)
    birth = next((i for i in all_items if constructs.toolbox_key(i) == "ratswd_alt1"), None)
    narrowing = [i for i in all_items if i.type in ("federal_state", "postal_code", "geolocation")
                 or constructs.toolbox_key(i) in ("ratswd_hhg1", "ratswd_ber1")]
    if birth is not None and narrowing:
        findings.append(Finding(WARNING, de_en(
            "{named}: Geburtsmonat und -jahr zusammen mit {others} machen Personen in kleinen Stichproben erkennbar. "
            "Veröffentlichen Sie dann nur das Geburtsjahr oder Altersgruppen.",
            "{named}: month and year of birth together with {others} can make people recognisable in small samples. "
            "Then publish only the year of birth or age groups.", named=named(birth),
            others=", ".join(i.code for i in narrowing[:3])), item_id=birth.id, block_id=birth.block_id))
    for item in all_items:
        if constructs.toolbox_key(item):
            for lang in survey.languages:
                if "[System]" in str((item.texts or {}).get(lang, {})):
                    findings.append(Finding(WARNING, de_en("{named} in {lang}: Platzhalter „[System]“ durch den "
                                                           "Namen der Technik ersetzen.",
                                                           "{named} in {lang}: replace the placeholder “[System]” with "
                                                           "the name of the technology.", named=named(item), lang=_lang_name(lang)),
                                            item_id=item.id, block_id=item.block_id))
                    break
    if survey.status == survey.DRAFT and (survey.qversion or 1) > 1:
        from app.extensions import db
        from app.models import Response

        old = db.session.scalar(db.select(db.func.count(Response.id)).filter(
            Response.survey_id == survey.id, Response.source == "real",
            db.func.coalesce(Response.qversion, 1) < survey.qversion))
        if old:
            findings.append(Finding(WARNING, de_en("Fragebogen-Fassung {v}: {n} Teilnahmen stammen aus früheren "
                                                   "Fassungen. Im Export an der Spalte qversion erkennbar; geänderte oder gelöschte Fragen "
                                                   "sind nur im gesicherten Stand im Reiter „Export“ vollständig.",
                                                   "Questionnaire revision {v}: {n} participations come from earlier revisions. "
                                                   "In the export they can be told by the column qversion; changed or deleted "
                                                   "questions are complete only in the saved state in the tab “Export”.",
                                                   v=survey.qversion, n=old)))
    from app import flow, image_styles

    current_style = image_styles.style_id(survey)
    # the approved AI images of the whole survey in one query, not one per element (code review 2026-10-07, D5)
    from app.extensions import db as _db
    from app.models import Asset, Block, Item

    ai_images = {}
    for a in _db.session.scalars(_db.select(Asset).join(Item, Asset.item_id == Item.id).join(Block, Item.block_id == Block.id)
                                 .filter(Block.survey_id == survey.id, Asset.source == "ai", Asset.status == "approved")
                                 .order_by(Asset.id)):
        ai_images.setdefault(a.item_id, []).append(a)
    for item in all_items:
        stale = [a for a in ai_images.get(item.id, []) if a.style != current_style]
        if stale:
            findings.append(Finding(WARNING, de_en("{named}: {n} KI-Bild(er) im Stil „{old}“, aktuell gewählt: „{new}“.",
                                                   "{named}: {n} AI image(s) in the style “{old}”, currently chosen: “{new}”.",
                                                   named=named(item), n=len(stale),
                                                   old=image_styles.style_label(survey, stale[0].style),
                                                   new=image_styles.style_label(survey)), item_id=item.id))
        # "Bild über dem Text": a hint; "Bild statt Text" makes it a problem of the element (validate_images)
        add = hasattr(item.kind, "level_images_on") and item.kind.level_images_on(item) == "add"
        for code, n, total in (item.kind.level_image_gaps(item) if add else []):
            findings.append(Finding(WARNING, de_en("{named}: Merkmal {code} hat nur für {n} von {total} Stufen ein Bild; "
                                                   "Stufen ohne Bild fallen in der Tabelle auf.",
                                                   "{named}: attribute {code} has an image for only {n} of {total} levels; "
                                                   "levels without an image stand out in the table.",
                                                   named=named(item), code=code, n=n, total=total), item_id=item.id))
    from app import loops

    from app import piping, rows

    for severity, message, item_id, block_id in (loops.check(survey) + piping.check(survey) + rows.check(survey)
                                                 + item_condition.check(survey)):
        findings.append(Finding(severity, message, item_id=item_id, block_id=block_id))
    from app import panel
    from app.items.base import BUILTIN_VARIABLES

    # {{pid}}, URL parameters, answers to other questions (piping.check looks at those)
    everywhere = (BUILTIN_VARIABLES | {p["name"] for p in panel.config(survey)["params"]} | piping.names(survey)
                  | set(flow.branch_names(survey)) | set(__import__("app.webservice", fromlist=["names"]).names(survey)))
    from app.items.base import placeholders

    for block in survey.blocks:
        if not loops.active(block):
            # the block title is filled like the questions (UI/UX audit 2026-10-05, V12)
            used = set().union(*[placeholders(t) for t in (block.titles or {}).values()]) - everywhere
            if used and block.show_title:
                findings.append(Finding(WARNING, de_en("Blocktitel „{title}“: {used} wird nicht ersetzt: Der "
                                                       "Block steht in keiner Schleife.",
                                                       "Block title “{title}”: {used} is not replaced: the block is in "
                                                       "no loop.", title=block.title(),
                                                       used=", ".join("{{" + u + "}}" for u in sorted(used))),
                                        block_id=block.id))
            for item in block.items:
                used = item.kind.used_placeholders(item) - everywhere - (item.kind.prompt_variables(item) if item.kind.has_image else set())
                if used:
                    findings.append(Finding(WARNING, de_en("{named}: {used} wird nicht ersetzt: Der Block steht in keiner "
                                                           "Schleife.", "{named}: {used} is not replaced: the block is in no "
                                                           "loop.", named=named(item),
                                                           used=", ".join("{{" + u + "}}" for u in sorted(used))),
                                            item_id=item.id))
    if consent is not None and (consent.config or {}).get("duration") and any(i.kind.collects_answer for i in all_items if i is not consent):
        # the consent names a duration: remind when the questionnaire has grown or shrunk far away from it
        from app import duration

        stated = consent.config["duration"]
        est = duration.estimate(survey)
        typical = duration.minutes(est["typical"])
        # one minute off and more than a quarter (UI audit 2026-10-04, R49: "2 Minuten" against "ca. 3 Min." went unnoticed)
        if abs(typical - stated) >= 1 and abs(typical - stated) > 0.25 * typical:
            shown = duration.label(est)
            end = "" if shown.endswith(".") else "."  # "ca. 3 Min." ends the sentence itself (W118)
            findings.append(Finding(
                WARNING, de_en("Einwilligung: Genannt sind etwa {stated} Minuten, geschätzt sind {est}{end} "
                               "Bitte die Dauer anpassen (in der Einwilligung: „Aus dem Fragebogen schätzen“).",
                               "Consent: about {stated} minutes are stated, the estimate is {est}{end} "
                               "Please adjust the duration (in the consent: “Estimate from the questionnaire”).",
                               stated=stated, est=shown, end=end), item_id=consent.id))
    if consent is not None:
        # a few words cannot name purpose, those responsible and what is stored: a placeholder left in by mistake
        # ("Text") must not reach participants unnoticed (UI/UX audit 2026-10-16, 1)
        words = len(re.findall(r"\w+", consent.text("body", survey.default_language) or ""))
        if 0 < words < 25:
            findings.append(Finding(
                WARNING, de_en("Einwilligung: Der Text hat nur {n} Wörter. Er nennt so kaum Zweck, Verantwortliche "
                               "und was gespeichert wird; „Standardtext einsetzen“ in der Einwilligung hilft.",
                               "Consent: the text has only {n} words. That hardly names the purpose, those responsible "
                               "and what is stored; “Insert standard text” in the consent helps.", n=words),
                item_id=consent.id))
    findings.extend(duration_findings(survey, consent, all_items))
    findings.extend(title_findings(survey))
    from app import external, theme
    from app.items import geolocation

    if consent is not None:
        # an edited consent text does not follow the settings by itself: remind of what is recorded but not named
        recorded = [(survey.paradata, de_en("technische Angaben (Gerät, Browser)", "technical information (device, browser)"),
                     ("technische angaben", "gerätetyp", "browser", "technical information", "device type")),
                    (theme.is_auto(theme.of(survey)), de_en("die gezeigte Darstellung (hell oder dunkel)",
                                                            "the appearance shown (light or dark)"),
                     ("hell oder dunkel", "helle oder dunkle", "darstellung", "light or dark", "dark mode", "appearance")),
                    (getattr(survey, "page_timing", False), de_en("die Zeit je Seite", "the time per page"),
                     ("wie lange", "zeit je seite", "zeit pro seite", "bearbeitungszeit", "verweildauer",
                      "how long", "time spent", "time on each page", "time per page")),
                    (any(i.type == "media" for b in survey.blocks for i in b.items),
                     de_en("wie Video oder Audio abgespielt wurden (Abspielprotokoll)", "how video or audio was played (playback log)"),
                     ("abgespielt", "abspiel", "video", "tonaufnahme", "played", "playback")),
                    (getattr(survey, "focus_paste", False), de_en("Fensterwechsel und Einfügen", "window switches and pasting"),
                     ("fenster", "tab wechsel", "einfügen", "window", "paste")),
                    (getattr(survey, "ai_bait", False), de_en("den Köder für KI-Agenten (automatisch ausgefüllte Antworten)",
                                                              "the bait for AI agents (automatically filled answers)"),
                     ("ki-systeme", "automatisch ausgefüllt", "automatisiert", "ai systems", "automatically")),
                    (getattr(survey, "repeat_marker", False), de_en("im Browser, dass teilgenommen wurde (Cookie gegen erneute Teilnahme)",
                                                                    "in the browser that someone took part (cookie against repeated participation)"),
                     ("cookie",)),
                    (bool(external.hosts(survey)), de_en("Daten auf einer anderen Website (externer Abschnitt, verbunden über "
                                                         "die Teilnahme-ID)", "data on another website (external section, "
                                                         "linked by the participation ID)"), ("andere website", "anderen website", "externe", "web-app", "webanwendung",
                                           "another website", "other website", "external", "web app")),
                    (bool(__import__("app.webservice", fromlist=["privacy_hosts"]).privacy_hosts(survey)),
                     de_en("Angaben an einen Web Service übermittelt", "answers passed on to a web service"),
                     ("webdienst", "web service", "webservice", "web-dienst")),
                    (getattr(survey, "geo_ip", False), de_en("Land und Bundesland, geschätzt aus der IP-Adresse",
                                                             "country and federal state, estimated from the IP address"),
                     ("ip-adresse schätzen", "aus ihrer ip", "aus deiner ip", "bundesland", "from your ip", "federal state")),
                    (geolocation.finest(survey) is not None, de_en("den Standort des Geräts (Element Standort)",
                                                                   "the location of the device (element Location)"),
                     ("standort", "position", "location"))]
        for lang in survey.languages:
            body = ((consent.texts or {}).get(lang) or {}).get("body", "")
            body = consent.kind.shown_body(consent, lang).lower() if body else ""  # as participants read it (X43)
            for on, what, words in recorded:
                if on and body and not any(w in body for w in words):
                    findings.append(Finding(
                        WARNING, de_en("Einwilligung in {lang}: Die Befragung erfasst {what}, der Text nennt das nicht. "
                                       "Bitte im Einwilligungstext ergänzen.",
                                       "Consent in {lang}: the survey records {what}, the text does not say so. "
                                       "Please add it to the consent text.", lang=_lang_name(lang), what=what),
                        item_id=consent.id))
            # the deletion code is a promise, not something recorded: an own text should make it, and must not
            # still say that answers cannot be deleted
            if getattr(survey, "erase_code", False) and lang in ("de", "de-du", "en") and body and not any(
                    w in body for w in __import__("app.items.consent", fromlist=["ERASE_WORDS"]).ERASE_WORDS):
                findings.append(Finding(
                    WARNING, de_en("Einwilligung in {lang}: Teilnehmende erhalten am Ende einen Löschcode für ihre "
                                   "Antworten, der Text nennt ihn nicht. Bitte im Einwilligungstext ergänzen oder die Einstellung "
                                   "„Löschcode für Teilnehmende“ ausschalten.",
                                   "Consent in {lang}: participants receive a deletion code for their answers at the end, "
                                   "the text does not mention it. Please add it to the consent text or switch off the "
                                   "setting “Deletion code for participants”.", lang=_lang_name(lang)), item_id=consent.id))
    if getattr(survey, "erase_code", False):
        from app import panel as panel_mod

        passing = panel_mod.passing_links(survey)
        if passing:
            findings.append(Finding(
                WARNING, de_en("Löschcode: Bei {where} leitet der Abschlusslink sofort oder nach wenigen "
                               "Sekunden weiter; den Löschcode auf der letzten Seite sehen diese Personen dann nicht oder nur kurz. "
                               "Entweder dort „Endseite mit Knopf“ wählen oder die Einstellung „Löschcode für Teilnehmende“ ausschalten.",
                               "Deletion code: with {where} the completion link redirects at once or after a few seconds; "
                               "these people then do not see the deletion code on the last page, or only briefly. Either "
                               "choose “End page with button” there or switch off the setting “Deletion code for "
                               "participants”.", where=", ".join(passing))))
    if getattr(survey, "geo_ip", False) and consent is None:
        findings.append(Finding(WARNING, de_en("Land und Bundesland aus der IP-Adresse sind eingeschaltet, die Befragung hat aber "
                                               "keine Einwilligung: Nachgeschlagen wird erst mit der Zustimmung, so bleibt "
                                               "beides leer.",
                                               "Country and federal state from the IP address are switched on, but the survey "
                                               "has no consent: they are looked up only with the consent, so both stay empty.")))
    for severity, message, block_id in flow.check(survey):  # a quota's gap message carries its page (quotas.Gap)
        findings.append(Finding(severity, message, block_id=block_id, link=getattr(message, "link", None)))
    from app import panel as panel_mod

    for severity, message in panel_mod.check(survey):  # completion links that would not be accepted today
        findings.append(Finding(severity, message))
    from app import theme

    look = theme.clean(survey.theme)
    for severity, message in theme.problems(look) + theme.logo_problems(look, survey.languages):  # unreadable colours, logo without text
        findings.append(Finding(severity, message + de_en(" Ändern unter Einstellungen → Erscheinungsbild.",
                                                          " Change it under Settings → Appearance.")))
    for col, items in columns.items():
        if len(items) > 1:
            findings.append(Finding(ERROR, de_en("Variablenname „{col}“ ist mehrfach vergeben.",
                                                 "Variable name “{col}” is used more than once.", col=col), item_id=items[0].id))
    # the same over everything the export writes: its own columns (status, pid, scheme, url_…, t_…), the columns
    # of loop passes (q_1) and order columns; SPSS also takes "Status" and "status" for one name
    from app import export

    reported = {col.lower() for col, items in columns.items() if len(items) > 1}
    names = {}
    exported = [col for col, _ in export.columns(survey, with_hb=False)]
    for col in exported:
        names.setdefault(col.lower(), []).append(col)
    for low, cols in names.items():
        if len(cols) > 1 and low not in reported:
            owner = next((i for i in all_items if low == i.code.lower() or low.startswith(i.code.lower() + "_")), None)
            findings.append(Finding(
                ERROR, de_en("Variablenname „{col}“ steht doppelt im Export: So heißt auch eine feste Spalte, die Spalte "
                             "einer Schleife oder ein Name, der sich nur in Groß- und Kleinschreibung unterscheidet. Bitte umbenennen.",
                             "Variable name “{col}” appears twice in the export: a fixed column, the column of a loop or a "
                             "name that differs only in upper and lower case has the same name. Please rename it.",
                             col=cols[-1]),
                item_id=owner.id if owner else None))
    # SPSS: reserved words (TO, BY, ...) and names over 64 characters (a loop suffix, _order, ...) get another
    # name in the .sav and .sps files (sav.spss_names, named in the codebook); a warning, the export stays valid
    from app import sav

    spss = sav.spss_names(exported)
    renamed = [(col, spss[col]) for col in exported if spss[col] != col and col.lower() not in reported and len(names[col.lower()]) == 1]
    if renamed:
        owner = next((i for i in all_items if renamed[0][0].lower() == i.code.lower()
                      or renamed[0][0].lower().startswith(i.code.lower() + "_")), None)
        shown = ", ".join(de_en("„{a}“ → „{b}“", "“{a}” → “{b}”", a=a, b=b) for a, b in renamed[:5]) + (
            de_en(" und {n} weitere", " and {n} more", n=len(renamed) - 5) if len(renamed) > 5 else "")
        findings.append(Finding(
            WARNING, de_en("SPSS-Export: {shown}. SPSS kennt diese Namen nicht (Schlüsselwort wie TO, BY, AND oder länger als "
                           "64 Zeichen); die SPSS-Dateien nennen die Variablen deshalb anders, das Codebuch vermerkt es. Für gleiche Namen "
                           "in allen Dateien bitte kürzer oder anders benennen.",
                           "SPSS export: {shown}. SPSS does not accept these names (a keyword such as TO, BY, AND or longer "
                           "than 64 characters); the SPSS files therefore name the variables differently, the codebook notes "
                           "it. For the same names in all files please choose shorter or different names.", shown=shown),
            item_id=owner.id if owner else None))
    # the study plan (app/study_plan.py): hypotheses whose elements are missing
    from app import study_plan

    findings.extend(study_plan.findings(survey))
    return findings


def blocking(findings):
    return [f for f in findings if f.severity == ERROR]


def stats(survey):
    """Key figures for the editor sidebar. `orders` counts distinct presentation orders
    (the state space from randomization); M5 adds conditions and loop rows."""
    from math import factorial

    blocks = survey.blocks
    pages = [p for b in blocks for p in b.pages()]
    items = [i for p in pages for i in p]
    questions = [i for i in items if i.kind.collects_answer]
    columns = sum(len(i.kind.export_columns(i)) for i in items)
    has_consent = any(i.type == "consent" for i in items)
    # Paths through the flow: randomizers (conditions), loop row selections/orders, and conditional
    # blocks (shown or not). Item order within blocks is a separate factor; statement order inside
    # a scale is not counted (it does not change what someone is asked).
    within = 1
    for b in blocks:
        if b.randomize_items:
            for p in b.pages():
                within *= factorial(len(p))
    from app import loops

    loop_rows = 0
    for loop in (loops.config_node(nd) for nd in loops.nodes(survey)):
        if loop["columns"] and loop["rows"]:
            loop_rows += len(loop["rows"])
    ai_images = sum(
        len(i.kind.required_keys(i)) for i in items if i.kind.has_image and i.config.get("source") == "ai"
    ) + sum(1 for i in items if not i.kind.has_image and i.kind.has_image_set(i)  # image sets: not what was uploaded
            for k in i.kind.required_keys(i) if not any(a.key == k and a.source == "upload" for a in i.assets))
    from app import duration

    figures = _flow_figures(survey)
    # paths: randomizers and branches only; loops (micro-scenarios) run for everyone, so their row
    # selection and order would inflate the number without saying anything about the design
    paths = figures["conditions"] * 2 ** figures["branches"]
    return {
        "duration": duration.estimate(survey),
        "blocks": len(blocks),
        "pages": len(pages),
        "questions": len(questions),
        "columns": columns,
        "paths": paths,
        "orders": paths * within,  # including the item order within blocks
        "ai_images": ai_images,
        "has_consent": has_consent,
        "loop_rows": loop_rows,
        **figures,
    }


def _flow_figures(survey):
    from app import flow

    return flow.figures(survey)


def human_count(n):
    from app.lang import english

    if english():  # the sidebar in English: 1,234 and 2.5 m
        if n < 10_000:
            return f"{n:,}"
        for limit, suffix in ((1e12, "tn"), (1e9, "bn"), (1e6, "m"), (1e3, "k")):
            if n >= limit:
                return f"{fixed(n / limit, 1)} {suffix}" if n < 1e15 else "> 1 quadrillion"
        return str(n)
    if n < 10_000:
        return f"{n:,}".replace(",", ".")
    for limit, suffix in ((1e12, "Bio."), (1e9, "Mrd."), (1e6, "Mio."), (1e3, "Tsd.")):
        if n >= limit:
            return f"{fixed(n / limit, 1)} {suffix}".replace(".", ",", 1) if n < 1e15 else "> 1 Brd."
    return str(n)
