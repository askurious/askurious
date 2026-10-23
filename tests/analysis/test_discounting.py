"""Delay discounting (app/items/discounting.py): open to every researcher, the server replays
the choices, k and AUC, the quick five-question procedure."""
import json
import math

from app.extensions import db
from app.models import Item, Response, Survey
from tests.conftest import CONSENT_OK, add, fill_consent

E = "/surveys/1/editor"


def _dd(app, client, **settings):
    fill_consent(client)
    assert add(client, "discounting").status_code == 302                          # item 2
    if settings:
        with app.app_context():
            item = db.session.get(Item, 2)
            item.config = {**item.config, **settings}
            db.session.commit()


def _choices_for(k, amount=100, delays=(7, 30, 182, 365, 1826), steps=6):
    """What a participant with hyperbolic k would choose in the adjusting-amount procedure."""
    out = ""
    for d in delays:
        now, step = amount / 2, amount / 4
        value = amount / (1 + k * d)
        for _ in range(steps):
            x = "L" if value > now else "S"
            out += x
            now += step if x == "L" else -step
            step /= 2
    return out


def test_open_without_a_tick(app, client, survey):
    """No longer experimental (Ralf, 2026-10-10): in the palette for everyone, with no feature switched on."""
    assert "Delay Discounting" in client.get(E + "/").data.decode()
    assert add(client, "discounting").status_code == 302
    assert client.get(E + "/items/2").status_code == 200


def test_k_is_recovered_from_the_choices(app, client, survey):
    _dd(app, client)
    with app.app_context():
        item = db.session.get(Item, 2)
        for k in (0.001, 0.01, 0.1):
            s = item.kind.scores(item, {"c": _choices_for(k)})
            assert abs(math.log(s["k"]) - math.log(k)) < 0.25, (k, s)
            assert 0 < s["auc"] < 1
        patient, impatient = (item.kind.scores(item, {"c": _choices_for(k)}) for k in (0.0005, 0.5))
        assert patient["auc"] > impatient["auc"] and patient["ip_5y"] > impatient["ip_5y"]


def test_participant_flow_and_export(app, client, survey):
    _dd(app, client)
    with app.app_context():
        db.session.get(Item, 2).answer_mode = "required"
        db.session.commit()
    client.post("/surveys/1/start", data={})
    with app.app_context():
        url = f"/s/{db.session.get(Survey, 1).code}"
    p = app.test_client()
    p.get(url)
    p.post(url, data=CONSENT_OK)
    html = p.get(url).data.decode()
    assert "data-dd-task" in html and '"total": 30' in html
    assert p.post(url, data={"nav": "next", "i2": json.dumps({"c": "LS" * 14, "rt": [900] * 28})}).status_code == 200  # too few choices
    assert p.post(url, data={"nav": "next", "i2": json.dumps({"c": _choices_for(0.01), "rt": [900] * 30})}).status_code == 302
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(is_preview=False))
        assert r.answers["2"]["c"] == _choices_for(0.01)
    csv = client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig")
    header = csv.splitlines()[0]
    assert all(c in header for c in ("_k", "_lnk", "_auc", "_ip_1w", "_ip_5y", "_choices"))


def test_quick_procedure(app, client, survey):
    from app.items.discounting import LADDER, QUICK_START, ladder_days, replay

    _dd(app, client, mode="quick")
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.total(item) == 5 and len(LADDER) == 33
        trials, ed50 = replay("quick", 100, [], 6, "SSSSS")
        assert trials[0] == (50, ladder_days(QUICK_START)) and trials[-1][1] == ladder_days(QUICK_START - 15)
        assert ed50 == math.sqrt(ladder_days(QUICK_START - 15) * ladder_days(QUICK_START - 16))  # below the last delay
        patient = item.kind.scores(item, {"c": "LLLLL"})
        assert patient["ed50"] > ladder_days(QUICK_START + 15) and patient["k"] < item.kind.scores(item, {"c": "SSSSS"})["k"]
        assert item.kind._parse(item, json.dumps({"c": "LLLL"})) is None
        cols = [c for c, _ in item.kind.export_columns(item)]
        assert cols == [f"{item.code}_{k}" for k in ("ed50", "k", "lnk", "choices")]


def test_switching_to_quick_keeps_the_delays(app, client, survey):
    _dd(app, client)
    with app.app_context():
        code = db.session.get(Item, 2).code
    client.post(E + "/items/2", data={"code": code, "question": "Was wäre Ihnen lieber?", "mode": "quick", "amount": "1000", "steps": "6"})
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.config["mode"] == "quick" and item.config["amount"] == 1000 and len(item.config["delays"]) == 5


def test_quick_fifth_choice_decides_between_two_values():
    """Math audit 2026-10-05, N-1: the fifth choice of the quick procedure has an effect: ED50 is the geometric mean of
    the fifth delay and its neighbour in the chosen direction (Koffarnus & Bickel, 2014); 32 patterns, 32 values."""
    import itertools

    from app.items.discounting import replay

    ed50 = {"".join(p): replay("quick", 100, [], 6, "".join(p))[1] for p in itertools.product("LS", repeat=5)}
    assert len(set(ed50.values())) == 32
    hours = 1 / 24
    assert math.isclose(ed50["SSSSL"], math.sqrt(2 * hours * 3 * hours))    # 0.102 d (before: 0.0833 like SSSSS)
    assert math.isclose(ed50["SSSSS"], math.sqrt(2 * hours * 1 * hours))    # 0.059 d
    assert math.isclose(ed50["LLLLS"], math.sqrt(25 * 365.25 * 15 * 365.25))  # 7073 d (before: 9131 like LLLLL)
    assert ed50["LLLLL"] > 25 * 365.25                                        # beyond the longest delay asked
    assert ed50["SSSSS"] < ed50["SSSSL"] < ed50["SSSLS"] and ed50["LLLLS"] < ed50["LLLLL"]


