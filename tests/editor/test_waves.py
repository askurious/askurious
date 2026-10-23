"""Waves of a longitudinal design (app/waves.py): matching by the personal code (exact, tolerant only when unique,
duplicates), the list of waves on the research plan, the wide and long files, the report and the rights."""
import csv
import io
import zipfile

from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.items.personal_code import DEFAULT_PARTS
from app.models import Response, Survey, SurveyMember, User
from app.waves import link, report
from tests.conftest import add

P = DEFAULT_PARTS  # mother, birthday (2 digits), father, older siblings, place of birth: 6 characters


def test_exact_and_unmatched():
    persons = link([["M07K1A", "B12S0H", None], ["B12S0H", "M07K1A", "Z01Z0Z"]], P)
    by = {p["code"]: p for p in persons}
    assert by["M07K1A"]["rows"] == {0: 0, 1: 1} and by["M07K1A"]["quality"] == "exact"
    assert by["B12S0H"]["rows"] == {0: 1, 1: 0}
    assert by["Z01Z0Z"]["quality"] == "unmatched" and by[None]["quality"] == "no_code"
    rep = report(persons, 2)
    assert (rep["persons"], rep["linked"], rep["complete"], rep["unmatched"], rep["no_code"]) == (3, 2, 2, 1, 1)
    assert rep["waves"][0] == {"rows": 3, "exact": 2, "tolerant": 0, "partial": 0, "unmatched": 0, "duplicate": 0, "no_code": 1}


def test_tolerant_only_when_unique_both_ways():
    # one part differs (birthday 07 vs 17): linked only with the tick
    assert {p["quality"] for p in link([["M07K1A"], ["M17K1A"]], P)} == {"unmatched"}
    persons = link([["M07K1A"], ["M17K1A"]], P, tolerant=True)
    assert len(persons) == 1 and persons[0]["quality"] == "tolerant" and persons[0]["rows"] == {0: 0, 1: 0}
    assert persons[0]["code"] == "M07K1A"                                            # the code of the first wave
    # two parts differ: never
    assert len(link([["M07K1A"], ["M17K2A"]], P, tolerant=True)) == 2
    # ambiguous: two codes of wave 2 are each one part away -> nothing linked
    amb = link([["M07K1A"], ["M17K1A", "M07K1B"]], P, tolerant=True)
    assert all(p["quality"] == "unmatched" for p in amb) and len(amb) == 3
    # ambiguous the other way round: two codes of wave 1 near one code of wave 2
    assert all(p["quality"] == "unmatched" for p in link([["M17K1A", "M07K1B"], ["M07K1A"]], P, tolerant=True))
    # an exact pair is not broken up by a near code, and a code linked exactly in both waves takes no third
    mixed = link([["M07K1A", "B12S0H"], ["M07K1A", "B12S0X"]], P, tolerant=True)
    assert sorted(p["quality"] for p in mixed) == ["exact", "tolerant"]
    # "weiß nicht" in one wave counts as one differing part
    assert link([["M07K1A"], ["MXXK1A"]], P, tolerant=True)[0]["quality"] == "tolerant"


def test_duplicates_stay_unlinked():
    persons = link([["M07K1A", "M07K1A", "B12S0H"], ["M07K1A", "B12S0H"]], P, tolerant=True)
    dup = [p for p in persons if p["quality"] == "duplicate"]
    assert len(dup) == 2 and all(p["rows"] in ({0: 0}, {0: 1}) for p in dup)
    # the wave-2 row with the same code is not linked to either of them, nor tolerantly to anything near
    same = next(p for p in persons if p["code"] == "M07K1A" and p["quality"] != "duplicate")
    assert same["quality"] == "unmatched" and same["rows"] == {1: 0}
    assert next(p for p in persons if p["code"] == "B12S0H")["quality"] == "exact"
    # a code near a duplicate is ambiguous
    near = link([["M07K1A", "M07K1A"], ["M07K1B"]], P, tolerant=True)
    assert next(p for p in near if p["code"] == "M07K1B")["quality"] == "unmatched"
    # every part unknown is no code; three waves
    assert link([["XXXXXX"]], P)[0]["quality"] == "no_code"
    assert link([["??????"]], P)[0]["quality"] == "no_code"  # "weiß nicht" since audit 2026-10-10, N-11
    three = link([["M07K1A"], ["M07K1A"], ["M07K1A"]], P)
    assert len(three) == 1 and three[0]["rows"] == {0: 0, 1: 0, 2: 0} and report(three, 3)["complete"] == 1


