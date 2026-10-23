"""Terms of use for researchers (licence.md in the project root).

Researchers accept the current version on their first login and after every change; until
then every page except login, public pages and the terms themselves redirects here. The
version is the "Version:" line of licence.md."""
import os
import re

from flask import Blueprint, abort, current_app, redirect, render_template, request, url_for
from flask_login import current_user
from markupsafe import Markup, escape

from app.extensions import db
from app.links import local_path
from app.models import utcnow

bp = Blueprint("terms", __name__, url_prefix="/terms")

# accounts.: who does not accept (new) terms can still delete the account. The three share links (dashboard,
# quota state, preview) are pages for people without an account; a researcher who happens to be signed in sees
# them like everyone else.
OPEN_ENDPOINTS = ("static", "terms.", "auth.", "public.", "participate.", "media.", "healthz", "cron.", "accounts.",
                  "board_share.", "quota_share.", "preview_share.", "data_access.api_", "mcp.")


def _path():
    return os.path.join(os.path.dirname(current_app.root_path), "licence.md")


# the operator's sentence in section 1 of licence.md: an installation puts its own into
# INSTANCE_OVERLAY/templates/terms/_operator.md (instance_overlay/README.md); its version stays that of licence.md
_OPERATOR = re.compile(r"<!-- operator -->(.*?)<!-- /operator -->", re.S)


def source():
    with open(_path(), encoding="utf-8") as f:
        text = f.read()
    own = os.path.join(current_app.config.get("INSTANCE_OVERLAY") or "", "templates", "terms", "_operator.md")
    if current_app.config.get("INSTANCE_OVERLAY") and os.path.isfile(own):
        with open(own, encoding="utf-8") as f:
            sentence = f.read().strip()
        return _OPERATOR.sub(lambda m: sentence, text)
    return _OPERATOR.sub(lambda m: m.group(1), text)


_version = {}  # per process: {path: ((mtime_ns, size), version)}


def version():
    """The "Version:" line of licence.md. Read again only when the file changed (its time or size): terms.gate asks
    before every request of a signed-in person (code review 2026-10-07, K9)."""
    path = _path()
    try:
        st = os.stat(path)
        stamp = (st.st_mtime_ns, st.st_size)
    except OSError:
        stamp = None
    kept = _version.get(path)
    if stamp is not None and kept is not None and kept[0] == stamp:
        return kept[1]
    m = re.search(r"^Version:\s*(\S+)", source(), re.M)
    found = m.group(1) if m else "1"
    if stamp is not None:
        _version[path] = (stamp, found)
    return found


