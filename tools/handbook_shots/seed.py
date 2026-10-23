"""Builds the demo state for the handbook screenshots in a fresh database, through the app itself.

    python tools/handbook_shots/seed.py <work folder> <de|en>

One researcher, "Maria Muster" (a made-up name with a made-up ORCID iD), with her researcher pages in the given
language. Surveys made from the study templates get their titles in that language, as they would for her; the demo
surveys of this script (learning app, live poll, interview guide, crowd) carry their questions in that language too.
The study templates have German questions only, so their participant pages are German in both runs.

Nothing here calls an AI service: where the handbook shows AI results (translations "AI, unchecked"), the app's own
code stores them from the fixed demo texts below instead of an answer of the model. Writes <work folder>/seed.json
with the codes and ids the screenshot driver needs.
"""
import copy
import json
import os
import random
import sys
from datetime import timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import shotapp  # noqa: E402

ORCID = "0000-0000-0000-0028"  # fictional; checksum-valid format, no real person
NAME = "Maria Muster"
AFFILIATION = {"de": "Institut für Kommunikationswissenschaft, Beispiel-Universität",
               "en": "Institute of Communication Science, Example University"}

# ---- the demo survey "learning app" (replaces the TAM template, whose scales need a licence) ----------------------
TEXTS = {
    "title": {"de": "Lernen mit KI im Studium", "en": "Learning with AI at university"},
    "description": {"de": "Wir untersuchen, wie Studierende KI-Lernhilfen für die Prüfungsvorbereitung beurteilen und "
                          "ob sie sie nutzen würden.",
                    "en": "We study how students judge AI study aids for exam preparation and whether they would use "
                          "them."},
    "question": {"de": "Was bestimmt, ob Studierende eine KI-Lernhilfe nutzen wollen?",
                 "en": "What determines whether students want to use an AI study aid?"},
    "h1": {"de": "Je nützlicher die KI-Lernhilfe erscheint, desto höher die Nutzungsabsicht.",
           "en": "The more useful the AI study aid seems, the higher the intention to use it."},
    "h2": {"de": "Studierende verschiedener Fächer unterscheiden sich in ihrer Nutzungsabsicht.",
           "en": "Students of different subjects differ in their intention to use it."},
    "nutzen_q": {"de": "Wie sehr stimmen Sie den folgenden Aussagen über die KI-Lernhilfe zu?",
                 "en": "To what extent do you agree with the following statements about the AI study aid?"},
    "nutzen": {"de": ["Die KI-Lernhilfe spart mir Zeit beim Lernen.",
                      "Mit der KI-Lernhilfe verstehe ich den Stoff besser.",
                      "Die KI-Lernhilfe verbessert meine Prüfungsergebnisse.",
                      "Die KI-Lernhilfe ist für mein Studium nützlich."],
               "en": ["The AI study aid saves me time when studying.",
                      "With the AI study aid I understand the material better.",
                      "The AI study aid improves my exam results.",
                      "The AI study aid is useful for my studies."]},
    "absicht_q": {"de": "Und wie sehr stimmen Sie diesen Aussagen zu?",
                  "en": "And to what extent do you agree with these statements?"},
    "absicht": {"de": ["Ich habe vor, die KI-Lernhilfe im nächsten Semester zu nutzen.",
                       "Ich werde die KI-Lernhilfe für die nächste Prüfung verwenden.",
                       "Ich würde die KI-Lernhilfe meinen Kommilitoninnen und Kommilitonen empfehlen."],
                "en": ["I intend to use the AI study aid next semester.",
                       "I will use the AI study aid for my next exam.",
                       "I would recommend the AI study aid to my fellow students."]},
    "agree5": {"de": ["stimme überhaupt nicht zu", "stimme eher nicht zu", "teils/teils", "stimme eher zu",
                      "stimme voll und ganz zu"],
               "en": ["strongly disagree", "disagree", "neither agree nor disagree", "agree", "strongly agree"]},
    "fach_q": {"de": "Was studieren Sie?", "en": "What do you study?"},
    "fach": {"de": ["Geistes- und Sozialwissenschaften", "Natur- und Ingenieurwissenschaften", "Medizin",
                    "Wirtschaftswissenschaften", "anderes Fach"],
             "en": ["Humanities and social sciences", "Natural sciences and engineering", "Medicine", "Economics",
                    "another subject"]},
    "wunsch_q": {"de": "Was müsste eine KI-Lernhilfe können, damit Sie sie nutzen?",
                 "en": "What would an AI study aid have to do for you to use it?"},
    "block1": {"de": "Einschätzung", "en": "Assessment"},
    "block2": {"de": "Wissen über KI", "en": "Knowledge about AI"},
    "block3": {"de": "Zur Person", "en": "About you"},
    "project": {"de": "Lehrforschungsprojekt Medienwirkung", "en": "Teaching research project on media effects"},
    "consent_q": {"de": "Einwilligung", "en": "Consent"},
    "agree": {"de": "Weiter", "en": "Next"},
    "disagree": {"de": "Ich möchte nicht teilnehmen", "en": "I do not want to take part"},
}