# --- with surveys ------------------------------------------------------------------------------------------------

def _answer(code):
    keys = DEFAULT_PARTS
    values = [code[0], code[1:3], code[3], code[4], code[5]]
    return dict(zip(keys, values))


def _two_waves(app, client, wave1, wave2):
    """Survey 1 with a personal code (item 2) and a text question (item 3), survey 2 as its duplicate; real
    participations with the codes given and the answers "a<n>" / "b<n>"."""
    add(client, "personal_code")
    client.post("/surveys/1/editor/items/2", data=MultiDict([("code", "pcode"), ("question", "Code"), ("parts_shown", "1")]
                                                            + [("part", p) for p in DEFAULT_PARTS]))
    add(client, "text_input")
    client.post("/surveys/1/editor/items/3", data={"code": "note", "question": "Notiz"})
    with app.app_context():
        from app import duplicate

        s1 = db.session.get(Survey, 1)
        user = db.session.scalar(db.select(User).filter_by(orcid="0000-0000-0000-001X"))
        s2, _ = duplicate.create(s1, user, "Test nachher")
        ids = {}
        for survey, codes, prefix in ((s1, wave1, "a"), (s2, wave2, "b")):
            items = {i.code: i.id for b in survey.blocks for i in b.items}
            ids[survey.id] = items
            for n, code in enumerate(codes):
                answers = {str(items["note"]): f"{prefix}{n}"}
                if code:
                    answers[str(items["pcode"])] = _answer(code)
                db.session.add(Response(survey_id=survey.id, source="real", is_preview=False, status=Response.COMPLETED,
                                        pid=f"{prefix}{n}", qversion=1, answers=answers, layout={}))
        db.session.commit()
        return s2.id


def _save(client, *rows, survey_id=1):
    data = [("wave_survey", str(s)) for s, _ in rows]
    labels = [("wave_label", label) for _, label in rows]
    merged = []
    for a, b in zip(data, labels):
        merged += [a, b]
    return client.post(f"/surveys/{survey_id}/waves", data=MultiDict(merged))


def _zip(client, **args):
    r = client.get("/surveys/1/waves/merged.zip", query_string=args)
    assert r.status_code == 200, r.status_code
    z = zipfile.ZipFile(io.BytesIO(r.data))
    return {name: z.read(name) for name in z.namelist()}


def _rows(body):
    return list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))


def test_plan_saves_the_waves_for_every_wave(app, client, survey):
    two = _two_waves(app, client, ["M07K1A"], ["M07K1A"])
    page = client.get("/surveys/1/researchdesign").data.decode()
    assert 'id="wellen"' in page and 'name="wave_survey"' in page and "Test nachher" in page
    _save(client, (1, "vorher"), (two, "nachher"))
    with app.app_context():
        for sid in (1, two):
            assert db.session.get(Survey, sid).waves == {"waves": [{"survey": 1, "label": "vorher"}, {"survey": two, "label": "nachher"}]}
    # this survey must be among them, no survey twice
    r = _save(client, (two, "nachher"), (two, "x"))
    with app.app_context():
        assert db.session.get(Survey, 1).waves["waves"][1]["label"] == "nachher"
    assert r.status_code == 302
    # taken out: the other wave forgets the list
    _save(client, (1, "vorher"))
    with app.app_context():
        assert db.session.get(Survey, two).waves is None and len(db.session.get(Survey, 1).waves["waves"]) == 1
    # the duplicate did not take the list along (copied settings only)
    with app.app_context():
        from app import duplicate

        assert "waves" not in duplicate.SETTINGS


