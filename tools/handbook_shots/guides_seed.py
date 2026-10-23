"""The demo state for the pictures of the page "Anleitungen" (app/data/handbook/<lang>/guides.md), called by seed.py.

Panel study: the template "Panelbefragung mit Quoten" gets a panel connection (PID stored once and required, a
provider parameter `source` with the values a and b, completion links and a rule on the made-up host panel.example),
is set running, and its dummy participations become clearly made-up "real" ones from the two providers (PIDs
P100001 …), so that the quota state, its provider filter and the provider links have numbers. A read-only quota
link and a field-report dashboard for provider a are made.

Seminar: a second copy of the customer-satisfaction template with own links for made-up people ("Anna Beispiel",
"Ben Muster", "Clara Probe", "Kurs Biologie") and a dashboard link each; some participations are put on the links.

Longitudinal study: three waves of a small survey with the element "Persönlicher Code" and a scale, 120 to 150
made-up persons per wave who give the same code each time, linked under "Wellen" in the research plan and computed
("Längsschnitt rechnen") in the seed.

Everything stays on this machine; no AI service, no mail.
"""
import copy
import random
from datetime import timedelta

PANEL = {
    "base": "https://panel.example/return",
    "rule": "https://panel-b.example/end",
}
SEMINAR = {
    "title": {"de": "Mensa-Befragung im Seminar", "en": "Canteen survey in the seminar"},
    "names": {"de": "Anna Beispiel\nBen Muster\nClara Probe\nKurs Biologie",
              "en": "Anna Beispiel\nBen Muster\nClara Probe\nKurs Biologie"},
}
WAVES = {
    "title": {"de": "Studienverlauf – Welle {n}", "en": "Course of studies – wave {n}"},
    "label": {"de": ["Semesterbeginn", "Semestermitte", "Semesterende"], "en": ["start of term", "mid-term", "end of term"]},
    "question": {"de": "Wie sehr stimmen Sie den folgenden Aussagen über Ihr Studium zu?",
                 "en": "To what extent do you agree with the following statements about your studies?"},
    "statements": {"de": ["Ich studiere gern.", "Mein Studium macht mir Freude.", "Ich bin mit meinem Studium zufrieden.",
                          "Ich würde mein Fach wieder wählen."],
                   "en": ["I enjoy studying.", "My studies give me pleasure.", "I am satisfied with my studies.",
                          "I would choose my subject again."]},
    "agree5": {"de": ["stimme überhaupt nicht zu", "stimme eher nicht zu", "teils/teils", "stimme eher zu",
                      "stimme voll und ganz zu"],
               "en": ["strongly disagree", "disagree", "neither agree nor disagree", "agree", "strongly agree"]},
    "description": {"de": "Wir begleiten Studierende durch ein Semester und fragen dreimal nach ihrer Studienzufriedenheit.",
                    "en": "We follow students through one term and ask three times about their satisfaction with "
                          "their studies."},
}
PASTE = ("Complete link: https://panel.example/return?status=complete&PID=\n"
         "Screenout link: https://panel.example/return?status=screenout&PID=\n"
         "Overquota link: https://panel.example/return?status=overquota&PID=\n"
         "Quality link: https://panel.example/return?status=quality&PID=\n"
         "Duplicate link: https://panel.example/return?status=duplicate&PID=\n"
         "Speeder link: https://panel.example/return?status=speeder&PID=")


def _running(survey, days=4):
    """Set running as "Befragung starten" leaves it (lifecycle.start), without the checks: the screenshots need a
    survey in the field with participations."""
    from app.models import Survey, utcnow

    survey.status = Survey.RUNNING
    survey.archived = False
    survey.opened_at = utcnow() - timedelta(days=days)
    survey.data_pool = {**(survey.data_pool or {}), "sources": ["real"]}


def _as_real(survey, rows, params_of, days=4):
    """Make dummy participations look like real ones from the field: source "real", no preview, the given URL
    parameters, completed over the last days. Clearly made-up values only (PIDs P100001 …)."""
    from sqlalchemy.orm.attributes import flag_modified

    from app.models import utcnow

    now = utcnow()
    for n, r in enumerate(rows):
        r.source, r.is_preview = "real", False
        r.params = {**(r.params or {}), **params_of(n)}
        flag_modified(r, "params")
        if r.completed_at:
            shift = timedelta(hours=(n * 7) % (days * 24))
            took = r.completed_at - r.started_at if r.started_at else timedelta(minutes=5)
            r.completed_at = now - shift
            r.started_at = r.completed_at - took


