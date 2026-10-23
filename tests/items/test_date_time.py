"""Year, month, date and time of day picked from lists (app/items/date_time.py)."""
import io
import random
import zipfile
from datetime import datetime, timedelta

import pytest
from werkzeug.datastructures import MultiDict

from app import results
from tests.participation.test_participate import _pos
from app.extensions import db
from app.items import date_time
from app.items.base import AnswerError
from app.models import Item, Response, Survey
from tests.conftest import CONSENT_OK, add, fill_consent
from tests.helpers.page_pos import page_pos

NOW = datetime(2026, 10, 1, 12, 0)  # the clock of these tests (UTC)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(date_time, "_now", lambda: NOW)


def setup(client, parts="date", mode="required", **config):
    """A new date element in block 2; returns nothing, ids count up from 2."""
    add(client, "date_time")
    with client.application.app_context():
        item_id = max(i.id for i in db.session.scalars(db.select(Item)))
    client.post(f"/surveys/1/editor/items/{item_id}", data={
        "code": f"d{item_id}", "question": "Wann?", "answer_mode": mode, "parts": parts, "span": "past",
        "minute_step": "5", **config})
    return item_id


def parse(app, item_id, lang="de", **fields):
    with app.app_context():
        item = db.session.get(Item, item_id)
        return item.kind.parse_answer(item, MultiDict({f"i{item_id}_{k}": str(v) for k, v in fields.items()}), lang)


def test_each_kind_is_stored_as_iso(app, client, survey):
    year, month, day = setup(client, "year"), setup(client, "month"), setup(client, "date")
    time, both = setup(client, "time"), setup(client, "datetime")
    assert parse(app, year, y=1987) == 1987                                   # a year is a number
    assert parse(app, month, m=5, y=1987) == "1987-05"
    assert parse(app, day, d=3, m=5, y=1987) == "1987-05-03"
    assert parse(app, time, h=9, n=5) == "09:05"
    assert parse(app, both, d=23, m=12, y=2001, h=14, n=30) == "2001-12-23T14:30"


def test_days_that_do_not_exist(app, client, survey):
    day = setup(client)
    assert parse(app, day, d=29, m=2, y=2024) == "2024-02-29"                 # a leap year
    assert parse(app, day, d=29, m=2, y=2000) == "2000-02-29"                 # divisible by 400
    for d, m, y in ((29, 2, 2023), (30, 2, 2024), (31, 4, 2020), (31, 11, 1999)):
        with pytest.raises(AnswerError, match="gibt es nicht"):
            parse(app, day, d=d, m=m, y=y)
    old = setup(client, year_min="1890")
    with pytest.raises(AnswerError, match="gibt es nicht"):                   # divisible by 100, not by 400
        parse(app, old, d=29, m=2, y=1900)
    with pytest.raises(AnswerError, match="does not exist"):
        parse(app, day, "en", d=31, m=6, y=2020)


def test_incomplete_missing_and_forged_answers(app, client, survey):
    day, optional, time = setup(client), setup(client, mode="optional"), setup(client, "time", minute_step="15")
    with pytest.raises(AnswerError, match="Bitte wählen Sie eine Antwort"):
        parse(app, day)
    assert parse(app, optional) is None
    for item_id in (day, optional):                                            # half a date is no answer, also if voluntary
        with pytest.raises(AnswerError, match="vervollständigen"):
            parse(app, item_id, d=3, m=5)
    for bad in ({"d": 3, "m": 13, "y": 2000}, {"d": 0, "m": 5, "y": 2000}, {"d": "3a", "m": 5, "y": 2000},
                {"d": "²", "m": 5, "y": 2000}, {"d": -1, "m": 5, "y": 2000}):
        with pytest.raises(AnswerError, match="Ungültiger Wert"):
            parse(app, day, **bad)
    assert parse(app, time, h=23, n=45) == "23:45"
    for h, n in ((24, 0), (12, 60), (12, 20)):                                 # 20 is no quarter of an hour
        with pytest.raises(AnswerError, match="Ungültiger Wert"):
            parse(app, time, h=h, n=n)


