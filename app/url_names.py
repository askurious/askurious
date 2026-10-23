"""English addresses (Philipp, 2026-10-08): every path of Askurious is English, e.g. /surveys/<code>/flow instead of
/surveys/<code>/ablauf, /surveys/<code>/researchdesign instead of /studienplan.

RENAMED lists the German path segments of before with their English ones; tests/platform/test_url_names.py makes sure no
route uses one of them again. A German address of before leads with 308 to its English one (english_path, Ralf
2026-10-08, as decisions.md "Englische Adressen" says): method, form data and query stay, so bookmarks, links in
mails, embedded dashboards and return links handed to web apps (/zurueck) keep working. Only an address no route
answers is looked at; links Askurious writes are English.

Two German addresses stay as they are, because services outside call them at a fixed address: /slack/aktion (set in
the Slack app; Slack follows no redirect) and /rechenknecht/… (COMPUTE_CALLBACK of the Rechenknecht)."""
from werkzeug.exceptions import NotFound
from werkzeug.routing import BaseConverter

RENAMED = {
    "abbrechen": "cancel", "abgleichen": "reconcile", "ablauf": "flow", "ableiten": "derive",
    "abmelden-ueberall": "sign-out-everywhere", "abschluss": "completion", "angaben": "details",
    "anmelden": "sign-in", "ansicht": "view", "antwort": "answer", "assistent": "assistant",
    "aufnahmegeraet": "recorder", "aus": "off", "ausblenden": "hide", "auswertung": "analysis",
    "automatisch": "automatic", "barrierefreiheit": "accessibility", "beenden": "end", "befragung": "survey",
    "befragung.json": "survey.json", "befragung.zip": "survey.zip", "befragungen": "surveys",
    "beschwerde": "complaint", "bestaetigen": "confirm", "darstellung": "display", "daten.<fmt>": "data.<fmt>",
    "datenbank": "database", "datenschutz": "privacy", "dateinamen.csv": "filenames.csv", "doch": "after-all",
    "druck": "print", "einbetten": "embed", "eingereicht": "submitted", "einrichten": "set-up",
    "einrichtung": "setup", "einstellungen": "settings", "elementtypen": "itemtypes", "ende": "end",
    "entwickeln": "develop", "ergebnis": "result", "ergebnis.json": "result.json", "experimentell": "experimental",
    "faelle.csv": "cases.csv", "fassung-wechseln": "switch-version", "fassungen": "versions",
    "favorit": "favourite", "feld": "fieldwork", "folien": "slides", "formular": "form", "fortsetzen": "resume",
    "fragebogen-<version>.docx": "questionnaire-<version>.docx", "fragebogen.docx": "questionnaire.docx",
    "fragen-publikum.csv": "audience-questions.csv", "freischalten": "admit", "gespraech": "conversation",
    "gewichtung": "weighting", "glossar": "glossary", "gruppe": "group", "gruppen": "groups",
    "hochladen": "upload", "impressum": "imprint", "kategorien": "categories",
    "kategoriensystem.qdc": "category-system.qdc", "ki": "ai", "klassen": "classes", "kommentar": "comment",
    "konstrukte": "constructs", "konten": "accounts", "konto": "account", "kreuzen": "cross", "leinwand": "screen",
    "leitfaden": "guide", "leitfrage": "guide-question", "lernen": "learn", "lernhilfen": "learning-aids",
    "literatur.<fmt>": "references.<fmt>", "loeschcode": "deletion-code", "loeschen": "delete",
    "messniveau": "level-of-measurement", "methodenteil.<fmt>": "methods-section.<fmt>", "modus": "mode",
    "neu": "new", "neu-zaehlen": "recount", "neue-fassung": "new-version", "notizen.docx": "notes.docx",
    "notizen.zip": "notes.zip", "nutzungsbedingungen": "terms", "passwort": "password",
    "passwort-vergessen": "forgot-password", "plz": "postcode", "praeregistrierung": "preregistration",
    "praesentieren": "present", "preise": "pricing", "protokoll": "log", "pruefen": "check",
    "qualitaet": "quality", "quelle.<fmt>": "source.<fmt>", "quoten": "quotas", "reaktion": "reaction",
    "referent": "speaker", "schleife": "loop", "siegelkette": "seal-chain", "siegelkette.zip": "seal-chain.zip",
    "sitzungen": "sessions", "skala": "scale", "skalen": "scales", "skalen.docx": "scales.docx",
    "speichern": "save", "sperren": "lock", "sprache": "language", "sprachnachrichten.zip": "voice-messages.zip",
    "stand": "state", "stand.csv": "state.csv", "steuern": "control", "stichprobe": "sample-size", "stimme": "vote",
    "stopp": "stop", "studienplan": "researchdesign", "suche": "search", "teil": "part", "teilen": "share",
    "transkribieren": "transcribe", "transkript": "transcript", "transkript.txt": "transcript.txt",
    "trennen": "disconnect", "uebersetzung": "translation", "verbinden": "connect", "verknuepfen": "link",
    "verknuepft": "linked", "verknuepfung-loesen": "unlink", "veroeffentlichungen": "publications",
    "verwendet.<fmt>": "used.<fmt>", "vignetten": "vignettes", "vorlage": "template", "vorlagen": "templates",
    "vorstudie": "pilot", "warum": "why", "wetter": "weather", "wieder": "again", "zeigen": "show",
    "zeitstempel": "timestamp", "ziel": "goal", "zitationen": "citations", "zugang": "access", "zuordnen": "assign",
    "zurueck": "back", "zusammenfassung": "summary", "zusammenfassungen": "summaries",
    "zusammenfassungen.csv": "summaries.csv", "zusammenfassungen.docx": "summaries.docx",
    "zusammenstellung": "compilation", "zusammenstellung.zip": "compilation.zip", "zweiter-faktor": "second-factor",
    # the steps of the set-up wizard (/surveys/<code>/setup/<step>, StepConverter); "ablauf" and "studienplan" above
    "modell.R": "model.R", "projekt.qdpx": "project.qdpx", "widerrufen": "withdraw",
    "titel": "title", "frage": "question",  # the steps of a live vote's set-up (live.STEPS)
    "projekt": "project", "sprachen": "languages", "einwilligung": "consent", "gestaltung": "design", "fertig": "done",
}