# What each version changed, newest first (UI/UX audit 2026-10-09, X69): who accepted an older version sees the
# entries since then above the text, each with links to its sections (#abschnitt-N, the headings of licence.md and
# terms_en.html). A new "Version:" line in licence.md needs its entry here (tests/ui/test_ux_audit9_e.py).
CHANGES = (
    ("2026-10-23c", "Organisationen (Abschnitt 2): Admins können festlegen, dass nur sie Personen von außen in die "
                    "Organisation, ihre Gruppen und Befragungen holen.",
     "Organisations (section 2): admins can set that only they bring people from outside into the organisation, its "
     "groups and surveys.", (2,)),
    ("2026-10-23b", "Dienste Dritter (Abschnitt 6): KI-Chat für Teilnehmende (experimentell). Die Nachrichten gehen über "
                    "einen Modellzugang, den Sie für die Befragung selbst eintragen; Sie brauchen eine Vereinbarung mit "
                    "dem Anbieter und eine Einwilligung, die den KI-Chat nennt.",
     "Third-party services (section 6): AI chat for participants (experimental). The messages go through a model "
     "access you enter for the survey yourself; you need an agreement with the provider and a consent that names the "
     "AI chat.", (6,)),
    ("2026-10-23", "Inhalte Dritter (Abschnitt 7): Die aus OpenStreetMap abgeleiteten Postleitzahldaten gibt es nicht "
                   "mehr zum Herunterladen; die Seite „Lizenzen und Datenquellen“ beschreibt, wie sie entstanden sind, "
                   "eine Kopie gibt es auf Anfrage. Die Umrisse der Bundesländer tragen „© BKG (2026)“, das Jahr des "
                   "letzten Datenbezugs. Keine neuen Pflichten für Sie.",
     "Third-party contents (section 7): the postcode data derived from OpenStreetMap are no longer offered as "
     "downloads; the page “Licences and data sources” describes how they came about, a copy is available on "
     "request. The outlines of the federal states carry “© BKG (2026)”, the year of the last download. No new "
     "obligations for you.", (7,)),
    ("2026-10-19", "Dienste Dritter (Abschnitt 6): Die App für Tagebuchstudien zeigt die Fragebögen in Android System "
                   "WebView von Google; die WebView kann von sich aus Dienste von Google anfragen (Safe Browsing, "
                   "Autofill-Vorhersagen bei eingeschaltetem Autofill-Dienst). Keine neuen Pflichten für Sie.",
     "Third-party services (section 6): the app for diary studies shows the questionnaires in Android System WebView "
     "by Google; the WebView may contact Google services on its own (Safe Browsing, autofill predictions while an "
     "autofill service is switched on). No new obligations for you.", (6,)),
    ("2026-10-18", "Konten (Abschnitt 2): Admins Ihrer Heimat-Organisation können ein Konto, das nur zu ihrer "
                   "Organisation gehört, zur Löschung vormerken; es ist sofort gesperrt und wird nach 30 Tagen "
                   "gelöscht, Befragungen und Designs, die nur Ihnen gehören, gehen an eine Person der Organisation "
                   "oder werden mitgelöscht. Gehören Sie zu mehreren Organisationen, entfernen sie Sie nur aus ihrer.",
     "Accounts (section 2): admins of your home organisation can mark an account that belongs to their organisation "
     "only for deletion; it is blocked at once and deleted after 30 days, and surveys and designs that belong to you "
     "alone pass to a person of the organisation or are deleted. If you belong to several organisations, they only "
     "remove you from theirs.", (2,)),
    ("2026-10-17b", "Instrumentrechte (Abschnitt 7): Für KUT, TAM und GDMS ersetzt eine Anmeldung oder die Angabe "
                    "nichtkommerzieller Nutzung keinen Rechtebeleg. Katalogwortlaut und neue Verwendung setzen "
                    "einen vom Betreiber geprüften Nachweis für Person, Studie und Sprachfassung voraus. "
                    "Öffentliche Ausgaben enthalten für diese Instrumente neutrale IDs und Quellen statt Wortlaut.",
     "Instrument rights (section 7): for KUT, TAM and GDMS, signing in or stating non-commercial use does not "
     "replace rights evidence. Catalogue wording and new use require operator-checked evidence for the account, "
     "study and language version. Public outputs contain neutral IDs and sources instead of this wording.", (7,)),
    ("2026-10-17", "Überarbeitet und gekürzt, ohne neue Pflichten für Sie: Betreiber wie im Impressum (Abschnitt 1); "
                   "Auftragsverarbeitung um Widerspruch gegen neue Auftragsverarbeiter, Hinweis bei rechtswidriger "
                   "Weisung, Herausgabe über den Export und Überprüfungen ergänzt, Datensicherheit nicht mehr von der "
                   "Gewähr ausgenommen (Abschnitte 2 und 8); was die Einwilligungserklärung nennt, steht einmal in "
                   "Abschnitt 2; Dienste Dritter zusammengefasst (Abschnitt 6); die reinen Quellenangaben ohne Pflicht "
                   "für Sie (Bibliotheken, Schrift, Transkriptionsmodelle und weitere) stehen nur noch auf der Seite "
                   "„Lizenzen und Datenquellen“ (Abschnitt 7).",
     "Revised and shortened, with no new obligations for you: operator as in the legal notice (section 1); "
     "processing on your behalf adds the objection to new processors, a notice of unlawful instructions, handing "
     "over through the export and inspections, and data security is no longer excluded from the warranty "
     "(sections 2 and 8); what the consent form states is said once in section 2; third-party services combined "
     "(section 6); the plain attributions without an obligation for you (libraries, font, transcription models and "
     "others) are only on the page “Licences and data sources” (section 7).",
     (1, 2, 6, 7, 8)),
    ("2026-10-16b", "Löschen durch Teilnehmende (Abschnitt 2): ohne Löschcode im Browser der Teilnahme bis 15 Minuten "
                    "nach ihrem Ende (bisher stand hier fälschlich sieben Tage).",
     "Deletion by participants (section 2): without a deletion code in the browser of the participation for up to 15 "
     "minutes after it ended (this said seven days by mistake).",
     (2,)),
    ("2026-10-16", "Hinweise auf rechtswidrige Inhalte (Abschnitt 2): Meldeweg, mögliche Maßnahmen der Admins und ihre "
                   "Mitteilung mit Begründung. Lizenzen (Abschnitt 7): nichtkommerzielle Konstrukte auch nicht für "
                   "Auftragsforschung; englische Adaptionen mit eigener Quelle und Lizenz; Wortlaut aus APA PsycTests "
                   "nicht an KI-Übersetzung und KI-Textprüfung, Präregistrierungen damit nicht automatisch nach Coscine; "
                   "abgeleitete Gebietsdateien zum Herunterladen; Transkription nur mit frei lizenzierten Modellen; "
                   "vollständige Liste der Bibliotheken; Autoren des Quellcodes.",
     "Notices of illegal content (section 2): how to report, the measures the admins can take and how they are "
     "communicated with reasons. Licences (section 7): non-commercial constructs not for commissioned research either; "
     "English adaptations with their own source and licence; wording from APA PsycTests not sent to AI translation or "
     "the AI text check, preregistrations holding it not uploaded to Coscine automatically; derived area files to "
     "download; transcription only with freely licensed models; full list of libraries; authors of the source code.",
     (2, 7)),
    ("2026-10-15d", "Organisationen (Abschnitt 2): Ihr Konto gehört zu einer Heimat-Organisation und kann weiteren "
                    "angehören; Gruppen teilen und die Personensuche findet nur innerhalb; Gäste ohne Organisation; "
                    "Admins der Heimat-Organisation verwalten Ihr Konto im Rahmen der Plattform, ohne Inhalte und "
                    "Antworten Ihrer Befragungen zu sehen; statt einer Einrichtung steht Ihre Heimat-Organisation; Logo "
                    "und Standarddesign je Organisation.",
     "Organisations (section 2): your account belongs to a home organisation and can belong to further ones; groups "
     "share and the people search finds only within; guests without an organisation; admins of the home organisation "
     "manage your account within the platform's limits, without seeing the content or answers of your surveys; your "
     "home organisation stands in place of an institution; logo and default design per organisation.", (2,)),
    ("2026-10-15c", "Befristete Konten (Nutzertypen): Sperre am Ende der Laufzeit nach Hinweisen in der Plattform, "
                    "Löschung nach der Löschfrist, wenn nichts zu übergeben ist; bei Betreuung ist die betreuende Person "
                    "Admin Ihrer Befragungen; Admins legen Laufzeit, Befragungsarten und Anlegerecht je Konto fest. "
                    "Kontakt (Abschnitt 10): Fragen zu den Bedingungen richten Sie über das Impressum an den Betreiber "
                    "(Link auf die Seite „Impressum & Datenschutz“ statt der Adresse einer persönlichen Webseite).",
     "Accounts limited in time (user types): blocked at the end of the period after notices in the platform, deleted "
     "after the deletion period if nothing has to be handed over; with a supervisor, the supervising person is admin "
     "of your surveys; admins set period, types of survey and right to create per account. Contact (section 10): you "
     "address questions about the terms to the operator via the legal notice (a link to the page “Legal notice & "
     "privacy” instead of the address of a personal website).", (2, 10)),
    ("2026-10-15b", "Interviews: Die Einwilligung aller, die sprechen, bestätigen Sie zu Beginn jedes Interviews mit einem "
                    "Haken („Einwilligung aller Sprechenden liegt vor“), nicht mehr mit einem eigenen Haken nur beim "
                    "Videogespräch.",
     "Interviews: you confirm the consent of everyone who speaks at the start of every interview with one tick "
     "(“Consent of everyone who speaks given”), no longer with a tick of its own for video calls only.", (3,)),
    ("2026-10-15a", "Interviewlinks: Personen ohne Konto führen Interviews eines Leitfadens in Ihrem Auftrag; Sie wählen "
                    "sie aus, verpflichten sie auf Vertraulichkeit, geben den Link nur ihnen selbst und sperren ihn, wenn "
                    "die Arbeit endet. Lizenzen: die demografischen Standardfragen aus dem Standardfragenkatalog des "
                    "RatSWD (2023, CC BY 4.0) und die Standardvariablen von KonsortSWD (Schneider et al., 2026, "
                    "CC BY 4.0) für den Export unter den Inhalten Dritter. KI-Agenten-Schnittstelle (experimentell): Ein "
                    "eigener KI-Agent liest mit persönlichem Token Fragebögen und bearbeitet Entwürfe; die Inhalte "
                    "überträgt der Agent an den Anbieter, den Sie wählen, in Ihrer Verantwortung; lizenzpflichtige "
                    "Wortlaute (APA PsycTests) gibt die Schnittstelle nicht heraus.",
     "Interviewer links: people without an account conduct interviews of a guide on your behalf; you choose them, "
     "oblige them to confidentiality, give the link only to them and block it when the work ends. Licences: the "
     "demographic standard questions from the RatSWD catalogue of standard questions (2023, CC BY 4.0) and the "
     "standard variables of KonsortSWD (Schneider et al., 2026, CC BY 4.0) for the export among the third-party "
     "content. AI agent interface (experimental): your own AI agent reads questionnaires and edits drafts with a "
     "personal token; the agent transfers the contents to the provider you choose, under your responsibility; the "
     "interface does not hand out licensed wordings (APA PsycTests).", (3, 6, 7)),
    ("2026-10-13a", "Lizenzen: google-re2 (BSD 3-Clause, mit Abseil unter Apache 2.0) unter den verwendeten "
                    "Bibliotheken; es prüft die eigenen regulären Ausdrücke in Freitextfeldern.",
     "Licences: google-re2 (BSD 3-Clause, with Abseil under Apache 2.0) among the libraries used; it checks the "
     "own regular expressions of free-text fields.", (7,)),
    ("2026-10-12c", "Lizenzen: PDF.js (Apache 2.0) unter der mitgelieferten Software; es wandelt beim Hochladen von Folien"
                    " für eine Live-Abstimmung die PDF-Datei im eigenen Browser in Bilder um.",
     "Licences: PDF.js (Apache 2.0) among the bundled software; when slides are uploaded for a live poll it turns "
     "the PDF file into pictures in your own browser.", (7,)),
    ("2026-10-12b", "Sicherungen: alle vier Stunden statt täglich, und auch die hochgeladenen Dateien (Bilder, "
                    "Aufnahmen); gelöscht weiter nach 14 Tagen.",
     "Backups: every four hours instead of daily, and the uploaded files too (images, recordings); still deleted "
     "after 14 days.", (2,)),
    ("2026-10-12a", "Lizenzen: die Sperrliste für Passwörter (zxcvbn, MIT) unter den Daten Dritter.",
     "Licences: the block list for passwords (zxcvbn, MIT) among the third-party data.", (7,)),
    ("2026-10-11c", "Tagebuchstudien mit der Android-App (experimentell): Pseudonymität der Einträge, Texte der "
                    "Benachrichtigungen, Belastung und Testcode; die App und Google Play unter den Diensten Dritter.",
     "Diary studies with the Android app (experimental): pseudonymity of the entries, texts of the notifications, "
     "burden and test code; the app and Google Play among the third-party services.", (3, 6)),
    ("2026-10-11b", "Papierfragebögen sind nicht mehr experimentell: für alle Forschenden unter „Datenerhebung“.",
     "Paper questionnaires are no longer experimental: for all researchers under “Data collection”.", (2, 6)),
    ("2026-10-11a", "KI-Hilfe beim Kategorisieren und KI-Lesen der Handschrift: Die Bestätigung, dass die "
                    "Teilnehmenden informiert wurden, gibt die Eigentümerin oder ein Admin der Befragung, und sie gilt "
                    "nur für den genannten Empfänger; ein anderer Dienst (etwa der eigene KI-Zugang einer beteiligten "
                    "Person) braucht eine eigene Bestätigung. „Generative KI nutzen“ aus stoppt auch die Transkription "
                    "und Anfragen an den eigenen KI-Zugang.",
     "AI help for categorising and AI reading of handwriting: the confirmation that the participants were told is "
     "given by the owner or an admin of the survey and holds only for the recipient it names; another service (such "
     "as the own AI access of a person involved) needs its own confirmation. “Use generative AI” off also stops the "
     "transcription and requests to the own AI access.", (6,)),
    ("2026-10-10d", "Papierfragebögen (experimentell): Das KI-Lesen der Handschrift schickt Bildausschnitte der "
                    "Schreibfelder an OpenAI (dann ein weiterer Auftragsverarbeiter); nur nach Hinweis in der "
                    "Einwilligung und einer Bestätigung je Befragung. Lizenzen: der Datensatz handschriftlicher "
                    "Ziffern für die Ziffernerkennung (UCI, CC BY 4.0).",
     "Paper questionnaires (experimental): the AI reading of handwriting sends image sections of the writing fields "
     "to OpenAI (then a further processor); only after a note in the consent and a confirmation per survey. Licences: "
     "the data set of handwritten digits for the digit recognition (UCI, CC BY 4.0).", (2, 6, 7)),
    ("2026-10-10c", "Papierfragebögen (experimentell): Papierbögen bewahren Sie selbst auf und vernichten sie auch "
                    "nach einer Löschanfrage; Scans löscht die Plattform nach der Übernahme. Lizenzen: die "
                    "Bibliotheken reportlab und pypdf.",
     "Paper questionnaires (experimental): you keep the paper sheets yourself and destroy them also after a deletion "
     "request; the platform deletes the scans after the takeover. Licences: the libraries reportlab and pypdf.", (2, 7)),
    ("2026-10-10b", "Eigener KI-Zugang für alle, nicht mehr experimentell; neue Seite „KI-Nutzung“ unter „Konto“ mit "
                    "den eigenen KI-Aufrufen der letzten 30 Tage und dem Schalter, generative KI für sich ganz "
                    "abzuschalten. Lizenzen: die Bibliotheken Flask-Migrate, Alembic und python-docx; im "
                    "Docker-Betrieb für PostgreSQL psycopg (LGPL-3.0).",
     "Own AI access for everyone, no longer experimental; new page “AI usage” under “Account” with your own AI calls "
     "of the last 30 days and the switch to turn generative AI off for yourself altogether. Licences: the libraries "
     "Flask-Migrate, Alembic and python-docx; in the Docker set-up psycopg (LGPL-3.0) for PostgreSQL.", (6, 7)),
    ("2026-10-10", "KI-Funktionen: Die KI-Hilfe beim Kategorisieren offener Antworten schickt Antworttexte von "
                   "Teilnehmenden an OpenAI (dann ein weiterer Auftragsverarbeiter); nur nach Hinweis in der "
                   "Einwilligung und einer Bestätigung je Befragung. Genauer beschrieben, wie die Plattform schon "
                   "arbeitet: Sicherungskopien, Zugang zur Administration, Server-Protokolle und weitere Dienste "
                   "(Transkription, E-Mail, Meldungen an die Admins über Slack, Zeitstempel, Rechenknecht, Web "
                   "Services); eigener KI-Zugang (experimentell).",
     "AI functions: the AI help for categorising open answers sends answer texts of participants to OpenAI (then a "
     "further processor); only after a note in the consent and a confirmation per survey. Described more precisely, "
     "as the platform already works: backups, access to the administration, server logs and further services "
     "(transcription, email, notices to the admins via Slack, timestamps, compute node, web services); own AI access "
     "(experimental).", (2, 3, 6)),
    ("2026-10-09", "Lizenzen: Quelle der Wetter-Demo (Open-Meteo, CC BY 4.0); im Docker-Betrieb die Bibliotheken "
                   "gunicorn und packaging.",
     "Licences: source of the weather demo (Open-Meteo, CC BY 4.0); in the Docker set-up the libraries gunicorn and "
     "packaging.", (7,)),
    ("2026-10-08", "Konstrukte: Skalen aus Zeitschriftenartikeln ohne eigene Bedingungen (etwa TAM und KUT) nur für "
                   "nichtkommerzielle Forschung und Lehre; Arbeitsübersetzungen in jeder Sprache keine autorisierten "
                   "Übersetzungen; bei Veröffentlichungen über Zenodo oder Coscine gilt deren Lizenz nicht für die "
                   "Wortlaute, Skalen aus APA PsycTests lässt die Plattform weg.",
     "Constructs: scales from journal articles without terms of their own (such as TAM and KUT) only for "
     "non-commercial research and teaching; working translations in any language are no authorised translations; "
     "when publishing via Zenodo or Coscine, its licence does not cover the wordings, and the platform leaves out "
     "scales from APA PsycTests.", (4, 7)),
    ("2026-10-06", "Gemeinsame Auswertung: Hinweis auf den Absatz für die Einwilligung unter Einstellungen › "
                   "Einwilligung und Datenschutz.",
     "Pooled analysis: pointer to the paragraph for the consent under Settings › Consent and data protection.", (4,)),
    ("2026-10-05", "Interviewleitfäden: Löschen, Aufbewahrungsfristen ab Ende des Interviews und Anonymität von "
                   "Interviews; Sicherungskopien der Datenbank nach 30 Tagen gelöscht; Element „Standort“.",
     "Interview guides: deletion, retention periods from the end of the interview and anonymity of interviews; "
     "database backups deleted after 30 days; element “Location”.", (2, 3)),
)