def test_years_and_the_span(app, client, survey):
    past, future = setup(client), setup(client, span="future")
    anytime, fixed = setup(client, span="any"), setup(client, "year", year_min="1950", year_max="2008")
    with app.app_context():
        kind = db.session.get(Item, past).kind
        years = lambda i: kind.years(db.session.get(Item, i))                 # noqa: E731
        assert years(past)[0] == 2026 and years(past)[-1] == 1916             # the latest first, 110 years back
        assert years(future) == list(range(2026, 2037))
        assert (years(anytime)[0], years(anytime)[-1]) == (1916, 2036)
        assert years(fixed)[0] == 2008 and years(fixed)[-1] == 1950
    assert parse(app, past, d=1, m=10, y=2026) == "2026-10-01"
    assert parse(app, past, d=2, m=10, y=2026) == "2026-10-02"               # already the 2nd in Auckland
    with pytest.raises(AnswerError, match="nicht in der Zukunft"):
        parse(app, past, d=3, m=10, y=2026)
    assert parse(app, future, d=1, m=10, y=2026) == "2026-10-01"
    with pytest.raises(AnswerError, match="not be in the past"):
        parse(app, future, "en", d=30, m=9, y=2026)
    with pytest.raises(AnswerError, match="Jahr von 1916 bis 2026"):          # a year the list does not offer
        parse(app, past, d=1, m=1, y=1915)
    with pytest.raises(AnswerError, match="year from 1950 to 2008"):
        parse(app, fixed, "en", y=2009)
    assert parse(app, anytime, d=1, m=1, y=2030) == "2030-01-01"


def test_design_check_names_impossible_years(app, client, survey):
    wrong = setup(client, "year", year_min="2010", year_max="1990")
    ahead = setup(client, "year", year_min="2040")
    fine = setup(client, "time", year_min="2040")                            # no year asked: nothing to check
    with app.app_context():
        problems = lambda i: db.session.get(Item, i).kind.validate(db.session.get(Item, i))  # noqa: E731
        assert "größer" in problems(wrong)[0] and "Zukunft" in problems(ahead)[0] and problems(fine) == []


def test_page_lists_and_a_participation(app, client, survey):
    day, time = setup(client), setup(client, "time", mode="optional", minute_step="15")
    fill_consent(client)
    client.post("/surveys/1/start", data={})
    with app.app_context():
        url = f"/s/{db.session.get(Survey, 1).code}"
    p = app.test_client()
    p.get(url)
    p.post(url, data=CONSENT_OK)
    html = p.get(url).data.decode()
    assert f'name="i{day}_d"' in html and f'name="i{day}_m"' in html and f'name="i{day}_y"' in html
    assert '<option value="">Tag</option>' in html and ">Februar</option>" in html and ">1916</option>" in html
    assert html.index(f'name="i{day}_d"') < html.index(f'name="i{day}_m"') < html.index(f'name="i{day}_y"')
    assert '<option value="45" >45</option>' in html and '<option value="50"' not in html      # quarters of an hour
    assert html.count('type="text"') == 1 and 'name="more_info"' in html         # only the field people do not see
    assert "<textarea" not in html                                               # nothing to type
    again = p.post(url, data={"nav": "next", f"i{day}_d": "31", f"i{day}_m": "2", f"i{day}_y": "1990", "pos": page_pos(p, 1)},
                   follow_redirects=True).data.decode()
    assert "Dieses Datum gibt es nicht." in again
    assert '<option value="31" selected>31</option>' in again and '<option value="1990" selected>' in again  # kept for the fix
    done = p.post(url, data={"nav": "next", f"i{day}_d": "28", f"i{day}_m": "2", f"i{day}_y": "1990",
                             f"i{time}_h": "7", f"i{time}_n": "15", "pos": _pos(again)}, follow_redirects=True).data.decode()
    assert "Dieses Datum" not in done
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(source="real"))
        assert r.status == Response.COMPLETED and r.answers == {"1": r.answers["1"], str(day): "1990-02-28", str(time): "07:15"}
    english = app.test_client()
    english.get(url + "?lang=en")
    english.post(url, data=CONSENT_OK)
    page = english.get(url).data.decode()
    assert '<option value="">Day</option>' in page and ">February</option>" in page and "Uhr" not in page


def test_conditions_compare_dates_and_times(app, client, survey):
    from app import flow

    holds = lambda answer, op, value: flow.holds({"item": 2, "op": op, "value": value}, {2: answer})  # noqa: E731
    assert holds("1987-05-23", "lt", "2000") and not holds("2001-01-01", "lt", "2000")
    assert holds("1987-05-23", "eq", "1987") and holds("1987-05-23", "eq", "05.1987") and holds("1987-05-23", "eq", "23.05.1987")
    assert holds("1987-05-23", "gt", "22.05.1987") and not holds("1987-05-23", "gt", "05.1987")   # in May, not after it
    assert holds("1987-05-23", "ne", "1988") and not holds("1987-05-23", "ne", "1987")
    assert holds("07:15", "lt", "8:00") and holds("23:10", "gt", "22:00") and not holds("07:15", "gt", "8:00")
    assert holds("2001-12-23T14:30", "gt", "23.12.2001 14:00") and holds("2001-12-23T14:30", "eq", "2001-12-23")
    assert not holds("07:15", "lt", "2000") and not holds("07:15", "eq", "abc") and holds("07:15", "ne", "abc")
    assert holds(1987, "lt", "2000") and holds(1987, "eq", "1987")           # a year is a number, as before
    assert holds("3", "lt", "10") and holds("b", "eq", "b")                   # other questions unchanged
    day = setup(client)
    with app.app_context():
        item = db.session.get(Item, day)
        assert item.kind.condition_ok(item, "2000") and item.kind.condition_ok(item, "23.05.2000")
        assert not item.kind.condition_ok(item, "Mai") and not item.kind.condition_ok(item, "14:30")


