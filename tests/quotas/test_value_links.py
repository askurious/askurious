"""Links per person (concept "Werbelinks", 2026-10-08): one link per student of a seminar as a random value of a
stored URL parameter, made from a list of names or a number, counted (started, completed, usable), given out as CSV
or printed cards, with one dashboard link per value. The names stay with the survey."""
import re
from datetime import timedelta

import pytest

from app import boards, duplicate, edit_guard, panel, transfer
from app.extensions import db
from app.models import BoardShare, Response, Survey, User, utcnow
from tests.conftest import CONSENT_OK
from tests.participation.test_participate import build


def _survey(app):  # inside an app context
    return db.session.get(Survey, 1)


def _param(app, name="w"):
    with app.app_context():
        return next(p for p in panel.config(_survey(app))["params"] if p["name"] == name)


def _labels(app, name="w"):
    with app.app_context():
        return panel.value_labels(_survey(app)).get(name, {})


def _start(client):
    build(client)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})


def test_links_from_names_get_random_values_and_keep_the_names_here(app, client, survey):
    r = client.post("/surveys/1/value-links", data={"param": "w", "names": "Anna Muster\n\n  Ben Beispiel \n=SUMME(A1)\n"})
    assert r.headers["Location"].endswith("/field#value-links")
    param = _param(app)
    assert param["mode"] == "store" and not param["required"] and len(param["allowed"]) == 3
    assert all(re.fullmatch(r"[a-z2-9]{6}", v) for v in param["allowed"]) and len(set(param["allowed"])) == 3
    labels = _labels(app)
    assert [labels[v] for v in param["allowed"]] == ["Anna Muster", "Ben Beispiel", "=SUMME(A1)"]
    html = client.get("/surveys/1/field").data.decode()
    code = param["allowed"][0]
    assert f"?w={code}" in html and 'value="Anna Muster"' in html and 'id="value-links"' in html
    assert "Anna" not in html.split('value="Anna Muster"')[0].split("<table")[-1]  # no name in an address before it


def test_a_number_makes_numbered_links_and_more_can_follow(app, client, survey):
    client.post("/surveys/1/value-links", data={"param": "w", "count": "3"})
    client.post("/surveys/1/value-links", data={"param": "w", "count": "2"})
    param = _param(app)
    assert len(param["allowed"]) == 5
    assert [_labels(app)[v] for v in param["allowed"]] == ["Link 1", "Link 2", "Link 3", "Link 4", "Link 5"]
    r = client.post("/surveys/1/value-links", data={"param": "w", "count": "196"}, follow_redirects=True)
    assert "Höchstens 200 Links je Parameter" in r.data.decode() and len(_param(app)["allowed"]) == 5
    r = client.post("/surveys/1/value-links", data={"param": "w"}, follow_redirects=True)
    assert "Namen (einen je Zeile) oder eine Anzahl" in r.data.decode()
    r = client.post("/surveys/1/value-links", data={"param": "1x", "count": "2"}, follow_redirects=True)
    assert "Ungültiger Parametername" in r.data.decode()


def test_up_to_200_allowed_values_in_the_table_of_parameters(app, client, survey):
    many = ", ".join(f"v{k}" for k in range(200))
    client.post("/surveys/1/panel", data={"param_name": "w", "param_mode": "store", "param_required": "0",
                                          "param_allowed": many + ", v0"})
    assert len(_param(app)["allowed"]) == 200                                     # each value once
    r = client.post("/surveys/1/panel", data={"param_name": "w", "param_mode": "store", "param_required": "0",
                                              "param_allowed": many + ", v200"}, follow_redirects=True)
    assert "höchstens 200 erlaubte Werte" in r.data.decode() and len(_param(app)["allowed"]) == 200