def test_wide_and_long_files_and_report(app, client, survey):
    two = _two_waves(app, client, ["M07K1A", "B12S0H", "D01D0D", "D01D0D", None], ["B12S0H", "M17K1A", "Q05Q5Q"])
    _save(client, (1, "vorher"), (two, "nachher"))
    files = _zip(client, layout="wide", fmt="csv")
    name = next(n for n in files if n.endswith(".csv") and n != "abgleich.csv")
    assert "breit" in name
    rows = _rows(files[name])
    by = {(r["match_code"], r["match_quality"]): r for r in rows}
    linked = by[("B12S0H", "exact")]
    assert linked["t1_note"] == "a1" and linked["t2_note"] == "b0" and linked["waves_n"] == "2"
    assert by[("M07K1A", "unmatched")]["t2_note"] == "" and by[("M17K1A", "unmatched")]["t1_note"] == ""
    assert sum(1 for r in rows if r["match_quality"] == "duplicate") == 2
    assert sum(1 for r in rows if r["match_quality"] == "no_code") == 1
    assert "t1_resp_id" in rows[0] and "t2_pcode" in rows[0]
    # tolerant: M07K1A and M17K1A differ in the birthday only
    rows = _rows(_zip(client, layout="wide", fmt="csv", tolerant="1")[name])
    tol = next(r for r in rows if r["match_quality"] == "tolerant")
    assert tol["match_code"] == "M07K1A" and tol["t1_note"] == "a0" and tol["t2_note"] == "b1"
    # long: one row per person and wave
    files = _zip(client, layout="long", fmt="csv")
    long_name = next(n for n in files if n.endswith(".csv") and n != "abgleich.csv")
    rows = _rows(files[long_name])
    b = [r for r in rows if r["match_code"] == "B12S0H"]
    assert sorted((r["wave"], r["wave_label"], r["note"]) for r in b) == [("1", "vorher", "a1"), ("2", "nachher", "b0")]
    assert len(rows) == 8                                                            # every row of both waves once
    report_rows = list(csv.reader(io.StringIO(files["abgleich.csv"].decode("utf-8-sig"))))
    assert report_rows[1][:4] == ["1", "vorher", "Test", "5"] and report_rows[2][3] == "3"
    assert ["verknuepft", "1"] in report_rows and ["zeilen_mit_doppeltem_code", "2"] in report_rows
    # SPSS and R files of the same data
    for fmt, magic in (("sav", b"$FL2"), ("rds", b"\x1f\x8b")):
        files = _zip(client, layout="wide", fmt=fmt)
        body = next(v for n, v in files.items() if n.endswith("." + fmt))
        assert body[:len(magic)] == magic
    # the page shows the report on request
    page = client.get("/surveys/1/data?waves=report").data.decode()
    assert "Wellen zusammenführen" in page and "in mehreren Wellen verknüpft" in page


def test_rights(app, client, survey):
    two = _two_waves(app, client, ["M07K1A"], ["M07K1A"])
    _save(client, (1, "vorher"), (two, "nachher"))
    # a merge is logged like the other raw-data downloads
    _zip(client)
    with app.app_context():
        from app.models import AccessLog

        row = db.session.scalars(db.select(AccessLog).order_by(AccessLog.id.desc())).first()
        assert row.action == "Datenexport Wellen zusammengeführt" and f"waves=1+{two}" in row.detail
    # only "Ansehen" on wave 2: it stays out, and fewer than two waves cannot be merged
    with app.app_context():
        m = db.session.scalar(db.select(SurveyMember).filter_by(survey_id=two))
        m.role = "viewer"
        db.session.commit()
    r = client.get("/surveys/1/waves/merged.zip")
    assert r.status_code == 302
    plan = client.get("/surveys/1/researchdesign").data.decode()
    assert "keine Berechtigung" in plan
    # nor can it be added anew
    _save(client, (1, "vorher"))
    _save(client, (1, "vorher"), (two, "nachher"))
    with app.app_context():
        assert len(db.session.get(Survey, 1).waves["waves"]) == 1
    # "Ansehen" on wave 1 itself: no download
    with app.app_context():
        m = db.session.scalar(db.select(SurveyMember).filter_by(survey_id=1))
        m.role = "viewer"
        db.session.commit()
    r = client.get("/surveys/1/waves/merged.zip")
    assert r.status_code == 302 and "zip" not in (r.headers.get("Content-Type") or "")