def build(app, client, uid, lang, out):
    """Adds what the guide pictures need to `out` (seed.json); returns the ids of the surveys made or changed here."""
    from flask_login import login_user

    from app.extensions import db
    from app.models import User

    made = set()
    out["paste"] = PASTE
    with app.test_request_context():
        login_user(db.session.get(User, uid))
        made.add(panel_study(app, client, uid, lang, out))
    out["seminar"] = seminar(app, client, uid, lang)
    made.add(out["seminar"]["id"])
    out["waves"] = waves(app, client, uid, lang)
    made.update(w["id"] for w in out["waves"])
    return made


def panel_study(app, client, uid, lang, out):
    from app import quotas
    from app.extensions import db
    from app.models import QuotaShare, Response, Survey

    survey = db.session.get(Survey, out["panelbefragung"]["id"])
    link = PANEL["base"] + "?status={s}&PID={{{{PID}}}}"
    survey.panel = {**(survey.panel or {}),
                    "params": [{"name": "PID", "mode": "store", "required": True, "allowed": [], "once": True},
                               {"name": "source", "mode": "store", "required": True, "allowed": ["a", "b"]}],
                    "redirects": {"completed": link.format(s="complete"), "screenout": link.format(s="screenout"),
                                  "quotafull": link.format(s="overquota"), "duplicate": link.format(s="duplicate"),
                                  "excluded": "", "declined": "", "closed": ""},
                    "modes": {},
                    "rules": [{"outcome": "quotafull", "item": "p:source", "op": "eq", "value": "b",
                               "url": PANEL["rule"] + "?st=qf&uid={{PID}}", "mode": "now"}],
                    "quota_placeholder": False, "multi": False}
    _running(survey)
    rows = list(db.session.scalars(db.select(Response).filter_by(survey_id=survey.id, source="dummy")
                                   .order_by(Response.id)))
    _as_real(survey, rows, lambda n: {"PID": f"P{100001 + n}", "source": "b" if n % 3 == 2 else "a"})
    db.session.commit()
    quotas.recount(survey)
    db.session.commit()
    code = survey.code
    # a read-only quota link for provider a (Ablauf › Quoten › Links)
    label = "Panelanbieter A" if lang == "de" else "Panel provider A"
    r = client.post(f"/surveys/{code}/flow/quotas/link", data={"qp": "source=a", "label": label, "valid": "30"})
    assert r.status_code in (302, 303), r.status_code
    share = db.session.scalar(db.select(QuotaShare).filter_by(survey_id=survey.id).order_by(QuotaShare.id.desc()))
    out["panelbefragung"]["quota_link"] = f"/q/{share.token}" if share else None
    # a field report dashboard; its designer offers the provider link form
    r = client.post(f"/surveys/{code}/dashboards/new", data={"template": "field", "name": ""})
    assert r.status_code in (302, 303), r.status_code
    out["panelbefragung"]["board"] = int(r.headers["Location"].rstrip("/").split("/")[-1])
    return survey.id


def seminar(app, client, uid, lang):
    from app import study_templates
    from app.extensions import db
    from app.models import Response, Survey

    r = client.post("/surveys/template/kundenzufriedenheit", data={"title": SEMINAR["title"][lang]})
    assert r.status_code in (302, 303), r.status_code
    with app.app_context():
        survey = db.session.scalar(db.select(Survey).filter_by(created_by_id=uid, title=SEMINAR["title"][lang]))
        code, sid = survey.code, survey.id
    r = client.post(f"/surveys/{code}/value-links", data={"names": SEMINAR["names"][lang], "param": "w", "board": "1"})
    assert r.status_code in (302, 303), r.status_code
    with app.test_request_context():
        from app import panel

        survey = db.session.get(Survey, sid)
        values = next(p for p in panel.config(survey)["params"] if p["name"] == "w")["allowed"]
        share = [12, 9, 4, 17]  # participations per link: Anna, Ben, Clara, the biology course
        plan = [v for v, k in zip(values, share) for _ in range(k)]
        rows = list(db.session.scalars(db.select(Response).filter_by(survey_id=sid, source="dummy")
                                       .order_by(Response.id)))[:len(plan)]
        _as_real(survey, rows, lambda n: {"w": plan[n]}, days=6)
        _running(survey, days=6)
        db.session.commit()
    return {"code": code, "id": sid}


