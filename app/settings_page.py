"""The settings page of a survey: seven groups, each folded to one line that says what is set (concept
"Einstellungen: Bestandsaufnahme und Gliederung", 2026-10-03).

summaries() gives that line per group, consent_mentions() what the consent names at the moment and where it is
set. Both are drawn on the page (surveys/detail.html) and drawn anew after every save (data-live)."""
from collections.abc import Sequence

from flask import url_for

from app.lang import de_en, english

# the groups in the order of the page (anchor, heading); the setup wizard asks in the same order
# the study plan has a page of its own since 2026-10-06 (study_plan.page); summaries() still gives its line for it
GROUPS_DE = (("befragung", "Befragung"), ("beteiligte", "Beteiligte Forschende"), ("teilnahme", "Teilnahme"),
             ("einwilligung", "Einwilligung und Datenschutz"),
             ("abschlusstexte", "Endseiten"), ("gestaltung", "Erscheinungsbild"))
GROUPS_EN = {"befragung": "Survey", "studienplan": "Research plan", "beteiligte": "Researchers involved",
             "teilnahme": "Participation", "einwilligung": "Consent and data protection", "abschlusstexte": "End pages",
             "gestaltung": "Appearance"}


class _Groups(Sequence):
    """GROUPS with the headings in the language of the page, read when the page is drawn (the anchors stay)."""

    def __getitem__(self, i):
        key, heading = GROUPS_DE[i]
        return (key, GROUPS_EN[key] if english() else heading)

    def __len__(self):
        return len(GROUPS_DE)


GROUPS = _Groups()
# groups with fields that are fixed from the start on, because the consent names them (or, for the look, like
# the questionnaire); their head carries the lock
LOCKING = {"befragung", "teilnahme", "einwilligung", "gestaltung"}


def _plural(n, one, many):
    """`one` and `many` are (German, English) pairs."""
    word = one if n == 1 else many
    return f"{n} {de_en(*word)}"


def basic_summaries(survey):
    """The lines every kind of survey shares (title and languages, researchers, appearance), also for a live poll
    and an interview guide (concept "Befragungstypen im Vergleich" 2026-10-17, T12)."""
    from app import theme
    from app.surveys import LANGUAGE_NAMES

    out = {}
    langs = [LANGUAGE_NAMES.get(code, code) for code in survey.languages or ["de"]]
    out["befragung"] = [(survey.title, True), (", ".join(langs), False)]
    if len(langs) > 1:
        out["befragung"].append((de_en("Sprachwahl an", "language choice on") if survey.language_choice
                                 else de_en("Sprache nach Browser", "language from the browser"), False))

    members = [m for m in survey.members if not m.is_former]
    names = [(m.display_name or "").split()[-1] for m in members[:3] if m.display_name]
    who = [(_plural(len(members), ("Person", "person"), ("Personen", "people")), True)]
    if names:
        who.append(("(" + ", ".join(names) + (", …" if len(members) > 3 else "") + ")", False))
    if survey.group_links:
        who.append((_plural(len(survey.group_links), ("Gruppe", "group"), ("Gruppen", "groups"))
                    + de_en(" mit Zugriff", " with access"), False))
    out["beteiligte"] = who

    look = theme.of(survey)
    # the colour scheme only: the image style is set elsewhere and was noise here (Ralf, 2026-10-23)
    out["gestaltung"] = [(theme.name(look), True)]
    return out


def summaries(survey):
    """{group: [(text, strong), ...]}: the line in the head of each folded group. `strong` (what differs from the
    standard) is no longer drawn in bold: next to plain parts it looked random (Ralf, 2026-10-03)."""
    from app import panel, retention, study_plan

    out = basic_summaries(survey)
    st = study_plan.status(survey)
    plan = []
    if st["hypotheses"]:
        plan.append((_plural(st["hypotheses"], ("Hypothese", "hypothesis"), ("Hypothesen", "hypotheses"))
                     + de_en(", {n} gemessen", ", {n} measured", n=st["measured"]), True))
    if st["plan"]["design"]:
        plan.append((study_plan.designs()[st["plan"]["design"]], False))
    if st["target"]:
        plan.append((de_en("Ziel {n} Abschlüsse", "target {n} completions", n=st["target"]), False))
    out["studienplan"] = plan or [(de_en("noch nicht begonnen", "not begun yet") if not survey.research_question
                                   else de_en("Forschungsfrage", "research question"), False)]

    cfg = panel.config(survey)
    mode = de_en("beliebig oft", "any number of times") if cfg["multi"] else (
        de_en("einmal, danach gesperrt", "once, then locked") if survey.repeat_marker
        else de_en("einmal je Sitzung", "once per session"))
    out["teilnahme"] = [(de_en("Zurück erlaubt", "back allowed") if survey.allow_back else de_en("ohne Zurück", "no back"), False)]
    if survey.show_progress:
        out["teilnahme"].append((de_en("Fortschritt", "progress"), False))
    if survey.auto_advance:
        out["teilnahme"].append((de_en("automatisch weiter", "auto-advance"), False))
    if survey.sampling is None:  # a diary study runs in the app: no browser lock (app/diary.py)
        out["teilnahme"].append((mode, True))

    consent = []
    item = survey.consent_item
    if item is not None:
        c = item.config or {}
        if c.get("min_age"):
            consent.append((de_en("ab {n}", "from {n}", n=c["min_age"]), False))
        if c.get("ethics"):
            consent.append((de_en("Ethikvotum", "ethics approval"), False))
        if (item.t(survey.default_language) or {}).get("compensation"):
            consent.append((de_en("Vergütung", "compensation"), False))
        if c.get("open_data"):
            consent.append(("Open Data", False))
    else:
        consent.append((de_en("ohne Einwilligung", "no consent"), False))
    if survey.erase_code:
        consent.append((de_en("Löschcode", "deletion code"), False))
    if survey.geo_ip:
        consent.append((de_en("IP-Schätzung", "IP estimate"), False))
    label = dict(retention.answers()).get(retention.of(survey)["answers"]) if retention.of(survey)["answers"] else None
    consent.append((de_en("Daten {period}", "data {period}", period=label) if label
                    else de_en("ohne Löschfrist", "no deletion period"), True))
    if survey.admin_hidden:
        consent.append((de_en("nicht in der Admin-Übersicht", "not in the admin overview"), False))
    out["einwilligung"] = consent

    links = []
    if cfg["params"]:
        links.append((_plural(len(cfg["params"]), ("Parameter", "parameter"), ("Parameter", "parameters")), True))
        links.append(("(" + ", ".join(p["name"] for p in cfg["params"][:3]) + (", …" if len(cfg["params"]) > 3 else "") + ")", False))
    n_links = sum(1 for url in cfg["redirects"].values() if url)
    if n_links:
        links.append((_plural(n_links, ("Abschlusslink", "completion link"), ("Abschlusslinks", "completion links")),
                      not cfg["params"]))
    if cfg["rules"]:
        links.append((_plural(len(cfg["rules"]), ("Regel", "rule"), ("Regeln", "rules")), False))
    if panel.sign_config(survey)["key"]:
        links.append((de_en("signiert", "signed"), False))
    out["panel"] = links or [(de_en("nicht genutzt", "not used"), False)]

    own = [key for key, text in ((survey.end_texts or {}).get(survey.default_language) or {}).items() if (text or "").strip()]
    out["abschlusstexte"] = [(_plural(len(own), ("eigener Text", "own text"), ("eigene Texte", "own texts")), True),
                             (de_en("sonst Standardtexte", "otherwise standard texts"), False)] if own \
        else [(de_en("Standardtexte", "standard texts"), False)]

    return out