def answers(app, item_id, values):
    with app.app_context():
        s = db.session.get(Survey, 1)
        t0 = datetime(2026, 9, 1, 10)
        for v in values:
            s.responses.append(Response(status=Response.COMPLETED, started_at=t0, completed_at=t0 + timedelta(seconds=300),
                                        answers={str(item_id): v} if v is not None else {}))
        db.session.commit()


def test_results_summarise_dates_and_years(app, client, survey):
    day, year = setup(client), setup(client, "year")
    answers(app, day, ["1987-05-23", "1990-01-01", "1987-11-02", "2001-12-23", None])
    answers(app, year, [1980, 1990])
    with app.app_context():
        r = results.compute(db.session.get(Survey, 1))
        e = next(e for e in r["items"] if e["item"].id == day)
        d = e["dates"]
        assert e["answered"] == 4 and e["columns"] == [] and d["n"] == 4
        assert (d["min"], d["median"], d["max"]) == ("23.05.1987", "02.12.1988", "23.12.2001")  # halfway between the middle two (R-9)
        # every year from the first to the last answer, the empty ones with 0 (UI/UX audit 2026-10-05, V59): 4 days
        # would need about 5,300 bars, so the step is the year
        assert d["step"] == "Jahr" and len(d["rows"]) == 15 and d["filled"]
        assert [(row["label"], row["count"]) for row in d["rows"] if row["count"]] == [("1987", 2), ("1990", 1), ("2001", 1)]
        y = next(e for e in r["items"] if e["item"].id == year)
        assert "dates" not in y and y["columns"][0]["type"] == "num" and y["columns"][0]["mean"] == 1985
        assert y["columns"][0]["gini"] is None                                # a year has no true zero
        item = db.session.get(Item, day)
        many = [{"d2": f"{1950 + n}-{1 + n % 12:02}-{1 + n % 28:02}"} for n in range(60)]
        wide = item.kind.results(item, many)
        assert wide["step"] == "Jahrzehnt" and wide["rows"][0]["label"] == "1950–1959" and wide["rows"][0]["count"] == 10
        clock = item.kind.results(item, [{"d2": "07:15"}, {"d2": "07:45"}, {"d2": "22:00"}])
        assert [(row["label"], row["count"]) for row in clock["rows"] if row["count"]] == [("07 Uhr", 2), ("22 Uhr", 1)]
        assert len(clock["rows"]) == 16                                       # the hours in between with 0
        # on the clock 22:00 is 9 h 15 min before 07:15 (over midnight), 14 h 15 min after 07:45: the arc of the
        # answers runs from 22:00 to 07:45, its median is 07:15 (circular median, audit 2026-10-05)
        assert (clock["min"], clock["median"], clock["max"]) == ("22:00", "07:15", "07:45") and clock["over_midnight"]
    html = client.get("/surveys/1/results").data.decode()
    assert "früheste <strong>23.05.1987</strong>" in html and "Md = 02.12.1988" in html