def _wave_file(lang, n):
    from app import items, study_templates

    data = study_templates.load("kundenzufriedenheit")
    s = data["survey"]
    s.update(title=WAVES["title"][lang].format(n=n), default_language=lang, languages=[lang], title_translations={},
             project="", description=WAVES["description"][lang], research_question="")
    s["study_plan"] = {"goal": "describe", "design": "panel", "hypotheses": [], "sample": {"target": 120,
                       "recruitment": "convenience", "note": ""}, "rules": "", "deviations": []}
    consent = copy.deepcopy(data["blocks"][0])
    t = consent["items"][0]["texts"]
    t[lang] = dict(t.pop("de"), question="Einwilligung" if lang == "de" else "Consent",
                   agree="Weiter" if lang == "de" else "Next",
                   disagree="Ich möchte nicht teilnehmen" if lang == "de" else "I do not want to take part")
    code_item = {"id": 2, "type": "personal_code", "code": "pcode", "answer_mode": "required",
                 "config": {"parts": ["mother_first", "birth_day", "father_first", "older_siblings", "birthplace_first"],
                            "export_parts": False},
                 "texts": {lang: items.get("personal_code").default_texts(lang)}}
    scale = {"id": 3, "type": "likert", "code": "ZUFR", "answer_mode": "nudge",
             "config": {"points": 5, "anchors": "agree", "labeling": "full", "label_display": "once", "randomize": False,
                        "level": "metric", "dont_know": False,
                        "statements": [{"id": f"s{k + 1}", "code": f"ZUFR_{k + 1}", "reverse": False} for k in range(4)]},
             "texts": {lang: {"question": WAVES["question"][lang], "labels": WAVES["agree5"][lang],
                              "statements": {f"s{k + 1}": x for k, x in enumerate(WAVES["statements"][lang])}}}}
    data["blocks"] = [consent,
                      {"id": 2, "titles": {lang: "Code"}, "show_title": False, "randomize_items": False, "settings": {},
                       "items": [code_item]},
                      {"id": 3, "titles": {lang: "Block 2"}, "show_title": False, "randomize_items": False, "settings": {},
                       "items": [scale]}]
    data["flow"] = {"t": "root", "children": [{"t": "block", "block": b} for b in (1, 2, 3)]}
    data["quotas"] = []
    return data


def waves(app, client, uid, lang):
    """Three waves with the same 160 made-up persons (each with a fixed code), some missing per wave, satisfaction
    falling a little over the term."""
    from flask_login import login_user
    from sqlalchemy.orm.attributes import flag_modified

    from app import dummy, panel_models, transfer, waves as waves_mod
    from app.extensions import db
    from app.models import Response, Survey, User

    rng = random.Random(21)
    letters = "ABCDEFGHIJKLMNOPRSTW"
    persons = []
    for _ in range(160):
        persons.append({"code": {"mother_first": rng.choice(letters), "birth_day": f"{rng.randint(1, 28):02d}",
                                 "father_first": rng.choice(letters), "older_siblings": str(rng.choice([0, 0, 1, 1, 2, 3])),
                                 "birthplace_first": rng.choice(letters)},
                        "level": rng.gauss(0, 0.8), "slope": rng.gauss(-0.25, 0.2)})
    made = []
    with app.test_request_context():
        user = db.session.get(User, uid)
        login_user(user)
        for n in (1, 2, 3):
            survey, _ = transfer.create(_wave_file(lang, n), user, WAVES["title"][lang].format(n=n))
            db.session.commit()
            who = [p for k, p in enumerate(persons) if rng.random() < (0.95, 0.85, 0.78)[n - 1]]
            dummy.generate(survey, len(who), seed=40 + n)
            db.session.commit()
            items = {i.code: i for b in survey.blocks for i in b.items}
            rows = list(db.session.scalars(db.select(Response).filter_by(survey_id=survey.id, source="dummy")
                                           .order_by(Response.id)))
            for r, p in zip(rows, who):
                answers = dict(r.answers or {})
                answers[str(items["pcode"].id)] = dict(p["code"])
                answers[str(items["ZUFR"].id)] = {
                    f"s{k + 1}": max(1, min(5, round(3.6 + p["level"] + p["slope"] * (n - 1) + rng.gauss(0, 0.6))))
                    for k in range(4)}
                r.answers = answers
                flag_modified(r, "answers")
            db.session.commit()
            made.append({"code": survey.code, "id": survey.id, "block": survey.blocks[1].id,
                         "label": WAVES["label"][lang][n - 1]})
        first = db.session.get(Survey, made[0]["id"])
        problem = waves_mod.save(first, user, [str(w["id"]) for w in made], [w["label"] for w in made])
        if problem:
            print("waves:", problem)
        db.session.commit()
        names = [v["name"] for v in panel_models.candidates(user, first)]
        if not names:
            print("waves: no variable over the waves")
        for w in made:
            s = db.session.get(Survey, w["id"])
            s.waves = {**(s.waves or {}), "model": {"vars": names[:1]}}
        db.session.commit()
    return made


def compute_waves(app, uid, out):
    """"Längsschnitt rechnen" for the waves, after everything else has changed them (the result is kept under the
    fingerprints of all waves, so it has to be the last change)."""
    from flask_login import login_user

    from app import panel_models
    from app.extensions import db
    from app.models import Survey, User

    with app.test_request_context():
        user = db.session.get(User, uid)
        login_user(user)
        last = db.session.get(Survey, out["waves"][-1]["id"])
        state = panel_models.start(user, last, background=False)
        db.session.commit()
        print("waves: Längsschnitt", state)