def test_names_are_typed_in_the_table_and_survive_the_form_of_parameters(app, client, survey):
    client.post("/surveys/1/value-links", data={"param": "w", "count": "2"})
    a, b = _param(app)["allowed"]
    with app.app_context():
        before = edit_guard.settings_state(_survey(app), "panel")
    r = client.post("/surveys/1/value-links/names", data={f"label:w:{a}": "Carla", f"label:w:{b}": "", "autosave": "1"})
    assert r.get_json()["status"] == "saved"
    assert _labels(app) == {a: "Carla"}
    with app.app_context():
        assert edit_guard.settings_state(_survey(app), "panel") == before      # no conflict for the other form
    # the form of parameters keeps the names of values that stay, and drops those of values that go
    client.post("/surveys/1/panel", data={"param_name": "w", "param_mode": "store", "param_required": "0",
                                          "param_allowed": f"{a}, {b}", "base": before})
    assert _labels(app) == {a: "Carla"}
    client.post("/surveys/1/panel", data={"param_name": "w", "param_mode": "store", "param_required": "0",
                                          "param_allowed": b})
    client.post("/surveys/1/value-links/names", data={f"label:w:{b}": "Dora"})
    assert _labels(app) == {b: "Dora"}


def test_counts_csv_and_cards(app, client, survey, monkeypatch):
    client.post("/surveys/1/value-links", data={"param": "w", "names": "Anna\n=Formel"})
    a, b = _param(app)["allowed"]
    _start(client)
    with app.app_context():
        code = _survey(app).code
    for value in (a, a, b):
        p = app.test_client()
        p.get(f"/s/{code}?w={value}")
        p.post(f"/s/{code}", data=CONSENT_OK)
    with app.app_context():
        now = utcnow()
        done = []
        for value in (a, a, b):
            r = Response(survey_id=1, source="real", is_preview=False, status=Response.COMPLETED, params={"w": value},
                         started_at=now - timedelta(minutes=5), completed_at=now)
            db.session.add(r)
            db.session.flush()
            done.append(r.id)
        db.session.add(Response(survey_id=1, source="test", is_preview=False, status=Response.COMPLETED,
                                params={"w": a}, started_at=now, completed_at=now))   # tests never count
        db.session.commit()
    from app import balance

    monkeypatch.setattr(balance, "marked", lambda survey, *a, **k: {done[0]})        # quality control marks one
    with app.app_context():
        rows = {row["value"]: row for row in panel.group_counts(_survey(app))}
    assert (rows[a]["started"], rows[a]["completed"], rows[a]["usable"], rows[a]["label"]) == (4, 2, 1, "Anna")
    assert (rows[b]["started"], rows[b]["completed"], rows[b]["usable"]) == (2, 1, 1)
    assert client.get("/surveys/1/value-links/usable").get_json() == {"usable": {f"w={a}": 1, f"w={b}": 1, "w=": 0},
                                                                      "flags": {}}
    assert f'data-usable="w={a}"' in client.get("/surveys/1/field").data.decode()  # filled in after the page
    csv = client.get("/surveys/1/value-links.csv")
    text = csv.data.decode("utf-8")
    assert csv.mimetype == "text/csv" and text.startswith("﻿Name;Parameter;Wert;Teilnahmelink;begonnen;abgeschlossen;verwertbar")
    assert f"Anna;w;{a};http" in text and f"?w={a};4;2;1" in text
    assert f"'=Formel;w;{b};" in text                                             # no formula in a spreadsheet
    cards = client.get("/surveys/1/value-links/cards").data.decode()
    assert cards.count('class="value-card"') == 2 and "<svg" in cards and f"?w={b}" in cards


def test_a_dashboard_link_per_value_shows_only_its_participations(app, client, survey):
    client.post("/surveys/1/value-links", data={"param": "w", "names": "Anna\nBen"})
    a, b = _param(app)["allowed"]
    # links made without the tick: the tick alone, without new names, adds the missing dashboard links (Ralf,
    # 2026-10-09: the fold "Jeder Person ihren Stand zeigen" went)
    r = client.post("/surveys/1/value-links", data={"param": "w", "board": "1"}, follow_redirects=True)
    assert "2 Dashboard-Links angelegt" in r.data.decode()
    client.post("/surveys/1/value-links", data={"param": "w", "board": "1"})            # nothing twice
    assert len(_param(app)["allowed"]) == 2                                           # and no new link
    with app.app_context():
        shares = db.session.scalars(db.select(BoardShare)).all()
        assert sorted((s.param, s.value, s.label, s.valid) for s in shares) == sorted(
            [("w", a, "Anna", "field"), ("w", b, "Ben", "field")])
    with app.test_request_context():
        urls = boards.value_share_urls(_survey(app))
    assert set(urls) == {("w", a), ("w", b)}
    assert urls[("w", a)] in client.get("/surveys/1/field").data.decode()
    assert urls[("w", b)] in client.get("/surveys/1/value-links.csv").data.decode()
    token = urls[("w", a)].rsplit("/", 1)[1]
    page = app.test_client().get(f"/d/{token}").data.decode()
    assert "Anna" not in page and f"w = {a}" not in page                          # no name, no parameter on the public page
    # the person's own participation link to pass on, with its QR code (UI/UX audit 2026-10-09, X56, X57)
    assert "über Ihren persönlichen Link" in page and f"/s/" in page and f"?w={a}" in page and "<svg" in page
    # per-value links do not use up the twenty ordinary links of a dashboard, and are folded in the designer
    assert "Links für einzelne Werte (2)" in client.get("/surveys/1/dashboards/1").data.decode()
    client.post("/surveys/1/dashboards/1/link", data={"valid": "30", "label": "alle"})
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(BoardShare.id)).filter(BoardShare.param == "")) == 1