def changes_since(accepted):
    """The entries of CHANGES newer than the version a person accepted, and whether older changes are left out
    (the person accepted a version before the first entry). None accepted (a new account): nothing."""
    if not accepted:
        return [], False
    newer = [c for c in CHANGES if c[0] > accepted]
    return newer, bool(newer) and accepted < CHANGES[-1][0]


def _inline(line):
    out = escape(line)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", str(out))
    out = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2" target="_blank" rel="noopener">\1</a>', out)
    # a page of this site (a path from the root, never "//host"): in the same tab
    return re.sub(r"\[([^\]]+)\]\((/(?!/)[^)\s]*)\)", r'<a href="\2">\1</a>', out)


def render_md(md):
    """Headings, paragraphs (lines joined), bullet lists with continuation lines, bold, links."""
    html, para, items = [], [], []

    def flush():
        if para:
            html.append("<p>" + " ".join(_inline(l) for l in para) + "</p>")
            para.clear()
        if items:
            html.append("<ul>" + "".join("<li>" + " ".join(_inline(l) for l in it) + "</li>" for it in items) + "</ul>")
            items.clear()

    for raw in md.splitlines():
        line = raw.strip()
        if not line:
            flush()
        elif line.startswith("#"):
            flush()
            level = min(len(line) - len(line.lstrip("#")), 3)
            text = line.lstrip("#").strip()
            number = re.match(r"(\d+)\.\s", text)  # "## 7. Lizenzen": the target of the links "Neu in Version" (X69)
            anchor = f' id="abschnitt-{number.group(1)}"' if number and level == 2 else ""
            html.append(f"<h{level}{anchor}>{_inline(text)}</h{level}>")
        elif line.startswith("- "):
            if para:
                flush()
            items.append([line[2:]])
        elif items and raw.startswith("  "):
            items[-1].append(line)
        else:
            if items:
                flush()
            para.append(line)
    flush()
    return Markup("\n".join(html))


def needs_acceptance(user):
    return user.is_authenticated and user.terms_version != version()


def gate():
    """before_request: logged-in researchers must accept the current terms first."""
    endpoint = request.endpoint or ""
    if not needs_acceptance(current_user) or any(endpoint == e or endpoint.startswith(e) for e in OPEN_ENDPOINTS):
        return None
    return redirect(url_for("terms.show", next=request.full_path.rstrip("?") if request.method == "GET" else None))


@bp.route("/", methods=["GET", "POST"])
def show():
    if request.method == "POST":
        if not current_user.is_authenticated:
            abort(403)
        if request.form.get("accept") != "1":
            return redirect(url_for("terms.show", error=1, next=request.form.get("next") or None))
        current_user.terms_version = version()
        current_user.terms_accepted_at = utcnow()
        db.session.commit()
        nxt = request.form.get("next", "")
        return redirect(local_path(nxt) or url_for("dashboard.index"))
    pending = needs_acceptance(current_user)
    changes, older = changes_since(current_user.terms_version) if pending else ([], False)
    return render_template("terms.html", body=render_md(source()), version=version(), pending=pending,
                           changes=changes, older_changes=older, nxt=request.args.get("next", ""),
                           error=bool(request.args.get("error")))
