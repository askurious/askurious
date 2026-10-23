"""Study templates: six typical studies to start from, each with its study plan, a questionnaire and dummy data, so
the results can be read at once (concept "Askurious für Studierende und Berufseinsteiger", package C).

The files in app/data/vorlagen/ are survey files (app/transfer.py) built by tests/build_templates.py through the app
itself; a new survey is read in from one like any survey file, then gets DUMMIES dummy participations. The dummy
answers are random: a hypothesis comes out as "nicht unterstützt" as often as not, which the results say.
"""
import copy
import json
from collections.abc import Mapping
from pathlib import Path

from flask import abort, flash, redirect, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.lang import de_en, english
from app.study_plan import bp

FOLDER = Path(__file__).resolve().parent / "data" / "vorlagen"
DUMMIES = 120
# key: (title, goal, what it shows)
TEMPLATES_DE = {
    "kundenzufriedenheit": ("Kundenzufriedenheit mit Weiterempfehlung", "describe",
                            "Weiterempfehlung (0 bis 10), Zufriedenheit als Likert-Skala, eine Gruppenfrage, eine offene Frage"),
    "technikakzeptanz": ("Technikakzeptanz (TAM)", "correlation",
                         "validierte Skalen aus dem Pool: Nützlichkeit, Bedienbarkeit, Nutzungsabsicht, Technikbereitschaft"),
    "experiment": ("Online-Experiment mit zwei Bedingungen", "causal",
                   "Randomisierung im Ablauf: je Person einer von zwei Texten, danach dieselbe Einstellungsskala"),
    "conjoint": ("Conjoint für ein Produkt", "preference",
                 "drei Eigenschaften mit zwei bis drei Stufen, Versuchsplan, Teilnutzen und Simulator in den Ergebnissen"),
    "vignetten": ("Vignettenstudie mit 2 × 3 Faktoren", "causal",
                  "Schleife mit Faktoren und Stufen: je Person mehrere kurze Beschreibungen, jede mit derselben Frage"),
    # UI/UX audit 2026-10-05, V25: what a commissioned study needs
    "panelbefragung": ("Panelbefragung mit Quoten", "describe",
                       "Kennung des Panels aus dem Link, Abschlusslinks für abgeschlossen, Screen-out und Quote voll, "
                       "Quoten nach Alter, Geschlecht und Bundesland"),
}
# title and what it shows in English (researcher pages in English, app/lang.py)
TEMPLATES_EN = {
    "kundenzufriedenheit": ("Customer satisfaction with recommendation",
                            "Recommendation (0 to 10), satisfaction as a Likert scale, a group question, an open question"),
    "technikakzeptanz": ("Technology acceptance (TAM)",
                         "validated scales from the pool: usefulness, ease of use, intention to use, technology readiness"),
    "experiment": ("Online experiment with two conditions",
                   "Randomisation in the flow: one of two texts per person, then the same attitude scale"),
    "conjoint": ("Conjoint for a product",
                 "three attributes with two to three levels, experimental design, part-worths and simulator in the results"),
    "vignetten": ("Vignette study with 2 × 3 factors",
                  "Loop with factors and levels: several short descriptions per person, each with the same question"),
    "panelbefragung": ("Panel survey with quotas",
                       "Panel identifier from the link, completion links for completed, screen-out and quota full, "
                       "quotas by age, gender and federal state"),
}


class _Templates(Mapping):
    """TEMPLATES read in the language of the page: {key: (title, goal, what it shows)} (keys and goals stay)."""

    dummies = DUMMIES  # for the page: how many dummy participations a template brings

    def __getitem__(self, key):
        title, goal, shows = TEMPLATES_DE[key]
        if english():
            title, shows = TEMPLATES_EN[key]
        return (title, goal, shows)

    def __iter__(self):
        return iter(TEMPLATES_DE)

    def __len__(self):
        return len(TEMPLATES_DE)


TEMPLATES = _Templates()


def load(key):
    if key not in TEMPLATES:
        abort(404)
    return json.loads((FOLDER / f"{key}.json").read_text(encoding="utf-8"))


def fill_instrument(item, key):
    """The wording of a scale of others into a template item: the files in app/data/vorlagen/ carry only its
    structure, the wording comes from the installation (constructs.INSTRUMENTS). False without it."""
    from app import constructs

    if key not in constructs.INSTRUMENTS:
        return True
    spec = constructs._instrument_spec(key)
    langs = list((item.get("texts") or {}).keys())
    if spec is None or not all(lang in spec["texts"] for lang in langs):
        return False
    item["texts"] = {lang: copy.deepcopy(spec["texts"][lang]) for lang in langs}
    meta = item["config"]["construct"]
    if isinstance(meta.get("template"), dict):
        meta["template"]["texts"] = copy.deepcopy(spec["texts"])
    return True


def strip_instruments(data):
    """The template without the wording of scales of others, as it is stored (tests/build_templates.py)."""
    from app import constructs

    for block in data.get("blocks") or []:
        for item in block.get("items") or []:
            key = ((item.get("config") or {}).get("construct") or {}).get("key")
            if key in constructs.INSTRUMENTS:
                item["texts"] = _blank(item.get("texts"))
                template = item["config"]["construct"].get("template")
                if isinstance(template, dict):
                    template["texts"] = _blank(template.get("texts"))
    return data