def test_the_tick_makes_one_dashboard_and_uses_it_again(app, client, survey):
    """Ralf, 2026-10-09: "Links erstellen" with the tick makes a dashboard with only the number of participations
    and a link of it per new link; the next run uses the same dashboard; without the tick nothing is made."""
    client.post("/surveys/1/value-links", data={"param": "w", "names": "Anna"})
    with app.app_context():
        assert not _survey(app).boards                                            # no tick, no dashboard
    html = client.get("/surveys/1/field").data.decode()
    assert 'name="board" value="1" >' in html                                     # not ticked before the first time
    r = client.post("/surveys/1/value-links", data={"param": "w", "names": "Ben", "board": "1"}, follow_redirects=True)
    assert "eigenen Dashboard-Link" in r.data.decode()
    r = client.post("/surveys/1/value-links", data={"param": "w", "names": "Cem", "board": "1"})
    with app.app_context():
        survey_ = _survey(app)
        assert len(survey_.boards) == 1
        board = survey_.boards[0]
        assert [t["key"] for t in board.tiles] == ["field:count"] and board.settings == {"value_links": "w"}
        assert sorted(s.label for s in board.shares) == ["Anna", "Ben", "Cem"]       # one each, none twice
    html = client.get("/surveys/1/field").data.decode()
    assert 'name="board" value="1" checked>' in html and "vom ersten Mal" in html
    client.post("/surveys/1/dashboards/1/delete")                                # gone: the next run makes a new one
    client.post("/surveys/1/value-links", data={"param": "w", "count": "1", "board": "1"})
    with app.app_context():
        assert [len(b.shares) for b in _survey(app).boards] == [4]


def test_names_stay_out_of_files_and_copies(app, client, survey):
    client.post("/surveys/1/value-links", data={"param": "w", "names": "Anna Muster"})
    with app.app_context():
        s = _survey(app)
        assert "Anna Muster" not in str(transfer.dump(s))
        new, _ = duplicate.create(s, db.session.get(User, 1), "Welle 2")
        db.session.commit()
        assert "value_labels" not in (new.panel or {}) and len(panel.config(new)["params"][0]["allowed"]) == 1


def test_only_admins_make_links(app, client, survey):
    from tests.dashboards.test_boards import OTHER

    from app.models import SurveyMember

    with app.app_context():
        db.session.add(SurveyMember(survey_id=1, orcid=OTHER, role="editor", position=1))
        db.session.commit()
    c = app.test_client()
    c.get(f"/auth/dev-login?orcid={OTHER}&name=Bea")
    c.post("/terms/", data={"accept": "1"})
    assert c.get("/surveys/1/field").status_code == 200                            # a member, not an admin
    c.post("/surveys/1/value-links", data={"param": "w", "count": "2"})
    with app.app_context():
        assert not panel.config(_survey(app))["params"]