def test_fit_k_is_not_capped_at_the_old_grid():
    """N-4: the search range of log k is wide (1e-9 to 1e5) and refined by golden section: all "now" at 1 hour and 1
    day gave k = 100 (edge), all "later" at 10 and 100 years k = 1e-6 (edge)."""
    from app.items.discounting import fit_k, small

    assert math.isclose(fit_k([1 / 24, 1], [1 / 128, 1 / 128]), 2928.5, rel_tol=1e-4)
    tiny = fit_k([3652.5, 36525], [1 - 1 / 512] * 2)
    assert math.isclose(tiny, 5.838e-8, rel_tol=1e-3)
    assert small(tiny) == 5.838e-08 and small(0.0123456) == 0.012346        # the export never rounds k to 0
    v = [1 / (1 + 0.0123 * d) for d in (7, 30, 182, 365, 1826)]
    assert math.isclose(fit_k([7, 30, 182, 365, 1826], v), 0.0123, rel_tol=1e-5)


def test_nonsystematic_data_after_johnson_and_bickel(app, client, survey):
    """N-3: the two criteria of Johnson & Bickel (2008) as columns _jb1/_jb2, in the codebook, in the method draft and
    as one line with the number of persons on the results page."""
    from app.items.discounting import nonsystematic

    assert nonsystematic([0.9, 0.7, 0.5, 0.3, 0.2]) == (0, 0)
    assert nonsystematic([0.9, 0.6, 0.85, 0.3, 0.2]) == (1, 0)                # a rise of 25 % of the amount
    assert nonsystematic([0.9, 0.7, 0.9, 0.8, 0.75]) == (0, 0)                # a rise of exactly 20 %: not more
    assert nonsystematic([0.5, 0.45, 0.48, 0.47, 0.45]) == (0, 1)             # only 5 % lower at the end
    assert nonsystematic([0.5, 0.45, 0.42, 0.41, 0.4]) == (0, 0)              # exactly 10 % lower: enough
    _dd(app, client)
    with app.app_context():
        item = db.session.get(Item, 2)
        code = item.code
        good = item.kind.scores(item, {"c": _choices_for(0.01)})
        assert (good["jb1"], good["jb2"]) == (0, 0)
        flat = item.kind.scores(item, {"c": ("L" + "S" * 5) * 5})              # the same point at every delay
        assert flat["jb2"] == 1
        cols = [c for c, _ in item.kind.export_columns(item)]
        assert f"{code}_jb1" in cols and f"{code}_jb2" in cols
        typ, values, notes = item.kind.codebook_info(item, f"{code}_jb1", "de")
        assert values == "0 = unauffällig; 1 = nicht systematisch" and "https://doi.org/10.1037/1064-1297.16.3.264" in notes[0]
    client.post("/surveys/1/start", data={})
    from app.models import utcnow

    with app.app_context():
        for n, c in enumerate((_choices_for(0.01), _choices_for(0.1), ("L" + "S" * 5) * 5)):
            db.session.add(Response(survey_id=1, source="real", status=Response.COMPLETED, answers={"2": {"c": c}},
                                    pid=f"t-{n}", started_at=utcnow(), completed_at=utcnow()))
        db.session.commit()
    csv = client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig")
    assert f"{code}_jb1" in csv.splitlines()[0]
    page = client.get("/surveys/1/results").data.decode()
    assert "<strong>1 von 3</strong> Personen" in page and "Johnson &amp; Bickel, 2008</a>" in page
    assert "An algorithm for identifying nonsystematic delay-discounting data" in page          # in the list of the page
    from app import methods

    with app.app_context():
        f = methods.facts(db.session.get(Survey, 1))
        text = methods.draft(f)
    assert "Kriterium 1 traf auf 0, Kriterium 2 auf 1 von n = 3 Personen zu" in text and "(Du et al., 2002)" in text
    methods_page = client.get("/surveys/1/methods").data.decode()
    assert "Experimentelle Aufgaben" in methods_page and "Area under the curve as a measure of discounting" in methods_page


def test_full_sources_in_codebook_and_editor(app, client, survey):
    """N-8: full APA entries through the source mechanism (codebook notes, the fold of the editor and of "Konto")."""
    from app import apa
    from app.items import discounting

    for source in (discounting.MAZUR, discounting.DU, discounting.MYERSON, discounting.KOFFARNUS, discounting.JOHNSON_BICKEL):
        assert apa.ref(source)["html"] != source                                # read as APA, not shown raw
    _dd(app, client, mode="quick")
    with app.app_context():
        item = db.session.get(Item, 2)
        notes = item.kind.codebook_info(item, f"{item.code}_ed50", "de")[2]
        assert "https://doi.org/10.1037/a0035973" in notes[0]
    editor = client.get(E + "/items/2").data.decode()
    assert "Literatur zu diesem Element" in editor and "An adjusting procedure for studying delayed reinforcement" in editor
    assert "A 5-trial adjusting delay discounting task" not in client.get("/account/").data.decode()  # not experimental