class StepConverter(BaseConverter):
    """A step of the set-up wizard: German key in the code and in setup_step, English in the address."""

    def to_python(self, value):
        back = {v: k for k, v in RENAMED.items()}
        if value in RENAMED and value not in back:  # a German name of before: no step under it any more
            from werkzeug.routing import ValidationError

            raise ValidationError()
        return back.get(value, value)  # "team" is the same in both

    def to_url(self, value):
        return RENAMED.get(value, value)


def _segment(seg):
    """The English segment for a German one of before ("daten.csv" -> "data.csv"), else the segment itself."""
    if seg in RENAMED:
        return RENAMED[seg]
    stem, dot, ext = seg.rpartition(".")
    if dot and f"{stem}.<fmt>" in RENAMED:
        return RENAMED[f"{stem}.<fmt>"].replace("<fmt>", ext)
    return seg


def english_path(path):
    """path with every German segment of before in English; the same path if none."""
    return "/".join(_segment(seg) for seg in path.split("/"))


def redirect_german():
    """before_request: an address no route answers that is a German one of before moves with 308 to its English one,
    if a route answers that (code audit 2026-10-08, W6)."""
    from flask import current_app, redirect, request

    if not isinstance(request.routing_exception, NotFound):
        return None
    target = english_path(request.path)
    if target == request.path:
        return None
    try:
        current_app.url_map.bind_to_environ(request.environ).match(target, method=request.method)
    except Exception:  # noqa: BLE001 - no route for the English address either (or not for this method)
        return None
    query = request.query_string.decode("latin-1")
    return redirect(request.script_root + target + ("?" + query if query else ""), code=308)