def test_a_row_without_a_link_per_value(app, client, survey):
    """Participations through the plain link (or a value no longer allowed) are counted in a row of their own."""
    client.post("/surveys/1/value-links", data={"param": "w", "names": "Anna"})
    (a,) = _param(app)["allowed"]
    with app.app_context():
        now = utcnow()
        for params, status in (({"w": a}, Response.COMPLETED), ({}, Response.COMPLETED), (None, Response.ACTIVE),
                               ({"w": "altwer"}, Response.COMPLETED)):
            db.session.add(Response(survey_id=1, source="real", is_preview=False, status=status, params=params,
                                    started_at=now, completed_at=now if status == Response.COMPLETED else None))
        db.session.commit()
        rows = panel.group_counts(_survey(app))
    assert [(r["value"], r["rest"], r["started"], r["completed"]) for r in rows] == [(a, False, 1, 1), (None, True, 3, 2)]
    html = client.get("/surveys/1/field").data.decode()
    assert "über den Link für alle (ohne eigenen Link)" in html and 'data-usable="w="' in html
    assert client.get("/surveys/1/value-links/usable").get_json()["usable"] == {f"w={a}": 1, "w=": 2}
    text = client.get("/surveys/1/value-links.csv").data.decode("utf-8")
    assert "(ohne Link je Wert);w;;;3;2;2;" in text
    assert client.get("/surveys/1/value-links/cards").data.decode().count('class="value-card"') == 1


def test_an_upper_limit_per_link_is_a_quota(app, client, survey):
    from app import flow, quotas

    client.post("/surveys/1/value-links", data={"param": "w", "count": "2"})
    r = client.post("/surveys/1/value-links/limit", data={"param": "w", "limit": "3"}, follow_redirects=True)
    assert "höchstens 3 abgeschlossene Teilnahmen" in r.data.decode()
    a, b = _param(app)["allowed"]
    with app.app_context():
        survey_ = _survey(app)
        quota = panel.cap_quota(survey_, "w")
        assert quota.code == "links_w" and quota.mode == "hard"
        assert [(c.name, c.target, c.conds) for c in quota.cells] == [
            (a, 3, [{"item": "p:w", "op": "eq", "value": a}]), (b, 3, [{"item": "p:w", "op": "eq", "value": b}])]
        kinds = [n["t"] for n, _, _ in flow.walk(flow.tree(survey_))]
        assert "quota" in kinds and quotas.full_ends(survey_)
    assert 'name="limit" min="0" max="100000" value="3"' in client.get("/surveys/1/field").data.decode()
    client.post("/surveys/1/value-links", data={"param": "w", "names": "Neu"})   # a new link gets the limit too
    client.post("/surveys/1/value-links/limit", data={"param": "w", "limit": "5"})
    with app.app_context():
        quota = panel.cap_quota(_survey(app), "w")
        assert len(quota.cells) == 3 and {c.target for c in quota.cells} == {5}
        assert all("Neu" not in c.name for c in quota.cells)                     # the cells name the value, not the person
    client.post("/surveys/1/value-links/limit", data={"param": "w", "limit": ""})
    with app.app_context():
        assert panel.cap_quota(_survey(app), "w") is None and not quotas.full_ends(_survey(app))


def test_in_the_field_the_limit_changes_and_turns_away_at_the_link(app, client, survey):
    from app import quotas

    client.post("/surveys/1/value-links", data={"param": "w", "count": "2"})
    client.post("/surveys/1/value-links/limit", data={"param": "w", "limit": "1"})
    a, b = _param(app)["allowed"]
    _start(client)
    with app.app_context():
        quota = panel.cap_quota(_survey(app), "w")
        next(c for c in quota.cells if c.name == a).done = 1
        db.session.commit()
        assert quotas.full_at_entry(_survey(app), {"w": a}) is not None
        assert quotas.full_at_entry(_survey(app), {"w": b}) is None
    client.post("/surveys/1/value-links/limit", data={"param": "w", "limit": "2"})
    with app.app_context():
        assert quotas.full_at_entry(_survey(app), {"w": a}) is None
    r = client.post("/surveys/1/value-links/limit", data={"param": "w", "limit": ""}, follow_redirects=True)
    assert "nur ändern, nicht entfernen" in r.data.decode()
    with app.app_context():
        assert panel.cap_of(_survey(app), "w") == 2