# ---- the interview guide ---------------------------------------------------------------------------------------
GUIDE = {
    "title": {"de": "Alltagsmobilität älterer Menschen", "en": "Everyday mobility of older adults"},
    "question": {"de": "Wie gestalten Menschen ab 70 ihre Wege im Alltag, und was hindert sie daran?",
                 "en": "How do people aged 70 and over organise their everyday journeys, and what holds them back?"},
    # the template's guide questions with the topic filled in, in their order
    "questions": {
        "de": ["Zum Einstieg: Erzählen Sie doch einmal, wie ein ganz normaler Tag bei Ihnen aussieht und welche Wege "
               "Sie dabei machen.",
               "Wie erleben Sie Ihre Wege zum Einkaufen, zum Arzt oder zu Freunden? Erzählen Sie doch einmal.",
               "Wenn Sie an Bus und Bahn denken: Was fällt Ihnen dazu ein?",
               "Wir sind am Ende angekommen. Gibt es noch etwas, das Ihnen zu Ihren Wegen im Alltag wichtig ist?"],
        "en": ["To begin with: tell me what a quite ordinary day looks like for you and which journeys you make.",
               "How do you experience your journeys to the shops, to the doctor or to friends? Tell me about it.",
               "When you think of buses and trains: what comes to mind?",
               "We have come to the end. Is there anything else about your everyday journeys that matters to you?"]},
    "topics": {"de": ["Einstieg", "Wege im Alltag", "Bus und Bahn", "Abschluss"],
               "en": ["Opening", "Everyday journeys", "Buses and trains", "Closing"]},
    "collect": {"de": "Welche Wege machen Sie an einem normalen Tag?\nWas tun Sie, wenn das Wetter schlecht ist?\n"
                      "Wer hilft Ihnen, wenn Sie nicht selbst fahren können?\nWas hat sich in den letzten Jahren "
                      "verändert?",
                "en": "Which journeys do you make on an ordinary day?\nWhat do you do when the weather is bad?\n"
                      "Who helps you when you cannot drive yourself?\nWhat has changed in recent years?"},
}

# ---- the live poll ---------------------------------------------------------------------------------------------
LIVE = {
    "title": {"de": "Vorlesungsfeedback: Methoden der empirischen Forschung",
              "en": "Lecture feedback: Methods of empirical research"},
    "language": {"de": "de-du", "en": "en"},
    "words": {"de": ["Beispiele"] * 5 + ["Regression"] * 5 + ["Tempo"] * 4 + ["Residuen"] * 4 + ["R-Code"] * 3
                    + ["Grafiken"] * 2 + ["Interaktion", "verständlich", "verwirrend"],
              "en": ["examples"] * 5 + ["regression"] * 5 + ["pace"] * 4 + ["residuals"] * 4 + ["R code"] * 3
                    + ["plots"] * 2 + ["interaction", "clear", "confusing"]},
    "better": {"de": ["mehr Übungen", "Pause in der Mitte", "Folien vorher hochladen", "langsamer bei den Formeln"],
               "en": ["more exercises", "a break in the middle", "upload slides beforehand", "slower on the formulas"]},
    "assistant": {"de": "Methodenvorlesung, 120 Studierende im dritten Semester, heute Regression. Ich möchte wissen, "
                        "ob das Tempo passt, was unklar blieb und ob die Übung geholfen hat.",
                  "en": "Methods lecture, 120 students in their third semester, today regression. I would like to know "
                        "whether the pace is right, what stayed unclear and whether the exercise helped."},
}