def test_scheme_must_match(app, client, survey):
    two = _two_waves(app, client, ["M07K1A"], ["M07K1A"])
    with app.app_context():
        s2 = db.session.get(Survey, two)
        item = next(i for b in s2.blocks for i in b.items if i.type == "personal_code")
        item.config = {**item.config, "parts": ["mother_first", "birth_day", "father_first"]}
        db.session.commit()
    _save(client, (1, "vorher"), (two, "nachher"))
    page = client.get("/surveys/1/researchdesign").data.decode()
    assert "weichen von der ersten Welle ab" in page
    assert client.get("/surveys/1/waves/merged.zip").status_code == 302


def test_autosave_draws_the_rows_anew(app, client, survey):
    two = _two_waves(app, client, ["M07K1A"], ["M07K1A"])
    r = client.post("/surveys/1/waves", data=MultiDict([("wave_survey", "1"), ("wave_label", "vorher"),
                                                        ("wave_survey", str(two)), ("wave_label", ""), ("autosave", "1")]))
    d = r.get_json()
    assert d["status"] == "saved" and 'name="wave_survey"' in d["form"] and "Welle 2" in d["form"]
    r = client.post("/surveys/1/waves", data=MultiDict([("wave_survey", str(two)), ("wave_label", "x"), ("autosave", "1")]))
    assert r.get_json()["status"] == "invalid" and "muss eine der Wellen" in r.get_json()["message"]


def test_large_merge_runs_in_a_process_of_its_own(app, client, survey, monkeypatch):
    """From REPORT_JOB_THRESHOLD participations of all waves together the merge leaves the web process like the other
    large exports (thread "Rechenknecht", 2026-10-17): 2 + 1 participations here."""
    from app import report_jobs

    two = _two_waves(app, client, ["M07K1A", "B12S0H"], ["M07K1A"])
    _save(client, (1, "vorher"), (two, "nachher"))
    started = []
    monkeypatch.setattr(report_jobs, "_spawn", lambda payload: started.append(payload["uri"]))
    app.config["REPORT_JOB_THRESHOLD"] = 4
    assert client.get("/surveys/1/waves/merged.zip").status_code == 200 and not started
    app.config["REPORT_JOB_THRESHOLD"] = 3                                          # wave 1 alone has only 2
    r = client.get("/surveys/1/waves/merged.zip", query_string={"layout": "long"})
    assert r.status_code == 202 and started == ["/surveys/1/waves/merged.zip?layout=long"]
    with app.app_context():
        from app import waves

        assert waves.participations(db.session.get(Survey, 1)) == 3


def test_codes_with_unknown_parts_are_linked_as_partial_only():
    """Audit 2026-10-21, F4: a code with "weiß nicht" (?) in some parts was linked as "exact" like a full code, and
    tolerantly even on the unknown part. Now: "partial", exactly only, and with fewer than MIN_KNOWN known parts not
    at all (no_code); the report counts the partial links."""
    from app.waves import MIN_KNOWN, report_csv

    assert MIN_KNOWN == 3
    persons = link([["M??K1A", "B12S0H"], ["M??K1A", "B12S0H"]], P)  # birthday unknown: 4 of 5 parts known
    assert sorted(p["quality"] for p in persons) == ["exact", "partial"]
    rep = report(persons, 2)
    assert (rep["linked"], rep["partial"], rep["waves"][0]["partial"]) == (2, 1, 1)
    # 2 of 5 parts known (birthday, father and siblings unknown): too little, every row a person of its own
    assert [p["quality"] for p in link([["M????A"], ["M????A"]], P)] == ["no_code", "no_code"]
    # no tolerant link over a code with unknown parts
    assert {p["quality"] for p in link([["M??K1A"], ["M??K1B"]], P, tolerant=True)} == {"unmatched"}
    assert {p["quality"] for p in link([["M??K1A"], ["M07K1A"]], P, tolerant=True)} == {"unmatched"}
    waves = [{"label": "a", "survey": Survey(title="A")}, {"label": "b", "survey": Survey(title="B")}]
    rows = list(csv.reader(io.StringIO(report_csv(rep, waves, False).lstrip("﻿"))))
    assert rows[0][6] == "partial" and rows[1][6] == "1"
    assert ["davon_teilweise_unbekannt", "1"] in rows