def consent_mentions(survey):
    """[(what, where, url)]: what the consent names at the moment because of a setting, and where that is set."""
    from app import panel, theme
    from app.items import consent as consent_mod

    here = url_for("surveys.detail", survey_id=survey.id)
    quality = url_for("surveys.quality", survey_id=survey.id) + "#prevent"  # "Datenqualität steigern" is on Qualitätssicherung (2026-10-12, E6)
    flow = url_for("flow_page.index", survey_id=survey.id)
    out = []
    if survey.erase_code:
        out.append((de_en("Löschcode", "deletion code"), "", ""))
    if survey.retention:
        out.append((de_en("Aufbewahrungsfristen", "retention periods"), "", ""))
    if survey.geo_ip:
        out.append((de_en("Land und Bundesland aus der IP-Adresse", "country and federal state from the IP address"), "", ""))
    collection, flow_name = de_en("Datenerhebung", "Data collection"), de_en("Ablauf", "Flow")
    qa_name = de_en("Qualitätssicherung", "Quality assurance")
    if survey.repeat_marker:
        out.append((de_en("Cookie gegen erneute Teilnahme", "cookie against taking part again"),
                    de_en("Teilnahme", "Participation"), here + "#teilnahme"))
    stored = panel.stored_names(survey)
    if stored:
        out.append((de_en("gespeicherte Kennung aus dem Link ({names})", "stored identifier from the link ({names})",
                          names=", ".join(stored)), collection, url_for("surveys.field", survey_id=survey.id) + "#panel"))
    if survey.paradata:
        out.append((de_en("technische Angaben", "technical details"), qa_name, quality))
    if survey.page_timing:
        out.append((de_en("Zeit je Seite", "time per page"), qa_name, quality))
    if survey.focus_paste:
        out.append((de_en("Fensterwechsel und Einfügen", "window switches and pasting"), qa_name, quality))
    if survey.ai_bait:
        out.append((de_en("Köder für KI-Agenten", "bait for AI agents"), qa_name, quality))
    if consent_mod._has_split(survey):
        out.append((de_en("getrennter Abschnitt für Kontaktangaben", "separate section for contact details"), flow_name, flow))
    if consent_mod._external_hosts(survey):
        out.append((de_en("externer Abschnitt ({hosts})", "external section ({hosts})",
                          hosts=", ".join(consent_mod._external_hosts(survey))), flow_name, flow))
    if consent_mod._webservice_hosts(survey):
        out.append((de_en("Web Service ({hosts})", "Web Service ({hosts})",
                          hosts=", ".join(consent_mod._webservice_hosts(survey))), flow_name, flow))
    # the names in the language of the page (UI/UX audit 2026-10-09, X6); the pool is the provider's (X84)
    names = consent_mod._construct_names(survey, de_en("de", "en"))
    if names:  # the fixed paragraph of the consent (Consent.pool_notice, licence.md section 4)
        out.append((de_en("gemeinsame Auswertung standardisierter Fragebögen ({names}) durch den Betreiber",
                          "pooled analysis of standardised questionnaires ({names}) by the provider of Askurious "
                          "(see legal notice)", names=", ".join(names)),
                    de_en("Fragebogen", "Questionnaire"), url_for("editor.index", survey_id=survey.id)))
    if theme.records_scheme(survey):
        out.append((de_en("helle oder dunkle Darstellung", "light or dark appearance"), de_en("Erscheinungsbild", "Appearance"),
                    here + "#gestaltung"))
    return out