def test_conspicuous_links_are_flagged(app, client, survey, monkeypatch):
    """A hint to look closer (Ralf, 2026-10-08): many marked, many completions in quick succession, far more than
    the others; never from IP addresses."""
    from app import balance

    client.post("/surveys/1/value-links", data={"param": "w", "count": "6"})
    values = _param(app)["allowed"]
    marked = set()
    with app.app_context():
        now = utcnow()

        def complete(value, minutes_ago, mark=False):
            r = Response(survey_id=1, source="real", is_preview=False, status=Response.COMPLETED, params={"w": value},
                         started_at=now - timedelta(minutes=minutes_ago + 5), completed_at=now - timedelta(minutes=minutes_ago))
            db.session.add(r)
            db.session.flush()
            if mark:
                marked.add(r.id)

        for k in range(4):                     # link 1: three of four marked, a day apart
            complete(values[0], k * 1440, mark=k < 3)
        for k in range(3):                     # link 2: three within six minutes
            complete(values[1], k * 3 + 100)
        for k in range(12):                    # link 3: twelve, a day apart; links 4-6 one each (median 2)
            complete(values[2], k * 1440 + 7)
        for v in values[3:]:
            complete(v, 9000)
        db.session.commit()
    monkeypatch.setattr(balance, "marked", lambda survey, *a, **k: marked)
    flags = client.get("/surveys/1/value-links/usable").get_json()["flags"]
    assert flags[f"w={values[0]}"] == ["3 von 4 abgeschlossenen von der Qualitätssicherung markiert"]
    assert flags[f"w={values[1]}"] == ["3 Abschlüsse innerhalb von 10 Minuten"]
    assert flags[f"w={values[2]}"] == ["12 Abschlüsse, mindestens 3-mal so viele wie üblich (Median 2)"]
    assert set(flags) == {f"w={v}" for v in values[:3]}
    assert f'data-flag="w={values[0]}"' in client.get("/surveys/1/field").data.decode()
    text = client.get("/surveys/1/value-links.csv").data.decode("utf-8")
    assert text.splitlines()[0].endswith(";auffällig") and "3 Abschlüsse innerhalb von 10 Minuten" in text


def test_the_block_participation_link_in_three_parts(app, client, survey):
    """Ralf, 2026-10-08: the language is picked next to the link (no table per language), links per value in one
    table, the parameter of links per person counts its values and stays out of the link for the panel."""
    with app.app_context():
        _survey(app).languages = ["de", "en"]
        db.session.commit()
    client.post("/surveys/1/value-links", data={"param": "w", "count": "2"})
    client.post("/surveys/1/panel", data={"param_name": ["w", "pid", "kurs"], "param_mode": ["store", "store", "redirect"],
                                          "param_required": ["0", "0", "0"],
                                          "param_allowed": [", ".join(_param(app)["allowed"]), "", "bio, chemie"]})
    html = client.get("/surveys/1/field").data.decode()
    assert "data-link-lang" in html and 'id="lang-url-de"' not in html             # a picker, no table per language
    assert html.index('id="value-links"') < html.index('id="panel"')
    panel_part = html.split('id="panel"')[1]
    assert 'type="hidden" name="param_name" value="w"' in panel_part              # w is looked after above
    assert "gehört zu den" in panel_part and 'value="w" placeholder' not in panel_part
    assert 'value="bio, chemie"' in html and "kurs=bio" in html and "nicht gezählt" in html  # not stored: one table
    link = re.search(r'id="panel-link"', html) and re.search(r'value="([^"]*\?pid=…[^"]*)"', html).group(1)
    assert "w=" not in link and "kurs=…" in link


def test_without_links_the_form_is_not_folded_twice(app, client, survey):
    """Ralf, 2026-10-08: "Links je Wert" with nothing but "Links je Person anlegen" in it was one level too many."""
    part = client.get("/surveys/1/field").data.decode().split('id="value-links"')[1].split('id="panel"')[0]
    assert "Namen eintragen" in part and "Weitere Links anlegen" not in part and 'name="names"' in part
    client.post("/surveys/1/value-links", data={"param": "w", "count": "1"})
    part = client.get("/surveys/1/field").data.decode().split('id="value-links"')[1].split('id="panel"')[0]
    assert "Namen eintragen (oder" not in part and '<details class="settings-fold link-fold value-links-add">' in part


def test_a_link_can_be_removed(app, client, survey):
    """The "×" of a row: the value is no longer allowed, its name goes, an empty cell of the upper limit too."""
    client.post("/surveys/1/value-links", data={"param": "w", "names": "Anna\nBen"})
    client.post("/surveys/1/value-links/limit", data={"param": "w", "limit": "3"})
    a, b = _param(app)["allowed"]
    html = client.get("/surveys/1/field").data.decode()
    assert f'name="remove" value="w:{a}"' in html and 'form="value-remove"' in html
    r = client.post("/surveys/1/value-links/remove", data={"remove": f"w:{a}"})
    assert r.headers["Location"].endswith("#value-links")
    assert _param(app)["allowed"] == [b] and _labels(app) == {b: "Ben"}
    with app.app_context():
        assert [c.name for c in panel.cap_quota(_survey(app), "w").cells] == [b]
    client.post("/surveys/1/value-links/remove", data={"remove": "w:nichtda"})          # unknown: nothing happens
    assert _param(app)["allowed"] == [b]