CROWD = {
    "title": {"de": "Feedback mit Menschenmenge (Realtime-Delphi)", "en": "Feedback with a crowd (real-time Delphi)"},
    "question": {"de": "Wie wahrscheinlich ist es, dass Sie in fünf Jahren mit einem selbstfahrenden Bus fahren?",
                 "en": "How likely is it that you will ride in a self-driving bus in five years?"},
    "low": {"de": "sehr unwahrscheinlich", "en": "very unlikely"},
    "high": {"de": "sehr wahrscheinlich", "en": "very likely"},
}

SCALE_IDEA = {"de": "Wie sehr Menschen einem Pflegeroboter zutrauen, sie im Alltag zuverlässig zu unterstützen",
              "en": "How much people trust a care robot to support them reliably in everyday life"}


def pick(texts, lang):
    return texts[lang]


def main(work_dir, lang):
    other = "en" if lang == "de" else "de"
    app = shotapp.make_app(work_dir)
    app.config["WTF_CSRF_ENABLED"] = False  # only this seeding process; the server checks CSRF as always
    from flask_login import login_user

    from app import dummy, transfer
    from app.extensions import db
    from app.models import Survey, User

    client = app.test_client()
    client.get(f"/auth/dev-login?orcid={ORCID}&name={NAME.replace(' ', '%20')}")
    with app.app_context():
        user = db.session.scalar(db.select(User).filter_by(orcid=ORCID))
        user.display = dict(user.display or {}, lang=lang)
        user.affiliation = AFFILIATION[lang]
        user.setup_due = False
        db.session.commit()
        uid = user.id
    client.post("/terms/", data={"accept": "1"})
    out = {"lang": lang, "orcid": ORCID, "name": NAME,
           # what the driver types into AI dialogs without sending them
           "fill": {"guide_collect": GUIDE["collect"][lang], "live_assistant": LIVE["assistant"][lang],
                    "scale_idea": SCALE_IDEA[lang]}}

    # the six study templates, as "Aus einer Vorlage" makes them (titles in the researcher's language)
    from app import study_templates

    with app.test_request_context():
        login_user(db.session.get(User, uid))
        titles = {k: study_templates.TEMPLATES[k][0] for k in study_templates.TEMPLATES}
    order = ["technikakzeptanz", "conjoint", "experiment", "vignetten", "panelbefragung", "kundenzufriedenheit"]
    for key in order:
        if key == "technikakzeptanz":
            continue  # its scales need a documented permission; the learning-app survey below takes its place
        r = client.post(f"/surveys/template/{key}", data={"title": titles[key]})
        assert r.status_code in (302, 303), (key, r.status_code)
    with app.app_context():
        for s in db.session.scalars(db.select(Survey).filter_by(created_by_id=uid)):
            for key in order:
                if s.title == titles.get(key):
                    out[key] = {"code": s.code, "id": s.id}

    out["learn"] = build_learning_survey(app, client, uid, lang, other)
    out["crowd"] = build_crowd(app, client, uid, lang)
    out["guide"] = build_guide(app, client, uid, lang)
    out["live"] = build_live(app, client, uid, lang)
    polish(app, uid, lang)
    with app.test_request_context():
        # the conjoint survey uses AI images in the style "Tusche & Aquarell" (settings, "Bildstil")
        db.session.get(Survey, out["conjoint"]["id"]).image_style = {"key": "watercolor", "custom": ""}
        db.session.commit()
        login_user(db.session.get(User, uid))
        # the learning-app survey fully translated, its own two scales still "AI, unchecked"
        fake_translate(db.session.get(Survey, out["learn"]["id"]), other, learning_mapping(lang, other),
                       unchecked=("NUTZEN", "ABSICHT"))
        # the customer satisfaction survey gets English and a half-done translation (the page "Übersetzung")
        kz = db.session.get(Survey, out["kundenzufriedenheit"]["id"])
        kz.languages = ["de", "en"]
        db.session.commit()
        fake_translate(kz, "en", KZ_EN, unchecked=("zufriedenheit",))
    import guides_seed  # the pictures of the page "Anleitungen" (guides.md)

    made = guides_seed.build(app, client, uid, lang, out)
    polish(app, uid, lang, only=made)
    guides_seed.compute_waves(app, uid, out)

    with app.app_context():  # the ids of blocks and elements per survey, for addresses like /editor/items/<id>
        for entry in out.values():
            if isinstance(entry, dict) and "id" in entry:
                survey = db.session.get(Survey, entry["id"])
                entry["blocks"] = [b.id for b in survey.blocks]
                entry["items"] = {i.code: i.id for b in survey.blocks for i in b.items}
                entry["types"] = {i.type: i.id for b in reversed(survey.blocks) for i in reversed(b.items)}
                entry["block_of"] = {i.code: b.id for b in survey.blocks for i in b.items}
    with open(os.path.join(work_dir, "seed.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    print(json.dumps(out, ensure_ascii=False))


def _likert(item_id, code, question, statements, labels):
    return {"id": item_id, "type": "likert", "code": code, "answer_mode": "nudge",
            "config": {"points": 5, "anchors": "agree", "labeling": "full", "label_display": "once", "randomize": True, "level": "metric",
                       "statements": [{"id": f"s{n + 1}", "code": f"{code}_{n + 1}", "reverse": False}
                                      for n in range(len(statements[0][1]))], "dont_know": False},
            "texts": {lg: {"question": question[lg], "labels": labels[lg],
                           "statements": {f"s{n + 1}": t for n, t in enumerate(sts)}} for lg, sts in statements}}


def build_learning_survey(app, client, uid, lang, other):
    """A cross-sectional survey with two own scales, a validated scale from "Konstrukte" (FITAI, CC BY 4.0), age and
    gender, a research plan with two hypotheses, 120 dummy participations and a half-done translation."""
    from flask_login import login_user

    from app import dummy, study_templates, transfer
    from app.extensions import db
    from app.models import Survey, User

    data = study_templates.load("technikakzeptanz")  # the structure of a survey file; its contents are replaced
    s = data["survey"]
    s.update(title=TEXTS["title"][lang], title_translations={other: TEXTS["title"][other]},
             project=TEXTS["project"][lang], description=TEXTS["description"][lang],
             research_question=TEXTS["question"][lang], default_language=lang, languages=[lang, other])
    plan = s["study_plan"]
    plan["hypotheses"][0].update(text=TEXTS["h1"][lang], iv="NUTZEN", dv="ABSICHT", analysis="r", effect=0.5,
                                 basis_note="")
    plan["sample"]["target"] = 160
    plan["hypotheses"][1].update(text=TEXTS["h2"][lang], kind="difference", direction="", iv="fach", dv="ABSICHT",
                                 basis_note="")
    plan["rules"] = ""
    consent = copy.deepcopy(data["blocks"][0])
    t = consent["items"][0]["texts"]
    t[lang] = dict(t.pop("de"), question="Einwilligung" if lang == "de" else "Consent",
                   agree="Weiter" if lang == "de" else "Next",
                   disagree="Ich möchte nicht teilnehmen" if lang == "de" else "I do not want to take part")
    consent["titles"] = {lang: t[lang]["question"]}
    both = [(lang, TEXTS["nutzen"][lang])]
    nutzen = _likert(2, "NUTZEN", TEXTS["nutzen_q"], both, TEXTS["agree5"])
    absicht = _likert(3, "ABSICHT", TEXTS["absicht_q"], [(lang, TEXTS["absicht"][lang])], TEXTS["agree5"])
    fach = {"id": 4, "type": "single_choice", "code": "fach", "answer_mode": "optional",
            "config": {"options": [{"id": f"o{n + 1}", "code": str(n + 1)} for n in range(5)], "layout": "vertical"},
            "texts": {lang: {"question": TEXTS["fach_q"][lang],
                             "options": {f"o{n + 1}": o for n, o in enumerate(TEXTS["fach"][lang])}}}}
    wunsch = {"id": 5, "type": "text_input", "code": "wunsch", "answer_mode": "optional",
              "config": {"multiline": True, "max_length": 2000, "validation": "none", "min": None, "max": None, "pattern": ""},
              "texts": {lang: {"question": TEXTS["wunsch_q"][lang]}}}
    data["blocks"] = [consent,
                      {"id": 2, "titles": {lang: TEXTS["block1"][lang]}, "show_title": False, "randomize_items": False,
                       "settings": {}, "items": [nutzen, absicht]},
                      {"id": 3, "titles": {lang: TEXTS["block2"][lang]}, "show_title": False, "randomize_items": False,
                       "settings": {}, "items": []},
                      {"id": 4, "titles": {lang: TEXTS["block3"][lang]}, "show_title": False, "randomize_items": False,
                       "settings": {}, "items": [fach, wunsch]}]
    data["flow"] = {"t": "root", "children": [{"t": "block", "block": n} for n in (1, 2, 3, 4)]
                    + [{"t": "split", "id": "s1"}]}
    with app.test_request_context():
        login_user(db.session.get(User, uid))
        survey, notes = transfer.create(data, db.session.get(User, uid), TEXTS["title"][lang])
        study_templates._fill_consent(survey)
        db.session.commit()
        sid, code = survey.id, survey.code
        blocks = {b.position: b.id for b in survey.blocks}
    # the validated scale and the demographics from "Konstrukte", as the palette adds them
    for key, block in (("fitai", blocks[2]), ("age", blocks[3]), ("gender", blocks[3])):
        r = client.post(f"/surveys/{code}/editor/blocks/{block}/items", data={"type": f"construct:{key}"})
        assert r.status_code in (302, 303), (key, r.status_code, r.get_data(as_text=True)[:300])
    with app.test_request_context():
        login_user(db.session.get(User, uid))
        survey = db.session.get(Survey, sid)
        survey.created_at = survey.created_at - timedelta(days=3)
        db.session.commit()
        dummy.generate(survey, 120, seed=7)
        db.session.commit()
        believable(survey)
    return {"code": code, "id": sid}


def believable(survey):
    """Dummy answers draw every value at random: ages from 11 to 120 and a knowledge test whose items do not hang
    together. For the screenshots the students are 19 to 34, and each answers the FITAI items by one level of
    knowledge (the foils reversed, as the scale scores them)."""
    from sqlalchemy.orm.attributes import flag_modified

    from app.extensions import db
    from app.models import Response

    rng = random.Random(3)
    items = {i.code: i for b in survey.blocks for i in b.items}
    age = items.get("age")
    fitai = next((i for i in items.values() if (i.config or {}).get("construct", {}).get("key") == "fitai"), None)
    for r in db.session.scalars(db.select(Response).filter_by(survey_id=survey.id, source="dummy")):
        answers = dict(r.answers or {})
        if age is not None and str(age.id) in answers:
            answers[str(age.id)] = rng.choice([19, 20, 20, 21, 21, 22, 22, 23, 23, 24, 25, 26, 27, 29, 31, 34])
        if fitai is not None and isinstance(answers.get(str(fitai.id)), dict):
            theta = rng.gauss(0, 1)
            row = {}
            for st in fitai.config.get("statements") or []:
                lean = -theta if st.get("reverse") else theta
                row[st["id"]] = max(1, min(5, round(3 + 1.0 * lean + rng.gauss(0, 0.9))))
            answers[str(fitai.id)] = row
        r.answers = answers
        flag_modified(r, "answers")
    db.session.commit()


def fake_translate(survey, target, mapping, unchecked=()):
    """A translation as the button "Alles Offene mit KI übersetzen" stores it (translations.translate_next), with the
    demo texts in `mapping` (source -> target) standing in for the model's answer, so no request leaves the machine.
    Texts not in `mapping` stay open. Afterwards a person has confirmed every AI text except those of the elements
    whose codes are in `unchecked`: they stay "AI, unchecked"."""
    from app import ai, translations
    from app.extensions import db
    from app.models import TranslationMark

    def fake_generate_text(instructions, prompt, **kwargs):
        entries = json.loads(prompt.split("Texts (JSON):\n", 1)[1])
        return json.dumps({e["id"]: mapping[e["text"]] for e in entries if e["text"] in mapping})

    real = ai.generate_text
    ai.generate_text = fake_generate_text
    try:
        for _ in range(5):
            done, left = translations.translate_next(survey, target)
            if not left:
                break
    except ai.AIError:
        pass
    finally:
        ai.generate_text = real
    keep = {f"item:{i.id}" for b in survey.blocks for i in b.items if i.code in unchecked}
    for m in db.session.scalars(db.select(TranslationMark).filter_by(survey_id=survey.id, lang=target)):
        if m.ai and m.unit not in keep:
            m.ai = False
    db.session.commit()
    left = translations.pending(survey, target)
    if left:
        print(f"  {survey.title}: {len(left)} texts left untranslated:",
              [f["source"][:40] for u, f in left][:12])


def learning_mapping(source, target):
    mapping = {}
    for value in TEXTS.values():
        a, b = value.get(source), value.get(target)
        if isinstance(a, list):
            mapping.update(zip(a, b))
        elif a:
            mapping[a] = b
    return mapping


# the customer satisfaction template, German -> English, for the page "Übersetzung": what the AI translated
KZ_EN = {"Wie wahrscheinlich ist es, dass Sie die Mensa weiterempfehlen?":
         "How likely is it that you would recommend the canteen?",
         "sehr unwahrscheinlich": "very unlikely", "sehr wahrscheinlich": "very likely",
         "Wie sehr stimmen Sie den folgenden Aussagen zu?": "To what extent do you agree with the following statements?",
         "stimme überhaupt nicht zu": "strongly disagree", "stimme eher nicht zu": "disagree",
         "teils/teils": "neither agree nor disagree", "stimme eher zu": "agree", "stimme voll und ganz zu": "strongly agree",
         "Das Essen schmeckt mir.": "I like the food.", "Die Preise sind angemessen.": "The prices are reasonable.",
         "Die Wartezeit ist kurz.": "The waiting time is short.",
         "Es gibt genug vegetarische Gerichte.": "There are enough vegetarian dishes.",
         "Einwilligung": "Consent", "Weiter": "Next", "Ich möchte nicht teilnehmen": "I do not want to take part"}


GUIDE_DETAILS = {
    # per guide question: aspects to tick off, maintenance questions, specific questions
    "de": [(["Tagesablauf", "regelmäßige Wege", "Verkehrsmittel"], ["Wie ging es dann weiter?", "Erzählen Sie ruhig mehr."],
            ["Seit wann ist das so?"]),
           (["Einkaufen", "Arztbesuche", "Besuche bei Freunden"],
            ["Können Sie ein Beispiel nennen?", "Wie ging es dann weiter?", "Wie war das für Sie?"],
            ["Wer begleitet Sie auf diesen Wegen?"]),
           (["Haltestelle", "Fahrplan", "Sicherheit"],
            ["Können Sie ein Beispiel nennen?", "Wie ging es dann weiter?", "Wie war das für Sie?"],
            ["Wie kommen Sie zur Haltestelle?"]),
           ([], [], [])],
    "en": [(["daily routine", "regular journeys", "means of transport"], ["What happened next?", "Do tell me more."],
            ["Since when has it been like that?"]),
           (["shopping", "visits to the doctor", "visiting friends"],
            ["Can you give an example?", "What happened next?", "How was that for you?"],
            ["Who goes with you on these journeys?"]),
           (["bus stop", "timetable", "safety"], ["Can you give an example?", "What happened next?", "How was that for you?"],
            ["How do you get to the bus stop?"]),
           ([], [], [])],
}
GUIDE_HINTS = {"de": ["Nicht unterbrechen, Pausen aushalten.", "", "", "Dank; Aufnahme beenden; Postskript direkt im Anschluss."],
               "en": ["Do not interrupt; allow pauses.", "", "", "Thanks; stop the recording; postscript right afterwards."]}
GUIDE_OPENING = {"de": "Begrüßung und Dank. Ablauf, Dauer und Tonaufnahme erklären, Fragen klären, Einwilligung einholen. "
                       "Die Aufnahme erst nach der Einwilligung starten.",
                 "en": "Welcome and thanks. Explain the procedure, duration and audio recording, answer questions, obtain "
                       "consent. Start the recording only after consent."}


def build_guide(app, client, uid, lang):
    """An interview guide after Witzel's template, with its questions on the everyday mobility of older adults."""
    from app.extensions import db
    from app.items.base import new_id
    from app.models import Survey

    r = client.post("/guide/new", data={"title": GUIDE["title"][lang], "language": lang, "template": "witzel",
                                        "research_question": GUIDE["question"][lang], "target": "erwachsene",
                                        "mode": "praesenz", "order": "free"})
    assert r.status_code in (302, 303), r.status_code
    with app.app_context():
        survey = db.session.scalar(db.select(Survey).filter_by(created_by_id=uid, title=GUIDE["title"][lang]))
        guide_blocks = [b for b in survey.blocks if any(i.type == "guide_question" for i in b.items)]
        for b, topic in zip(guide_blocks, GUIDE["topics"][lang]):
            b.titles = {lang: topic}
        n, opened = 0, False
        for b in survey.blocks:
            for item in b.items:
                if item.type == "info" and not opened:
                    opened = True
                    item.texts = {lang: {**(item.texts or {}).get(lang, {}), "question": GUIDE_OPENING[lang]}}
                if item.type != "guide_question" or n >= len(GUIDE["questions"][lang]):
                    continue
                aspects, keep, probe = GUIDE_DETAILS[lang][n]
                config = dict(item.config or {})
                texts = {"question": GUIDE["questions"][lang][n], "hint": GUIDE_HINTS[lang][n]}
                for key, rows in (("aspects", aspects), ("keep", keep), ("probe", probe)):
                    old = list(config.get(key) or [])
                    dicts = bool(old) and isinstance(old[0], dict)
                    ids = [(row.get("id") if dicts else row) for row in old][:len(rows)]
                    while len(ids) < len(rows):
                        ids.append(new_id())
                    config[key] = [{"id": i} for i in ids] if dicts else ids
                    texts[key] = dict(zip(ids, rows))
                item.config = config
                item.texts = {lang: texts}
                n += 1
        db.session.commit()
        return {"code": survey.code, "id": survey.id, "first_question": GUIDE["questions"][lang][0]}


def build_live(app, client, uid, lang):
    """A live poll from the template "Feedback zur Sitzung" with 26 answers in its current run."""
    from werkzeug.datastructures import MultiDict

    from app.extensions import db
    from app.live.base import state
    from app.models import Response, Survey, new_pid, utcnow

    r = client.post("/presenter/new", data={"title": LIVE["title"][lang], "language": LIVE["language"][lang],
                                             "template": "feedback"})
    assert r.status_code in (302, 303), r.status_code
    with app.test_request_context():
        survey = db.session.scalar(db.select(Survey).filter_by(created_by_id=uid, title=LIVE["title"][lang]))
        st = state(survey)
        questions = [i for b in survey.blocks for i in b.items]
        rating, pace, words, better = questions[:4]
        lo, hi = rating.kind.bounds(rating)
        stars = [5] * 11 + [4] * 9 + [3] * 3 + [2] * 2 + [1]
        options = [o["code"] for o in pace.kind.options(pace, survey.default_language)]
        paces = [options[0]] * 3 + [options[1]] * 17 + [options[2]] * 6
        cloud = list(LIVE["words"][lang])
        for n in range(26):
            form = MultiDict({f"i{rating.id}": str(int(lo) + stars[n] - 1), f"i{pace.id}": paces[n],
                              f"i{words.id}": cloud[n % len(cloud)], f"i{better.id}": LIVE["better"][lang][n % 4]})
            answers = {}
            for item in (rating, pace, words, better):
                try:
                    value = item.kind.parse_answer(item, form, survey.default_language)
                except Exception as e:  # noqa: BLE001
                    print("live answer", item.type, e)
                    continue
                if value not in (None, "", []):
                    answers[str(item.id)] = value
            db.session.add(Response(survey_id=survey.id, language=survey.default_language, status=Response.COMPLETED,
                                    answers=answers, pid=new_pid(survey.code), started_at=utcnow(),
                                    completed_at=utcnow(), source="real",
                                    params={"live_run": st["run"]["id"], "live_run_name": st["run"]["name"]}))
        db.session.commit()
        return {"code": survey.code, "id": survey.id, "items": [i.id for i in questions]}


def build_crowd(app, client, uid, lang):
    """A small survey with a rating question and the crowd feedback after it, 50 dummy participations."""
    from flask_login import login_user

    from app import dummy, study_templates, transfer
    from app.extensions import db
    from app.models import Survey, User

    data = study_templates.load("kundenzufriedenheit")
    s = data["survey"]
    s.update(title=CROWD["title"][lang], default_language=lang, languages=[lang], title_translations={},
             project="", description="", research_question="")
    s["study_plan"] = None
    consent = copy.deepcopy(data["blocks"][0])
    t = consent["items"][0]["texts"]
    t[lang] = dict(t.pop("de"), question="Einwilligung" if lang == "de" else "Consent",
                   agree="Weiter" if lang == "de" else "Next",
                   disagree="Ich möchte nicht teilnehmen" if lang == "de" else "I do not want to take part")
    rating = {"id": 2, "type": "rating", "code": "bus", "answer_mode": "optional",
              "config": {"style": "numbers", "points": 7, "min": 1.0, "max": 7.0, "step": 1, "show_value": False,
                         "reverse": False, "dont_know": False},
              "texts": {lang: {"question": CROWD["question"][lang], "left": CROWD["low"][lang],
                               "right": CROWD["high"][lang]}}}
    data["blocks"] = [consent, {"id": 2, "titles": {lang: "Block 1"}, "show_title": False, "randomize_items": False,
                                "settings": {}, "items": [rating]}]
    data["flow"] = {"t": "root", "children": [{"t": "block", "block": 1}, {"t": "block", "block": 2}]}
    with app.test_request_context():
        login_user(db.session.get(User, uid))
        survey, notes = transfer.create(data, db.session.get(User, uid), CROWD["title"][lang])
        if notes:
            print("crowd notes:", notes)
        db.session.commit()
        code, sid = survey.code, survey.id
        block = survey.blocks[1].id
        rating_id = survey.blocks[1].items[0].id
    r = client.post(f"/surveys/{code}/editor/blocks/{block}/items", data={"type": "sample_crowd"})
    assert r.status_code in (302, 303), r.status_code
    with app.test_request_context():
        survey = db.session.get(Survey, sid)
        crowd = next(i for i in survey.blocks[1].items if i.type == "sample_crowd")
        crowd.config = {**crowd.config, "sources": [rating_id], "subscales": [None], "avatars": 40}
        db.session.commit()
        dummy.generate(survey, 50, seed=5)
        db.session.commit()
        return {"code": code, "id": sid, "block": block}


def polish(app, uid, lang, only=None):
    """What a careful researcher fills in: a contact address and the institution in every consent, and the duration
    from the questionnaire, so the design check shows only what the screenshots are about."""
    from app import duration
    from app.extensions import db
    from app.models import Survey

    with app.test_request_context():
        for survey in db.session.scalars(db.select(Survey).filter_by(created_by_id=uid)):
            if only is not None and survey.id not in only:
                continue
            for m in survey.members:
                if hasattr(m, "affiliation") and not (m.affiliation or "").strip():
                    m.affiliation = AFFILIATION[lang]
            consent = next((i for b in survey.blocks for i in b.items if i.type == "consent"), None)
            if consent is None:
                continue
            config = dict(consent.config or {}, contact_email="maria.muster@example.org")
            try:
                config["duration"] = max(2, round(duration.minutes(duration.estimate(survey)["typical"])))
            except Exception as e:  # noqa: BLE001
                print("duration", survey.title, e)
            consent.config = config
            texts = dict(consent.texts or {})
            for lg in survey.languages:  # the standard text anew, with the duration just set
                texts[lg] = {**texts.get(lg, {}), "body": consent.kind.template_body(consent, lg)}
            consent.texts = texts
        db.session.commit()


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "de")