def _blank(texts):
    return {lang: {**t, "question": "", "statements": {s: "" for s in t.get("statements") or {}}}
            for lang, t in (texts or {}).items()}


def without_restricted(data):
    """The template without the instruments whose wording needs a documented permission (constructs.NOT_SHARED,
    PR #1187), which a new survey can never have yet (the permission names the study): their elements go, and the
    hypotheses that use them. Returns (data, [names of the instruments left out]) (Ralf, 2026-10-17, choice A)."""
    from app import constructs

    removed, codes = [], set()
    for block in data.get("blocks") or []:
        kept = []
        for item in block.get("items") or []:
            meta = (item.get("config") or {}).get("construct") or {}
            if meta.get("key") in constructs.NOT_SHARED or not fill_instrument(item, meta.get("key")):
                codes.add(item.get("code"))
                found = constructs.find(meta.get("key"))
                name = found.name if found is not None else meta.get("key")
                if name not in removed:
                    removed.append(name)
            else:
                kept.append(item)
        block["items"] = kept
    plan = (data.get("survey") or {}).get("study_plan")
    if codes and isinstance(plan, dict):
        plan["hypotheses"] = [h for h in plan.get("hypotheses") or [] if h.get("iv") not in codes and h.get("dv") not in codes]
    return data, removed


def _fill_consent(survey):
    """The consent of a new survey from a template: the standard text with the creator as the responsible person
    and the duration from the estimate. The templates carry no text of their own, so whoever leaves the setup wizard
    early still shows participants a full consent, not an empty one (UI/UX audit 2026-10-16, 1)."""
    from app import duration

    consent = next((i for b in survey.blocks for i in b.items if i.type == "consent"), None)
    if consent is None:
        return
    config = dict(consent.config or {})
    # at least 2: the standard text says "etwa {n} Minuten" in every language, and a template is never shorter
    config["duration"] = max(2, round(duration.minutes(duration.estimate(survey)["typical"])))
    if not config.get("researchers") and current_user.is_authenticated:
        from app.items.consent import user_refs

        config["researchers"] = user_refs(current_user)
    consent.config = config
    texts = dict(consent.texts or {})
    for lang in survey.languages:
        lt = dict(texts.get(lang, {}))
        if not (lt.get("body") or "").strip():
            lt["body"] = consent.kind.template_body(consent, lang)
        texts[lang] = lt
    consent.texts = texts


@bp.route("/template/<key>", methods=["POST"])
@login_required
def from_template(key):
    """A new draft from a study template, with its dummy data."""
    from app import auth, dummy, transfer

    from app import kind_access

    if not kind_access.may_create(current_user, "survey"):  # a template becomes a questionnaire
        abort(403)
    data, left_out = without_restricted(load(key))
    # a title of its own, typed before (UI/UX audit 2026-10-07, W16); the template's name when the field is empty
    title = " ".join((request.form.get("title") or "").split())[:200] or TEMPLATES[key][0]
    n, base = 2, title
    while transfer.twin_of(current_user, title) is not None:  # a template taken twice: "… (2)"
        title, n = f"{base} ({n})", n + 1
    try:
        survey, _notes = transfer.create(data, current_user, title)
    except transfer.Refused as e:
        # a template with an instrument that needs a documented permission (constructs.PERMISSION_REQUIRED, PR #1187):
        # refused before anything is written, a notice instead of an error page
        db.session.rollback()
        flash(str(e), "error")
        return redirect(url_for("wizard.new"))
    _fill_consent(survey)
    dummy.generate(survey, DUMMIES)
    db.session.commit()
    from markupsafe import Markup, escape

    text = de_en("„{title}“ ist als Entwurf angelegt, mit Studienplan, Fragebogen und {n} Dummy-Teilnahmen. Die "
                 "Antworten auf die Variablen der Hypothesen folgen dem Studienplan (erwarteter Effekt), alles andere ist "
                 "zufällig. Die Ergebnisse zeigen also, was Sie angenommen haben, nicht was herauskäme.",
                 "“{title}” has been created as a draft, with research plan, questionnaire and {n} dummy participations. "
                 "Answers to the variables of the hypotheses follow the research plan (expected effect), everything else "
                 "is random. So the results show what you assumed, not what would come out.",
                 title=survey.title, n=DUMMIES)
    if left_out:
        text += " " + de_en("Ohne {names}: Der Wortlaut dieser Skalen ist nicht allgemein freigegeben; er braucht eine "
                            "dokumentierte Berechtigung des Betreibers für Ihre Studie, und die Hypothesen dazu fehlen deshalb.",
                            "Without {names}: the wording of these scales is not generally released; it needs a documented "
                            "permission of the operator for your study, so the hypotheses on them are missing too.",
                            names=", ".join(left_out))
    # the benefit over an empty start is the filled results: one click there (pre-test 2026-10-16)
    link = Markup(' <a href="{}">{}</a>').format(url_for("surveys.results", survey_id=survey.id),
                                                 de_en("Ergebnisse mit den Dummy-Daten ansehen", "See the results with the dummy data"))
    flash(escape(text) + link, "success")
    # on through the setup wizard from the step "Studienplan": description, consent and languages come next (W16)
    return redirect(url_for("wizard.step", survey_id=survey.id, key="studienplan"))