@pytest.mark.parametrize("role, via_group, sees", [("viewer", False, False), ("viewer", True, False),
                                                   ("editor", False, True), ("editor", True, True), ("admin", False, True)])
def test_names_of_the_links_only_from_edit_up(app, client, survey, monkeypatch, role, via_group, sees):
    """Security audit 2026-10-09, R3 (Ralf, 2026-10-09): whom a link per person is for and whether it looks
    conspicuous only from the role "Bearbeiten" up, also through a group; "Ansehen" sees links and counts only.
    The hint over the names says so."""
    from tests.dashboards.test_boards import OTHER

    from app.models import SurveyGroup, SurveyMember, WorkGroup, WorkGroupMember

    client.post("/surveys/1/value-links", data={"param": "w", "names": "Anna Muster\nBen Beispiel"})
    a, b = _param(app)["allowed"]
    client.post("/surveys/1/value-links", data={"param": "w", "board": "1"})
    monkeypatch.setattr(panel, "_flags", lambda *args: ["Grund zum Hinsehen"])
    with app.app_context():
        if via_group:
            g = WorkGroup(name="Seminar")
            g.members.append(WorkGroupMember(orcid=OTHER, name="Bea", role="member"))
            db.session.add(g)
            db.session.flush()
            db.session.add(SurveyGroup(survey_id=1, group_id=g.id, role=role))
        else:
            db.session.add(SurveyMember(survey_id=1, orcid=OTHER, role=role, position=1))
        db.session.add(Response(survey_id=1, source="real", is_preview=False, status=Response.COMPLETED,
                                params={"w": a}, started_at=utcnow(), completed_at=utcnow()))
        db.session.commit()
    c = app.test_client()
    c.get(f"/auth/dev-login?orcid={OTHER}&name=Bea")
    c.post("/terms/", data={"accept": "1"})
    page = c.get("/surveys/1/field").data.decode()
    usable = c.get("/surveys/1/value-links/usable").get_json()
    if not sees:
        # code review 2026-10-15, H1: the cells and the JSON are keyed by a hash, so "Ansehen" cannot read the link
        # values and build a person's link; the counts still reach every cell
        import re

        keys = re.findall(r'data-usable="([^"]*)"', page)
        assert keys and all(k.startswith("h") for k in keys) and set(keys) == set(usable["usable"])
        assert sorted(usable["usable"].values()) == [0, 0, 1]
        assert a not in page and b not in page and a not in str(usable) and b not in str(usable)
        # Abgleich 2026-10-15 B-2 (#1020): "Ansehen" sees the counts, the ready links, CSV and cards only from
        # "Bearbeiten" up; this replaces R3's "links for every role".
        assert f"?w={a}" not in page and f"?w={b}" not in page
        assert "Anna Muster" not in page and "Ben Beispiel" not in page
        assert c.get("/surveys/1/value-links.csv").status_code in (302, 403)
        assert c.get("/surveys/1/value-links/cards").status_code in (302, 403)
        return
    assert usable["usable"][f"w={a}"] == 1                                         # the counts
    assert f"?w={a}" in page and f"?w={b}" in page
    csv = c.get("/surveys/1/value-links.csv").data.decode("utf-8")
    cards = c.get("/surveys/1/value-links/cards").data.decode()
    designer = c.get("/surveys/1/dashboards/1").data.decode()
    assert f"?w={a}" in csv and f"?w={b}" in cards
    for text in (page, csv, cards, designer):
        assert ("Anna Muster" in text) is sees and ("Ben Beispiel" in text) is sees
    assert (f'data-flag="w={a}"' in page) is sees
    assert bool(usable["flags"]) is sees and ("Grund zum Hinsehen" in csv) is sees
    assert "sehen nur Sie" not in page
    assert ("sehen nur „Bearbeiten“ und „Admin“" in page) is sees
    assert ("Namen ab der Rolle „Bearbeiten“" in page) is not sees