def test_export_codebook_and_spss(app, client, survey):
    day, year, time = setup(client), setup(client, "year"), setup(client, "time")
    month, both = setup(client, "month"), setup(client, "datetime")
    answers(app, day, ["1987-05-23", "1970-01-01"])
    with app.app_context():
        from app import export, sav

        s = db.session.get(Survey, 1)
        for r, y, t, m, b in zip(s.responses, (1987, 1970), ("07:15", "22:00"), ("1987-05", "1970-01"),
                                 ("1987-05-23T14:30", "1970-01-01T00:00")):
            r.answers = {**r.answers, str(year): y, str(time): t, str(month): m, str(both): b}
        db.session.commit()
        csv = export.to_csv(s)
        assert "1987-05-23,1987,07:15,1987-05,1987-05-23T14:30" in csv
        book = {row[0]: row for row in export.codebook(s)}
        assert book[f"d{day}"][2:4] == ("Datum", "JJJJ-MM-TT") and "nicht in der Zukunft" in book[f"d{day}"][4]
        assert "Jahre 1916 bis 2026" in book[f"d{day}"][4] and "Messniveau metrisch" in book[f"d{day}"][4]
        assert book[f"d{year}"][2] == "Jahr" and book[f"d{time}"][2:4] == ("Uhrzeit", "HH:MM (24 Stunden)")
        assert "Schritten von 5" in book[f"d{time}"][4] and "Jahre" not in book[f"d{time}"][4]
        _, _, meta = export._spss_dictionary(s, False, ("real",), export.DEFAULT_SPEEDER_PCT, True, None)
        assert meta[f"d{year}"]["kind"] == "int" and meta[f"d{year}"]["level"] == "SCALE"
        assert [meta[f"d{i}"]["date"] for i in (day, year, time, month, both)] == ["date", None, "time", "month", "datetime"]
        assert meta[f"d{day}"]["kind"] == "str"                                # ISO text in the CSV and in jamovi
        z = zipfile.ZipFile(io.BytesIO(export.spss_zip(s)))
        sps = z.read(next(n for n in z.namelist() if n.endswith(".sps"))).decode("utf-8-sig")
        assert f"    d{day} SDATE10" in sps and f"    d{time} TIME5" in sps and f"    d{year} F8.0" in sps
        assert f"    d{month} A8" in sps and f"    d{both} A16" in sps
        scale = next(line for line in sps.splitlines() if line.endswith("(SCALE)."))
        assert f" d{day} " in scale and f" d{time} " in scale and f" d{month} " not in scale
        # the .sav file: numbers with a date format; 1 January 1970 is 12,219,379,200 seconds after 14 October 1582
        assert sav.date_number("date", "1970-01-01") == 12219379200 and sav.date_number("time", "22:00") == 79200
        assert sav.date_number("month", "1987-05") == sav.date_number("date", "1987-05-01")
        assert sav.date_number("datetime", "1970-01-01T00:01") == 12219379260 and sav.date_number("date", "x") is None
        assert sav._format({"kind": "num", "date": "date"}) == (39 << 16) | (10 << 8)
        assert len(export.spss_sav(s)) > 500 and len(export.jamovi_omv(s)) > 500
        # a day can single out a person: published only after the confirmation, a year or a month stay
        hidden = export.open_columns(s)
        assert hidden == {f"d{day}", f"d{both}"}
        md = export.questionnaire_md(s)
        assert "Chosen from lists: date (day, month, year), years 1916–2026, not in the future" in md
        assert "Chosen from lists: time of day, minutes in steps of 5" in md


def test_sav_dates_as_spss_reads_them(app, client, survey, tmp_path):
    pyreadstat = pytest.importorskip("pyreadstat")
    day, time, both = setup(client), setup(client, "time"), setup(client, "datetime")
    answers(app, day, ["1987-05-23", None])
    with app.app_context():
        from app import export

        s = db.session.get(Survey, 1)
        s.responses[0].answers = {str(day): "1987-05-23", str(time): "07:15", str(both): "1987-05-23T14:30"}
        db.session.commit()
        path = tmp_path / "data.sav"
        path.write_bytes(export.spss_sav(s))
    df, meta = pyreadstat.read_sav(str(path))
    assert meta.original_variable_types[f"d{day}"] == "SDATE10" and meta.original_variable_types[f"d{time}"] == "TIME5"
    assert meta.original_variable_types[f"d{both}"] == "DATETIME17"
    assert str(df[f"d{day}"][0]) == "1987-05-23" and str(df[f"d{time}"][0]) == "07:15:00"
    assert str(df[f"d{both}"][0]).startswith("1987-05-23 14:30") and df[f"d{day}"].isna()[1]


def test_preview_dummy_data_and_duration(app, client, survey):
    day, both, soon = setup(client), setup(client, "datetime", minute_step="15"), setup(client, "date", span="future")
    html = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    assert "data-date" in html and f'name="i{both}_h"' in html and "Wann?" in html
    editor = client.get(f"/surveys/1/editor/items/{day}").data.decode()
    assert 'name="parts"' in editor and "Datum und Uhrzeit" in editor and 'name="year_min"' in editor
    with app.app_context():
        from app import dummy, duration

        s = db.session.get(Survey, 1)
        assert dummy.generate(s, 8, seed=5) == 8
        rows = list(db.session.scalars(db.select(Response).filter_by(source="dummy")))
        for r in rows:
            assert date_time.ISO_RE.fullmatch(r.answers[str(day)]) and len(r.answers[str(day)]) == 10
            assert r.answers[str(day)] <= "2026-10-02" < "2026-10-03" and r.answers[str(soon)] >= "2026-10-01"
            assert int(r.answers[str(both)][-2:]) % 15 == 0
        item = db.session.get(Item, day)
        assert duration.item_seconds(item, "de") > duration.item_seconds(db.session.get(Item, both), "de") - 5
        rng = random.Random(1)
        assert all(item.kind.dummy_fields(item, rng, {}) for _ in range(20))
