"""Live votes (app/live/): blocks released for the participants, page by page; the screen chosen apart from that."""
from app.extensions import db
from app.models import Item, Response, Survey
from tests.exports.test_transfer import _upload as _read_in


def make(app, client, title="Ringvorlesung"):
    r = client.post("/presenter/new", data={"title": title, "language": "de"})
    with app.app_context():
        survey = db.session.scalar(db.select(Survey).filter_by(title=title))
        assert survey.live is not None and survey.consent_item is None
        assert r.headers["Location"].endswith(f"/presenter/{survey.code}/setup/question")       # the short wizard
        code, sid, block = survey.code, survey.id, survey.blocks[0].id
    client.post(f"/presenter/{code}/setup/question", data={"wizard": "skip"})
    client.post(f"/presenter/{code}/setup/done", data={"wizard": "next"})
    with app.app_context():  # these tests are about "Frage für Frage" (new live votes run in blocks)
        survey = db.session.get(Survey, sid)
        survey.live = {**survey.live, "flow": "questions"}
        db.session.commit()
    return code, sid, block


def add(app, client, sid, block, kind, **config_texts):
    r = client.post(f"/surveys/{sid}/editor/blocks/{block}/items", data={"type": kind})
    with app.app_context():
        item = max((i for b in db.session.get(Survey, sid).blocks for i in b.items), key=lambda i: i.id)
        if kind != "page_break":
            assert r.headers["Location"].endswith(f"?item={item.id}&neu=1")    # a new question opens beside the list, ready to type
        if "options" in config_texts:
            labels = config_texts.pop("options")
            item.config = {**item.config, "options": [{"id": f"o{n}", "code": str(n)} for n in range(1, len(labels) + 1)]}
            config_texts["options"] = {f"o{n}": l for n, l in enumerate(labels, start=1)}
        if config_texts:
            item.texts = {"de": {**(item.texts or {}).get("de", {}), **config_texts}}
        db.session.commit()
        return item.id


def key(p, code):
    return p.get(f"/live/{code}/state").json["key"]


def send(p, code, data):
    """The answer to the question on the screen, as its form sends it (ref "q-<id>")."""
    item = key(p, code).split("-")[1] if "-" in key(p, code) else ""
    return p.post(f"/live/{code}/answer", data={"ref": f"q-{item}", **data})


def steer(client, code, **data):
    return client.post(f"/presenter/{code}/control", data=data, headers={"X-Requested-With": "fetch"}).json["state"]


def test_an_area_of_their_own(app, client):
    code, sid, block = make(app, client)
    assert client.get("/presenter/").headers["Location"].endswith("/dashboard/?live=1")   # in the list of the surveys
    assert "Ringvorlesung" in client.get("/dashboard/").data.decode()
    assert client.get(f"/surveys/{sid}/editor/").headers["Location"].endswith(f"/presenter/{code}")
    q = add(app, client, sid, block, "rating", question="Wie war es?")
    page = client.get(f"/presenter/{code}?item={q}").data.decode()
    assert f"/surveys/{sid}/editor/items/{q}?embed=1" in page and "Wie war es?" in page
    embedded = client.get(f"/surveys/{sid}/editor/items/{q}?embed=1").data.decode()
    assert "site-header" not in embedded and "Speichern und schließen" not in embedded
    r = client.post(f"/surveys/{sid}/editor/items/{q}?embed=1", data={"code": "wie", "question": "Wie war es?"})
    assert r.headers["Location"].endswith("embed=1")                               # stays embedded after saving
    assert "live-item-saved" in client.get(r.headers["Location"]).data.decode()     # tells the page to refresh its list


def test_the_phones_follow_the_screen(app, client):
    code, sid, block = make(app, client)
    q1 = add(app, client, sid, block, "single_choice", question="Wie fanden Sie den Vortrag?", options=["gut", "mittel", "schlecht"])
    q2 = add(app, client, sid, block, "text_input", question="Ein Wort zum Vortrag")
    people = [app.test_client() for _ in range(3)]
    for p in people:
        assert "Gleich geht es los" in p.get(f"/live/{code}").data.decode() and key(p, code) == "wait"
    steer(client, code, action="start")
    for p, choice in zip(people, ["1", "1", "3"]):
        assert key(p, code) == f"q-{q1}"
        form = p.get(f"/live/{code}").data.decode()                                 # the join page draws the form
        assert f'name="i{q1}"' in form and f'name="i{q2}"' not in form            # only the question on the screen
        assert send(p, code, {f"i{q1}": choice}).json["ok"] and key(p, code) == f"answered-{q1}"
    shown = client.get(f"/presenter/{code}/result.json").json
    assert shown["current"]["n"] == 3 and "rows" in shown["current"] and shown["open"]       # open from the start
    steer(client, code, action="results_off")                                           # the ghost hides them
    shown = client.get(f"/presenter/{code}/result.json").json
    assert shown["current"]["n"] == 3 and "rows" not in shown["current"]
    steer(client, code, action="results_on")
    assert client.get(f"/presenter/{code}/result.json").json["current"]["rows"] == [["gut", 2], ["schlecht", 1], ["mittel", 0]]  # most first
    # the answer can be changed while voting is open: the form comes back filled in
    assert 'value="3" checked' in people[2].get(f"/live/{code}?aendern=1").data.decode()
    assert send(people[2], code, {f"i{q1}": "2"}).json["ok"]
    assert client.get(f"/presenter/{code}/result.json").json["current"]["rows"] == [["gut", 2], ["mittel", 1], ["schlecht", 0]]
    st = steer(client, code, action="close")
    assert st["closed"] == [q1] and key(people[0], code) == f"closed-{q1}"
    assert people[0].post(f"/live/{code}/answer", data={"ref": f"q-{q1}", f"i{q1}": "2"}).status_code == 409
    st = steer(client, code, action="next")
    assert st["screen"] == q2 and st["show"] is True and key(people[0], code) == f"q-{q2}"  # shown before: stays shown
    for p, word in zip(people, ["Spannend", "spannend ", "zu lang"]):
        assert send(p, code, {f"i{q2}": word}).json["ok"]
    assert key(people[0], code) == "end"                                            # the last question: the closing text
    assert "Vielen Dank für Ihre Teilnahme" in people[0].get(f"/live/{code}").data.decode()
    steer(client, code, action="results_on")
    shown = client.get(f"/presenter/{code}/result.json").json["current"]
    assert shown["kind"] == "words" and [r[:2] for r in shown["rows"]] == [["Spannend", 2], ["zu lang", 1]]
    steer(client, code, action="prev")
    assert key(people[1], code) == f"closed-{q1}"                                  # back for the discussion: still closed
    st = steer(client, code, action="reopen")
    assert st["closed"] == [] and key(people[1], code) == f"answered-{q1}"
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id)).filter_by(survey_id=sid)) == 3
    csv = client.get(f"/surveys/{sid}/export/data.csv").data.decode("utf-8-sig")
    assert "zu lang" in csv and "durchgang" in csv and "Durchgang 1" in csv
    screen = client.get(f"/presenter/{code}/screen").data.decode()
    assert "<svg" in screen and f"/live/{code}" in screen and 'data-toggle="voting"' in screen and "Nächsten Block" not in screen
    guest = app.test_client()
    assert guest.get(f"/presenter/{code}").status_code in (302, 401) and guest.get("/live/nope").status_code == 404


def test_several_words_per_device_when_allowed(app, client):
    code, sid, block = make(app, client)
    q = add(app, client, sid, block, "text_input", question="Begriffe?")
    steer(client, code, action="start")
    p = app.test_client()
    send(p, code, {f"i{q}": "Regression"})
    assert key(p, code) == "end"                                                    # one answer only by default
    client.post(f"/surveys/{sid}/editor/items/{q}?embed=1", data={"code": "q", "question": "Begriffe?", "live_multi_shown": "1",
                                                                   "live_multi": "1", "max_length": "2000"})
    assert key(p, code) == f"q-{q}-0"
    for word in ("Varianz", "Mittelwert"):
        before = key(p, code)
        r = send(p, code, {f"i{q}": word}).json
        assert r["ok"] and r["key"] == key(p, code) != before and r["left"] is None   # the form comes back empty
    steer(client, code, action="results_on")
    assert sorted(r[0] for r in client.get(f"/presenter/{code}/result.json").json["current"]["rows"]) == ["Mittelwert", "Regression", "Varianz"]
    with app.app_context():
        rows = db.session.scalars(db.select(Response).filter_by(survey_id=sid)).all()
        assert len(rows) == 3 and sum(1 for r in rows if (r.params or {}).get("live_extra")) == 2
    assert "1 Teilnahme" in client.get(f"/presenter/{code}/present").data.decode()  # still one person


def test_a_new_run_starts_afresh_and_keeps_the_answers(app, client):
    code, sid, block = make(app, client, "Seminar")
    q = add(app, client, sid, block, "rating", question="Wie war es?")
    steer(client, code, action="start")
    p = app.test_client()
    send(p, code, {f"i{q}": "5"})
    assert client.get(f"/presenter/{code}/result.json").json["current"]["n"] == 1
    st = steer(client, code, action="new_run", name="Seminar 2")
    assert st["run"]["name"] == "Seminar 2" and st["runs"][0]["name"] == "Durchgang 1"
    assert st["screen"] == q and st["show"] is True and st["closed"] == []          # results open (the default)
    assert client.get(f"/presenter/{code}/result.json").json["current"]["n"] == 0    # only the current run
    assert key(p, code) == f"q-{q}"                                                # the same browser answers again
    send(p, code, {f"i{q}": "3"})
    with app.app_context():
        runs = sorted((r.params or {}).get("live_run_name") for r in db.session.scalars(db.select(Response).filter_by(survey_id=sid)))
        assert runs == ["Durchgang 1", "Seminar 2"]
    assert "früher: Durchgang 1 (1)" in client.get(f"/presenter/{code}/present").data.decode()


def test_one_list_of_slides(app, client):
    code, sid, first = make(app, client, "Reihenfolge")
    a, b = (add(app, client, sid, first, "rating", question=t) for t in ("A", "B"))
    page = client.get(f"/presenter/{code}").data.decode()
    assert "drag-handle" in page and "Seitenumbruch" not in page and 'value="add_block"' not in page
    assert f"/editor/items/{a}/place" in page and "Folie 1" in page and "Folie 2" in page   # slides (E5)
    assert client.post(f"/surveys/{sid}/editor/items/{b}/place", data={"block_id": first, "position": "0"}).headers["Location"].startswith(f"/presenter/{code}")
    add(app, client, sid, first, "page_break")                                      # an older live vote: breaks, blocks
    with app.app_context():
        from app.models import Block

        s = db.session.get(Survey, sid)
        s.blocks.append(Block(position=1, titles={"de": "Teil 2"}))
        db.session.commit()
        second = s.blocks[1].id
    client.post(f"/surveys/{sid}/editor/items/{a}/place", data={"block_id": second, "position": "0"})
    client.get(f"/presenter/{code}")                                                     # opening the page puts it into one list
    with app.app_context():
        s = db.session.get(Survey, sid)
        assert len(s.blocks) == 1 and [i.text("question") for i in s.blocks[0].items] == ["B", "A"]


def test_presenting_starts_from_the_beginning(app, client):
    code, sid, block = make(app, client, "Start")
    q = add(app, client, sid, block, "rating", question="Eins")
    add(app, client, sid, block, "rating", question="Zwei")
    steer(client, code, action="next")
    steer(client, code, action="next")
    steer(client, code, action="close")
    page = client.get(f"/presenter/{code}/present").data.decode()
    assert 'value="start"' in page and f'target="live-presenter-{code}"' in page
    r = client.post(f"/presenter/{code}/control", data={"action": "start"})
    assert r.headers["Location"].endswith(f"/presenter/{code}/speaker")             # the presenter view
    with app.app_context():
        live = db.session.get(Survey, sid).live
        assert live["screen"] == q and live["show"] is True and live["closed"] == []      # first question, results and voting open


# --- live concept, section 6: start page, short wizard, tabs, settings (Ralf, 2026-10-03) --------


def test_start_page_button_list_and_filter(app, client):
    html = client.get("/dashboard/").data.decode()
    assert 'href="/presenter/new"' in html and "Live-Abstimmung" in html and 'href="/surveys/new"' in html
    assert "?live=1" not in html                                                   # no filter before there is one
    code, sid, _ = make(app, client, "Vorlesung 3")
    client.post("/surveys/new", data={"title": "Normale Befragung"})
    html = client.get("/dashboard/").data.decode()
    assert "Vorlesung 3" in html and "Normale Befragung" in html and 'href="/dashboard/?art=live"' in html
    card = html.split(">Vorlesung 3<")[1].split("</li>")[0]
    assert "badge-live" in card and f'href="/presenter/{code}"' in card and f"/presenter/{code}/settings" in card  # Fragen (Ralf, 2026-10-05)
    html = client.get("/dashboard/?live=1").data.decode()
    assert "Vorlesung 3" in html and "Normale Befragung" not in html
    assert ">Live-Abstimmungen</a>" not in html.split('class="nav-areas"')[1].split("</nav>")[0]   # no area of its own in the header


def test_wizard_in_three_steps(app, client):
    html = client.get("/presenter/new").data.decode()
    assert html.count('class="wizard-step ') == 3 and "Titel und Sprache" in html and 'value="de-du" selected' in html
    r = client.post("/presenter/new", data={"title": "Sitzung 3", "language": "de-du"})
    assert r.headers["Location"].endswith("/presenter/1/setup/question")
    assert "Anlegen fortsetzen" in client.get("/dashboard/").data.decode()
    r = client.post("/presenter/1/setup/question", data={"type": "rating", "question": "Wie sicher fühlst du dich?"})
    assert r.headers["Location"].endswith("/presenter/1/setup/done")
    assert client.post("/presenter/1/setup/question", data={"type": "conjoint"}).headers["Location"].endswith("/question")
    html = client.get("/presenter/1/setup/done").data.decode()
    assert "<svg" in html and "/live/1" in html                                       # QR code and address
    assert 'value="start"' in html and 'formaction="/presenter/1/control"' in html           # „Präsentieren“
    r = client.post("/presenter/1/control", data={"action": "start"})                   # straight into the presenter view
    assert r.headers["Location"].endswith("/presenter/1/speaker")
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.setup_step is None and s.languages == ["de-du"] and not s.project and s.consent_item is None
        assert [(i.type, i.text("question", "de-du")) for i in s.blocks[0].items] == [("rating", "Wie sicher fühlst du dich?")]


def test_tabs_and_redirects(app, client):
    code, sid, _ = make(app, client)
    for path in (f"/presenter/{code}", f"/presenter/{code}/present", f"/presenter/{code}/settings", f"/surveys/{sid}/results",
                 f"/surveys/{sid}/data"):
        html = client.get(path).data.decode()
        assert html.count('class="survey-tabs"') == 1, path
        tabs = html.split('class="survey-tabs"')[1].split("</nav>")[0]
        assert [t for t in ("Folien", "Präsentieren", "Ergebnisse", "Export", "Einstellungen") if t not in tabs] == [], path
        assert [t for t in ("Ablauf", "Quoten", "Übersetzung", "Datenerhebung", "Qualitätssicherung", "Dashboards") if t in tabs] == []
    for path, target in (("", "settings"), ("/field", "present"), ("/flow/", ""), ("/quality", ""),
                         ("/dashboards/", ""), ("/translation/", "")):
        r = client.get(f"/surveys/{sid}{path}")
        assert r.status_code == 302 and r.headers["Location"].rstrip("/").endswith(f"/presenter/{code}/{target}".rstrip("/")), path


def test_settings_save_by_themselves(app, client):
    code, sid, block = make(app, client)
    q = add(app, client, sid, block, "rating", question="Wie war es?")
    html = client.get(f"/presenter/{code}/settings").data.decode()
    assert html.count("data-autosave-form") == 4 and ">Speichern</button></div>" not in html
    r = client.post(f"/presenter/{code}/settings", data={"part": "teilnahme", "qa": "1", "end_text": "Danke!", "autosave": "1"})
    assert r.get_json()["status"] == "saved"
    assert client.post(f"/presenter/{code}/settings", data={"part": "titel", "title": " ", "autosave": "1"}).get_json()["status"] == "invalid"
    client.post(f"/presenter/{code}/settings", data={"part": "titel", "title": "Neu", "language": "en"})
    with app.app_context():
        s = db.session.get(Survey, sid)
        assert s.title == "Neu" and s.languages == ["en"] and s.live["qa"] is True
        assert db.session.get(Item, q).text("question", "en") == "Wie war es?" and s.end_texts["en"]["thanks"] == "Danke!"
    assert steer(client, code, action="start")["show"] is False                     # presenting starts covered
    steer(client, code, action="results_on")
    assert steer(client, code, action="screen", item=q)["show"] is True              # turning the slides keeps it shown
    steer(client, code, action="results_off")
    assert steer(client, code, action="next")["show"] is False                       # and covered


# --- more slide types (live concept 6.3) --------------------------------------------------------


def _config(app, item_id, **config):
    with app.app_context():
        item = db.session.get(Item, item_id)
        item.config = {**item.config, **config}
        db.session.commit()


def _answer_all(app, client, code, sid, q, forms):
    """Put question q on the screen, let one phone per form answer, show the results."""
    steer(client, code, action="screen", item=q)
    for data in forms:
        p = app.test_client()
        page = p.get(f"/live/{code}").data.decode()
        assert f'name="ref" value="q-{q}"' in page, (key(p, code), page[page.find("live-stage"):][:600])        # drawn with the page
        r = send(p, code, data)
        assert r.status_code == 200, r.json
    steer(client, code, action="results_on")
    return client.get(f"/presenter/{code}/result.json").json["current"]


def test_more_slide_types_on_the_screen(app, client):
    code, sid, block = make(app, client, "Typen")
    html = client.get(f"/presenter/{code}").data.decode()
    for kind in ("likert", "ranking", "constant_sum", "slider", "position2d", "info", "image"):
        assert f'value="{kind}"' in html
    steer(client, code, action="start")

    lk = add(app, client, sid, block, "likert", question="Wie sehr stimmen Sie zu?")
    with app.app_context():
        st = [s["id"] for s in db.session.get(Item, lk).config["statements"]]
    shown = _answer_all(app, client, code, sid, lk, [{f"i{lk}_{st[0]}": "4"}, {f"i{lk}_{st[0]}": "5"}])
    assert shown["kind"] == "means" and shown["rows"][0][1:] == [4.5, 2] and shown["hi"] == 5

    rk = add(app, client, sid, block, "ranking", question="Was ist wichtig?", options=["Preis", "Qualität", "Service"])
    order = lambda *codes: {f"i{rk}": list(codes), f"i{rk}_touched": "1"}
    shown = _answer_all(app, client, code, sid, rk, [order("2", "1", "3"), order("2", "3", "1")])
    assert shown["kind"] == "ranks" and [r[0] for r in shown["rows"]] == ["Qualität", "Preis", "Service"]
    assert shown["rows"][0][1] == 1.0

    cs = add(app, client, sid, block, "constant_sum", question="100 Punkte", options=["A", "B"])
    split = lambda a, b: {f"i{cs}_1": str(a), f"i{cs}_2": str(b), f"i{cs}_touched": "1"}
    shown = _answer_all(app, client, code, sid, cs, [split(70, 30), split(50, 50)])
    assert shown["kind"] == "means" and shown["rows"][0][:2] == ["A", 60.0] and shown["hi"] == 100

    sl = add(app, client, sid, block, "slider", question="Wie viele Studierende hier?")
    _config(app, sl, min=0, max=200)
    with app.app_context():
        row = db.session.get(Item, sl).config["rows"][0]["id"]
    shown = _answer_all(app, client, code, sid, sl, [{f"i{sl}_{row}": v} for v in ("10", "30", "190")])
    assert shown["kind"] == "bars" and len(shown["rows"]) == 10 and shown["rows"][0] == ["0–20", 1]
    assert shown["rows"][-1] == ["180–200", 1] and shown["mean"] == 76.7 and shown["median"] == 30

    pos = add(app, client, sid, block, "position2d", question="Einordnen", options=["KI", "Roboter"])
    place = lambda a, b: {f"i{pos}_1": a, f"i{pos}_2": b}
    shown = _answer_all(app, client, code, sid, pos, [place("10,20", "80,90"), place("30,40", "60,70")])
    assert shown["kind"] == "points" and shown["points"][0] == {"label": "KI", "n": 2, "x": 20.0, "y": 30.0}
    assert shown["axes"]["x_label"] == "Risiko"

    info = add(app, client, sid, block, "info", question="Pause bis 11 Uhr")
    steer(client, code, action="screen", item=info)
    p = app.test_client()
    assert key(p, code) == f"look-{info}" and "Leinwand" in p.get(f"/live/{code}").data.decode()
    assert p.post(f"/live/{code}/answer", data={"ref": f"q-{info}"}).status_code == 409
    current = client.get(f"/presenter/{code}/result.json").json["current"]
    assert current["kind"] == "content" and "Pause bis 11 Uhr" in current["html"]


def test_wizard_lets_people_lay_down_their_own_questions(app, client):
    client.post("/presenter/new", data={"title": "Selbst", "language": "de"})
    html = client.get("/presenter/1/setup/question").data.decode()
    assert "Ich möchte meine Folien lieber selbst festlegen." in html and 'value="own"' in html
    r = client.post("/presenter/1/setup/question", data={"wizard": "own"})       # straight to the editing page
    assert r.headers["Location"].endswith("/presenter/1")
    with app.app_context():
        assert db.session.get(Survey, 1).setup_step is None


# --- package B: the code page ---------------------------------------------------------------------------------


def test_code_page(app, client):
    code, sid, block = make(app, client, "Codes")
    guest = app.test_client()
    html = guest.get("/live/").data.decode()
    assert 'name="code"' in html and "Code eingeben" in html
    r = guest.get(f"/live/?code= {code.upper()} ")                                       # spaces and capitals do not matter
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/live/{code}")
    client.post("/surveys/new", data={"title": "Normale Befragung"})
    with app.app_context():
        normal = db.session.scalar(db.select(Survey).filter_by(title="Normale Befragung"))
        normal_code = normal.code
    r = guest.get(f"/live/?code={normal_code}")                                         # a draft: not yet
    assert r.status_code == 404 and "keine laufende" in r.data.decode()
    with app.app_context():
        db.session.get(Survey, normal.id).status = Survey.RUNNING
        db.session.commit()
    assert guest.get(f"/live/?code={normal_code}").headers["Location"].endswith(f"/s/{normal_code}")
    assert "/l</" not in client.get(f"/presenter/{code}/screen").data.decode()
    assert "/l · Code" not in client.get(f"/presenter/{code}/screen").data.decode()


def test_code_page_stops_guessing(app, client):
    app.config["CODE_LIMIT"] = 3
    guest = app.test_client()
    addr = {"REMOTE_ADDR": "203.0.113.7"}
    for n in range(3):
        assert guest.get(f"/live/?code=nope{n}", environ_base=addr).status_code == 404
    r = guest.get("/live/?code=nope9", environ_base=addr)
    assert r.status_code == 429 and "Zu viele falsche Codes" in r.data.decode()


# --- package D: moderation of short texts -------------------------------------------------------------------------


def test_moderation_hides_filters_and_approves(app, client):
    code, sid, block = make(app, client, "Moderation")
    q = add(app, client, sid, block, "text_input", question="Ein Wort?")
    steer(client, code, action="start")
    for word in ("Statistik", "statistik", "Scheiße", "Mathe"):
        send(app.test_client(), code, {f"i{q}": word})
    steer(client, code, action="results_on")
    words = lambda: [r[0] for r in client.get(f"/presenter/{code}/result.json").json["current"]["rows"]]
    assert words() == ["Statistik", "Mathe"]                                          # the swear word is filtered
    mod = client.get(f"/presenter/{code}/moderation.json").json
    assert {e["text"]: e["status"] for e in mod["entries"]} == {"Statistik": "shown", "Scheiße": "filtered", "Mathe": "shown"}
    steer(client, code, action="hide", item=q, key="Mathe")
    assert words() == ["Statistik"]
    steer(client, code, action="approve", item=q, key="mathe")                        # back
    assert "Mathe" in words()
    client.post(f"/presenter/{code}/settings", data={"part": "teilnahme", "premod": "1", "filter": "1"})
    assert words() == ["Mathe"]                                                       # approved before, the rest waits
    assert {e["status"] for e in client.get(f"/presenter/{code}/moderation.json").json["entries"]} == {"pending", "filtered", "shown"}
    steer(client, code, action="approve", item=q, key="Statistik")
    assert sorted(words()) == ["Mathe", "Statistik"]
    st = steer(client, code, action="wall_on")
    current = client.get(f"/presenter/{code}/result.json").json["current"]
    assert st["wall"] == [q] and current["kind"] == "wall" and [r[:2] for r in current["rows"]] == [["Statistik", 2], ["Mathe", 1]]
    page = client.get(f"/presenter/{code}/speaker").data.decode()                        # the presenter's business
    assert "data-moderation=" in page and "live.js" in page
    assert "data-moderation=" not in client.get(f"/presenter/{code}/present").data.decode()
    assert f"/presenter/{code}/screen" in client.get(f"/presenter/{code}/present").data.decode()   # the button for the projector
    assert 'data-toggle="wall"' in client.get(f"/presenter/{code}/screen").data.decode()
    assert "data-fullscreen" in client.get(f"/presenter/{code}/screen").data.decode()        # full screen for the projector
    assert "data-fullscreen" in client.get(f"/presenter/{code}/speaker").data.decode()



def test_hidden_words_belong_to_their_run(app, client):
    """Pre-test 2026-10-23: a word hidden by a click in an earlier test stayed hidden in every later run; the screen
    said "1 Antwort" and stayed empty. Hiding and approving now hold for the run they were made in."""
    code, sid, block = make(app, client, "Moderation je Durchgang")
    q = add(app, client, sid, block, "text_input", question="Ein Begriff?")
    steer(client, code, action="start")
    send(app.test_client(), code, {f"i{q}": "Codieren"})
    steer(client, code, action="results_on")
    words = lambda: [r[0] for r in client.get(f"/presenter/{code}/result.json").json["current"]["rows"]]
    steer(client, code, action="hide", item=q, key="Codieren")
    assert words() == []
    steer(client, code, action="new_run", name="Seminar 2")
    steer(client, code, action="screen", item=q)
    steer(client, code, action="results_on")
    send(app.test_client(), code, {f"i{q}": "Codieren"})
    assert words() == ["Codieren"]                                                    # a new run starts afresh


def test_word_cloud_on_the_results_page(app, client):
    """The results page draws a short-text question as the screen's word cloud too, beside its list (Ralf,
    2026-10-16): with the moderation of the screen and only the chosen run."""
    import json
    import re

    from app import live
    from app.models import Item

    code, sid, block = make(app, client, "Wolke")
    q = add(app, client, sid, block, "text_input", question="Ein Wort?")
    steer(client, code, action="start")
    for word in ("Statistik", "statistik", "Mathe", "Scheiße", "langweilig"):
        send(app.test_client(), code, {f"i{q}": word})
    steer(client, code, action="hide", item=q, key="langweilig")
    steer(client, code, action="new_run", name="Seminar 2")
    steer(client, code, action="screen", item=q)
    send(app.test_client(), code, {f"i{q}": "Daten"})
    cloud = lambda page: json.loads(re.search(r"data-res-cloud='([^']*)'", page).group(1).replace("&#39;", "'"))
    page = client.get(f"/surveys/{sid}/results?go=1").data.decode()
    assert cloud(page) == [["Statistik", 2], ["Daten", 1], ["Mathe", 1]]        # hidden and swear word left out
    assert "live.js" in page and "live.css" in page and "langweilig" in page    # the list below keeps every text
    with app.app_context():
        run = str(db.session.get(Survey, sid).live["run"]["id"])
    assert cloud(client.get(f"/surveys/{sid}/results?durchgang={run}").data.decode()) == [["Daten", 1]]
    steer(client, code, action="wall_on")                                        # a wall on the screen: no cloud
    assert "data-res-cloud" not in client.get(f"/surveys/{sid}/results?go=1").data.decode()
    with app.app_context():                                                      # "Ansehen": words from five people on
        survey, item = db.session.get(Survey, sid), db.session.get(Item, q)
        survey.live = {**survey.live, "wall": []}
        data = {"items": [{"item": item, "columns": [{"free": True, "texts": {"rows": [("KI", 5), ("ki", 1), ("Bier", 4)]}}]}]}
        live.result_clouds(survey, data, viewer=True)
        assert data["items"][0]["columns"][0]["cloud"] == [("KI", 6)]

# --- package E: questions from the audience (Q&A) ----------------------------------------------------------------


def test_questions_from_the_audience(app, client):
    code, sid, block = make(app, client, "Q&A")
    add(app, client, sid, block, "rating", question="Wie war es?")
    p, other = app.test_client(), app.test_client()
    p.get(f"/live/{code}")
    assert p.get(f"/live/{code}/qa.json").json == {"enabled": False}
    assert p.post(f"/live/{code}/qa", data={"text": "Hallo?"}).status_code == 409        # off by default
    client.post(f"/presenter/{code}/settings", data={"part": "teilnahme", "qa": "1", "filter": "1"})
    assert p.post(f"/live/{code}/qa", data={"text": "  Gibt es die Folien online?  "}).json == {"ok": True, "pending": False}
    assert p.post(f"/live/{code}/qa", data={"text": "Was ist ein p-Wert?"}).json["ok"]
    assert other.post(f"/live/{code}/qa", data={"text": "So ein Scheiß"}).json["pending"]  # swear word: waits
    rows = p.get(f"/live/{code}/qa.json").json["questions"]
    assert [r["text"] for r in rows] == ["Gibt es die Folien online?", "Was ist ein p-Wert?"]
    second = rows[1]["id"]
    assert other.post(f"/live/{code}/qa/{second}/vote").json == {"ok": True, "votes": 1, "voted": True}
    assert other.post(f"/live/{code}/qa/{second}/vote").json["votes"] == 0           # taken back
    other.post(f"/live/{code}/qa/{second}/vote")
    assert [r["text"] for r in p.get(f"/live/{code}/qa.json").json["questions"]][0] == "Was ist ein p-Wert?"   # most votes first
    for n in range(3):
        p.post(f"/live/{code}/qa", data={"text": f"Frage {n}"})
    assert p.post(f"/live/{code}/qa", data={"text": "eine zu viel"}).status_code == 429  # five per device and run
    # Präsentieren: all questions with their status; the screen shows them
    mine = client.get(f"/presenter/{code}/qa.json").json
    assert {q["status"] for q in mine["questions"]} == {"open", "pending"} and mine["enabled"]
    pending = next(q["id"] for q in mine["questions"] if q["status"] == "pending")
    steer(client, code, action="qa_open", qa=pending)                                # approved by the presenter
    steer(client, code, action="qa_answer", qa=second)
    st = steer(client, code, action="qa_screen_on")
    shown = client.get(f"/presenter/{code}/result.json").json["current"]
    assert st["qa_screen"] and shown["kind"] == "qa" and shown["rows"][-1]["status"] == "answered"
    assert "So ein Scheiß" in [r["text"] for r in shown["rows"]]
    assert steer(client, code, action="next")["qa_screen"] is False                   # back to the slides
    csv = client.get(f"/presenter/{code}/audience-questions.csv").data.decode("utf-8-sig")
    assert csv.splitlines()[0] == "durchgang;durchgang_ursprung;frage;stimmen;status;zeit;folie" and "Was ist ein p-Wert?;1;answered" in csv
    page = client.get(f"/presenter/{code}/speaker").data.decode()
    assert "data-qa=" in page and 'data-toggle="qa"' not in client.get(f"/presenter/{code}/screen").data.decode()
    assert "data-live-qa=" in p.get(f"/live/{code}").data.decode()


# --- package F: quiz -------------------------------------------------------------------------------------------


def test_quiz_points_countdown_and_leaderboard(app, client, monkeypatch):
    from app import live

    code, sid, block = make(app, client, "Quiz")
    q1 = add(app, client, sid, block, "single_choice", question="Hauptstadt?", options=["Berlin", "Bonn"])
    q2 = add(app, client, sid, block, "slider", question="Wie viele Länder?")
    with app.app_context():
        row = db.session.get(Item, q2).config["rows"][0]["id"]
    page = client.get(f"/presenter/{code}?item={q1}").data.decode()
    assert 'name="action" value="quiz_set"' in page and 'name="correct" value="1"' in page
    r = client.post(f"/presenter/{code}/control", data={"action": "quiz_set", "item": q1, "on": "1", "correct": "1", "seconds": "20"})
    assert r.headers["Location"].endswith(f"?item={q1}")
    client.post(f"/presenter/{code}/control", data={"action": "quiz_set", "item": q2, "on": "1", "value": "16", "tol": "1"})
    now = [1000.0]
    from types import SimpleNamespace
    import time

    clock = SimpleNamespace(time=lambda: now[0], monotonic=time.monotonic)
    for module in (live.base, live.control, live.audience, live.screen, live.scores):
        monkeypatch.setattr(module, "time", clock)
    fast, slow, wrong = (app.test_client() for _ in range(3))
    for p in (fast, slow, wrong):
        p.get(f"/live/{code}")
    assert 'data-live-nick=' in fast.get(f"/live/{code}").data.decode()
    assert fast.post(f"/live/{code}/name", data={"name": "Ada"}).json["ok"]
    steer(client, code, action="start")                                                # q1 comes up at 1000
    assert fast.get(f"/live/{code}/state").json["left"] == 20
    send(fast, code, {f"i{q1}": "1"})                                                  # at once: 1000 points
    now[0] = 1010.0
    send(slow, code, {f"i{q1}": "1"})                                                  # after 10 of 20 s: 750
    send(wrong, code, {f"i{q1}": "2"})
    now[0] = 1025.0                                                                    # the countdown has run out
    assert app.test_client().post(f"/live/{code}/answer", data={"ref": f"q-{q1}", f"i{q1}": "1"}).status_code == 409
    state = fast.get(f"/live/{code}/state").json
    assert state["key"].startswith(f"closed-{q1}") and state["quiz"] == {"answered": True, "right": True, "points": 1000,
                                                                        "total": 1000, "place": 1, "of": 3}
    assert wrong.get(f"/live/{code}/state").json["quiz"]["right"] is False
    steer(client, code, action="results_on")
    shown = client.get(f"/presenter/{code}/result.json").json
    assert shown["open"] is False and shown["current"]["rows"][0][2]["right"] and not shown["current"]["rows"][1][2]["right"]
    steer(client, code, action="next")                                                 # q2: no countdown, 1000 if close enough
    send(slow, code, {f"i{q2}_{row}": "16.5"})
    send(wrong, code, {f"i{q2}_{row}": "30"})
    st = steer(client, code, action="board_on")
    board = client.get(f"/presenter/{code}/result.json").json["current"]
    assert st["board_screen"] and board["kind"] == "board"
    animals = {a for a, _, _ in live.NICK_ANIMALS}  # without a name of its own a phone has a random one (Philipp, 2026-10-04)
    assert [r["points"] for r in board["rows"]] == [1750, 1000, 0] and board["rows"][1]["name"] == "Ada"
    assert all(r["name"].split(" ")[-1] in animals for r in (board["rows"][0], board["rows"][2]))
    csv = client.get(f"/surveys/{sid}/export/data.csv").data.decode("utf-8-sig")
    assert "Ada" not in csv                                                            # the nickname never in the export
    assert 'data-toggle="board"' in client.get(f"/presenter/{code}/screen").data.decode()


# --- package G: reactions, marks over an image, printable slides --------------------------------------------------


def test_reactions_float_over_the_screen(app, client, monkeypatch):
    from app import live

    code, sid, block = make(app, client, "Reaktionen")
    add(app, client, sid, block, "rating", question="Wie war es?")
    steer(client, code, action="start")
    p = app.test_client()
    assert "data-live-react=" in p.get(f"/live/{code}").data.decode()
    now = [500.0]
    from types import SimpleNamespace
    import time

    clock = SimpleNamespace(time=lambda: now[0], monotonic=time.monotonic)
    for module in (live.base, live.control, live.audience, live.screen, live.scores):
        monkeypatch.setattr(module, "time", clock)
    assert p.post(f"/live/{code}/reaction", data={"e": "👍"}).json["ok"]
    assert p.post(f"/live/{code}/reaction", data={"e": "👏"}).status_code == 429          # too fast
    now[0] += 2
    assert p.post(f"/live/{code}/reaction", data={"e": "👏"}).json["ok"]
    assert p.post(f"/live/{code}/reaction", data={"e": "💩"}).status_code == 400          # not offered
    assert [e for _, e in client.get(f"/presenter/{code}/result.json").json["reactions"]] == ["👍", "👏"]
    client.post(f"/presenter/{code}/settings", data={"part": "teilnahme"})              # all unticked: no reactions
    assert "data-live-react=" not in app.test_client().get(f"/live/{code}").data.decode()
    assert client.get(f"/presenter/{code}/result.json").json["reactions"] == []


def test_marks_and_printable_slides(app, client):
    code, sid, block = make(app, client, "Folien")
    mark = add(app, client, sid, block, "image_mark", question="Wo ist es schön?")
    words = add(app, client, sid, block, "text_input", question="Ein Wort?")
    add(app, client, sid, block, "info", question="Pause")
    steer(client, code, action="start")
    send(app.test_client(), code, {f"i{mark}": "[[0.25, 0.5], [0.75, 0.5]]"})
    shown = client.get(f"/presenter/{code}/result.json").json["current"]
    assert shown["n"] == 1
    steer(client, code, action="results_on")
    shown = client.get(f"/presenter/{code}/result.json").json["current"]
    assert shown["kind"] == "marks" and shown["marks"] == [[0.25, 0.5], [0.75, 0.5]] and shown["mode"] == "points"
    steer(client, code, action="next")
    send(app.test_client(), code, {f"i{words}": "Sonne"})
    page = client.get(f"/presenter/{code}/slides").data.decode()
    assert "data-live-slides" in page and "Sonne" in page and "Pause" in page and "data-print" in page
    assert "Folien mit Ergebnissen" not in client.get(f"/presenter/{code}/present").data.decode()  # only under Export (Ralf, 2026-10-16)
    assert f"/presenter/{code}/slides" in client.get(f"/surveys/{sid}/data").data.decode()


def test_duplicate_a_live_vote_and_the_card_order(app, client):
    code, sid, block = make(app, client, "Original")
    q = add(app, client, sid, block, "single_choice", question="Hauptstadt?", options=["Berlin", "Bonn"])
    client.post(f"/presenter/{code}/control", data={"action": "quiz_set", "item": q, "on": "1", "correct": "1"})
    client.post(f"/presenter/{code}/settings", data={"part": "teilnahme", "qa": "1", "filter": "1"})
    steer(client, code, action="start")
    send(app.test_client(), code, {f"i{q}": "1"})
    card = client.get("/dashboard/").data.decode().split(">Original<")[1].split("</li>")[0]
    # Einstellungen and Duplizieren in the menu "⋯" after the ways (UI/UX audit 2026-10-16, 6)
    order = [card.index(t) for t in ("Folien</a>", "Ergebnisse</a>", "Export", "Einstellungen", "Duplizieren")]  # Folien (once Fragen) first (Ralf, 2026-10-05; E5)
    assert "Präsentieren</a>" not in card and "run-mark" in card and "badge-live-tag" in card  # being presented: „Läuft“
    assert order == sorted(order)                                                     # as on a survey's card
    client.post(f"/surveys/{sid}/duplicate", data={"title": "Kopie"})
    with app.app_context():
        copy = db.session.scalar(db.select(Survey).filter_by(title="Kopie"))
        twin = copy.blocks[0].items[0].id
        assert copy.live is not None and copy.live["qa"] is True and copy.live["screen"] is None
        assert copy.live["quiz"] == {str(twin): {"seconds": None, "correct": ["1"]}} and copy.live["runs"] == []
        assert db.session.scalar(db.select(db.func.count(Response.id)).filter_by(survey_id=copy.id)) == 0


def test_results_per_run_and_the_survey_file(app, client):
    code, sid, block = make(app, client, "Datei")
    q = add(app, client, sid, block, "single_choice", question="Hauptstadt?", options=["Berlin", "Bonn"])
    client.post(f"/presenter/{code}/control", data={"action": "quiz_set", "item": q, "on": "1", "correct": "1", "seconds": "30"})
    client.post(f"/presenter/{code}/settings", data={"part": "teilnahme", "qa": "1", "filter": "1"})
    steer(client, code, action="start")
    send(app.test_client(), code, {f"i{q}": "1"})
    steer(client, code, action="new_run", name="Seminar 2")
    steer(client, code, action="screen", item=q)
    for _ in range(2):
        send(app.test_client(), code, {f"i{q}": "2"})
    page = client.get(f"/surveys/{sid}/results?go=1").data.decode()
    assert 'name="durchgang"' in page and ">Seminar 2</option>" in page
    one = client.get(f"/surveys/{sid}/results?durchgang=Seminar%202").data.decode()
    assert "selected>Seminar 2</option>" in one and "Grundlage: <strong>2</strong>" in one        # only that run (an older address by name)
    assert "Grundlage: <strong>3</strong>" in page                                                # all runs
    raw = client.get(f"/surveys/{sid}/export/survey.json").data
    _read_in(client, None, raw=raw, title="Aus Datei")
    with app.app_context():
        new = db.session.scalar(db.select(Survey).filter_by(title="Aus Datei"))
        twin = new.blocks[0].items[0].id
        assert new.live is not None and new.consent_item is None and new.live["qa"] is True
        assert new.live["quiz"] == {str(twin): {"seconds": 30, "correct": ["1"]}} and new.live["runs"] == []


def test_screen_speaks_the_language_of_the_vote(app, client):
    client.post("/presenter/new", data={"title": "English", "language": "en"})
    client.post("/presenter/1/setup/question", data={"wizard": "skip"})
    client.post("/presenter/1/setup/done", data={"wizard": "next"})
    screen = client.get("/presenter/1/screen").data.decode()
    assert "&#34;answers&#34;: &#34;answers&#34;" in screen and "HIDDEN" in screen
    client.post("/presenter/1/settings", data={"part": "teilnahme", "qa": "1"})
    steer(client, "1", action="qa_screen_on")
    assert client.get("/presenter/1/result.json").json["current"]["question"] == "Questions from the audience"


def test_the_screen_also_without_questions(app, client):
    code, sid, block = make(app, client, "Leer")
    page = client.get(f"/presenter/{code}/present").data.decode()
    assert "Noch keine Folie" in page and 'value="start"' in page
    assert client.post(f"/presenter/{code}/control", data={"action": "start"}).headers["Location"].endswith(f"/presenter/{code}/speaker")
    assert client.get(f"/presenter/{code}/screen").status_code == 200
    assert client.get(f"/presenter/{code}/result.json").json["current"] is None


def test_presenter_view_with_notes_next_and_clock(app, client):
    code, sid, block = make(app, client, "Referent")
    q1 = add(app, client, sid, block, "rating", question="Wie war es?")
    add(app, client, sid, block, "text_input", question="Ein Wort?")
    page = client.get(f"/presenter/{code}?item={q1}").data.decode()
    assert 'value="notes_set"' in page
    r = client.post(f"/presenter/{code}/control", data={"action": "notes_set", "item": q1, "notes": "Auf die Streuung hinweisen"})
    assert r.headers["Location"].endswith(f"?item={q1}")
    steer(client, code, action="start")
    shown = client.get(f"/presenter/{code}/result.json?view=speaker").json
    assert shown["notes"] == "Auf die Streuung hinweisen" and shown["position"] == [1, 2]
    assert shown["next"]["kind"] == "question" and shown["next"]["question"] == "Ein Wort?"
    assert shown["started"]
    # "Als Nächstes" only for the presenter view: the projector would give away the next quiz question
    assert "next" not in client.get(f"/presenter/{code}/result.json").json
    steer(client, code, action="next")
    assert client.get(f"/presenter/{code}/result.json?view=speaker").json["next"] == {"kind": "end"}
    view = client.get(f"/presenter/{code}/speaker").data.decode()
    assert "data-notes" in view and "data-clock" not in view and "data-position" in view and "data-floats" in view and "Ansicht zum Vortragen" in view
    screen = client.get(f"/presenter/{code}/screen").data.decode()
    assert "data-notes" not in screen and "<svg" in screen
    present = client.get(f"/presenter/{code}/present")
    assert f'target="live-presenter-{code}"' in present.data.decode()
    # like a slide show (Ralf, 2026-10-09): one click puts the audience view in full screen on the second screen and
    # opens the presenter view on this one (live.js); the page may place windows and go full screen
    assert "data-two-screens" in present.data.decode() and f'data-speaker="/presenter/{code}/speaker"' in present.data.decode()
    policy = present.headers["Permissions-Policy"]
    assert "window-management=()" not in policy and "fullscreen=()" not in policy


def test_questions_from_the_audience_stay_beside_the_slides(app, client):
    code, sid, block = make(app, client, "Q&A dauerhaft")
    add(app, client, sid, block, "rating", question="Wie war es?")
    steer(client, code, action="start")
    assert client.get(f"/presenter/{code}/result.json").json["qa_side"] == []            # Q&A off: nothing beside
    client.post(f"/presenter/{code}/settings", data={"part": "teilnahme", "qa": "1", "filter": "1"})
    p = app.test_client()
    p.get(f"/live/{code}")
    p.post(f"/live/{code}/qa", data={"text": "Gibt es die Folien?"})
    shown = client.get(f"/presenter/{code}/result.json").json
    assert shown["current"]["type"] == "rating" and [q["text"] for q in shown["qa_side"]] == ["Gibt es die Folien?"]
    assert "data-qa-side" in client.get(f"/presenter/{code}/screen").data.decode()
    qid = shown["qa_side"][0]["id"]
    steer(client, code, action="qa_answer", qa=qid)
    assert client.get(f"/presenter/{code}/result.json").json["qa_side"] == []            # answered: off the column


def test_contact_on_the_privacy_page_and_accessible_controls(app, client):
    code, sid, block = make(app, client, "Kontakt")
    add(app, client, sid, block, "rating", question="Wie war es?")
    assert client.post(f"/presenter/{code}/settings", data={"part": "teilnahme", "contact_email": "kein-mail",
                                                             "autosave": "1"}).get_json()["status"] == "invalid"
    client.post(f"/presenter/{code}/settings", data={"part": "teilnahme", "contact_email": "lehre@example.org", "reactions": "1"})
    with app.app_context():
        uid = db.session.get(Survey, sid).public_key
    assert "lehre@example.org" in app.test_client().get(f"/s/{uid}/privacy").data.decode()
    phone = app.test_client().get(f"/live/{code}").data.decode()
    assert 'aria-live="polite"' in phone and 'aria-label="Daumen hoch"' in phone
    assert 'data-n aria-live="polite"' in client.get(f"/presenter/{code}/screen").data.decode()


def test_projector_screen_hides_the_steering(app, client):
    code, sid, block = make(app, client, "Beamer")
    screen = client.get(f"/presenter/{code}/screen").data.decode()
    assert 'data-steering hidden' in screen and "data-steering-toggle" not in screen and 'data-step="next"' in screen   # no ⚙ (Ralf, 2026-10-03); keys work
    view = client.get(f"/presenter/{code}/speaker").data.decode()
    assert "data-steering hidden" not in view and "data-steering-toggle" not in view


def test_the_presenter_chooses_the_reactions(app, client):
    code, sid, block = make(app, client, "Emojis")
    page = client.get(f"/presenter/{code}/settings").data.decode()
    assert page.count('name="emoji"') == 16 and "Langsamer" in page                     # also critical ones
    client.post(f"/presenter/{code}/settings", data={"part": "teilnahme", "reactions": "1", "emojis_shown": "1",
                                                    "emoji": ["🐇", "👎", "💡", "nicht erlaubt"]})
    phone = app.test_client()
    join = phone.get(f"/live/{code}").data.decode()
    assert 'data-e="👎"' in join and 'data-e="🐇"' in join and 'data-e="❤️"' not in join
    assert phone.post(f"/live/{code}/reaction", data={"e": "❤️"}).status_code == 400          # not offered
    assert phone.post(f"/live/{code}/reaction", data={"e": "🐇"}).json["ok"]
    with app.app_context():
        assert db.session.get(Survey, sid).live["emojis"] == ["👎", "💡", "🐇"]           # in the order offered


def test_quick_reactions_come_in_one_request(app, client):
    code, sid, block = make(app, client, "Schwung")
    phone = app.test_client()
    assert phone.post(f"/live/{code}/reaction", data={"e": ["👍", "👍", "❤️", "👍"]}).json["ok"]
    assert phone.post(f"/live/{code}/reaction", data={"e": ["👍"]}).status_code == 429       # the pause between requests
    assert app.test_client().post(f"/live/{code}/reaction", data={"e": ["👍", "🐇"]}).status_code == 400  # one not offered
    assert app.test_client().post(f"/live/{code}/reaction", data={"e": ["👏"] * 20}).json["ok"]
    with app.app_context():
        from app.models import LiveReaction

        assert db.session.scalar(db.select(db.func.count(LiveReaction.id)).filter_by(survey_id=sid)) == 4 + 8  # at most 8 a request


# --- delete a run, templates, compare runs, team quiz (Ralf, 2026-10-03) -------------------------------------------


def test_delete_an_earlier_run(app, client):
    code, sid, block = make(app, client, "Löschen")
    q = add(app, client, sid, block, "rating", question="Wie war es?")
    client.post(f"/presenter/{code}/settings", data={"part": "teilnahme", "qa": "1"})
    steer(client, code, action="start")
    p = app.test_client()
    send(p, code, {f"i{q}": "4"})
    p.post(f"/live/{code}/qa", data={"text": "Frage aus Runde 1"})
    old = steer(client, code, action="new_run", name="Runde 2")["runs"][0]["id"]
    send(app.test_client(), code, {f"i{q}": "2"})
    assert "Endgültig löschen" in client.get(f"/presenter/{code}/present").data.decode()
    r = client.post(f"/presenter/{code}/control", data={"action": "run_delete", "run": old})
    assert r.headers["Location"].endswith(f"/presenter/{code}/present")
    with app.app_context():
        from app.models import LiveQuestion

        assert db.session.scalar(db.select(db.func.count(Response.id)).filter_by(survey_id=sid)) == 1
        assert db.session.scalar(db.select(db.func.count(LiveQuestion.id))) == 0
        assert db.session.get(Survey, sid).live["runs"] == []
    current = steer(client, code, action="screen", item=q)["run"]["id"]
    client.post(f"/presenter/{code}/control", data={"action": "run_delete", "run": current})   # the current run stays
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id)).filter_by(survey_id=sid)) == 1


def test_wizard_template(app, client):
    client.post("/presenter/new", data={"title": "Vorlage", "language": "de"})
    page = client.get("/presenter/1/setup/question").data.decode()
    assert "Eine Vorlage nehmen" in page and 'value="feedback"' in page
    r = client.post("/presenter/1/setup/question", data={"wizard": "template", "template": "feedback"})
    assert r.headers["Location"].endswith("/presenter/1/setup/done")
    with app.app_context():
        rows = [(i.type, i.text("question", "de")) for i in db.session.get(Survey, 1).blocks[0].items]
    assert rows[0] == ("rating", "Wie hat Ihnen die heutige Sitzung gefallen?") and len(rows) == 4   # Sie: language "de"


def test_compare_two_runs_on_the_slides(app, client):
    code, sid, block = make(app, client, "Vergleich")
    q = add(app, client, sid, block, "rating", question="Wie war es?")
    steer(client, code, action="start")
    send(app.test_client(), code, {f"i{q}": "4"})
    first = steer(client, code, action="new_run", name="Semester 2")["runs"][0]["id"]
    for v in ("2", "3"):
        send(app.test_client(), code, {f"i{q}": v})
    page = client.get(f"/presenter/{code}/slides?vergleich={first}").data.decode()
    assert "vergleichen mit" in page and "&#34;compare&#34;" in page and "Semester 2" in page


def test_team_quiz(app, client):
    code, sid, block = make(app, client, "Teams")
    q = add(app, client, sid, block, "single_choice", question="Hauptstadt?", options=["Berlin", "Bonn"])
    client.post(f"/presenter/{code}/control", data={"action": "quiz_set", "item": q, "on": "1", "correct": "1"})
    client.post(f"/presenter/{code}/settings", data={"part": "teilnahme", "teams": "Rot\nBlau\n\nRot"})
    with app.app_context():
        assert db.session.get(Survey, sid).live["teams"] == ["Rot", "Blau"]
    steer(client, code, action="start")
    for team, answer in (("Rot", "1"), ("Rot", "2"), ("Blau", "1")):
        p = app.test_client()
        assert 'name="team"' in p.get(f"/live/{code}").data.decode()
        p.post(f"/live/{code}/name", data={"name": "", "team": team})
        send(p, code, {f"i{q}": answer})
    steer(client, code, action="board_on")
    board = client.get(f"/presenter/{code}/result.json").json["current"]
    assert board["teams"] == [{"name": "Blau", "points": 1000, "members": 1, "place": 1},
                              {"name": "Rot", "points": 500, "members": 2, "place": 2}]


def test_export_page_of_a_live_vote_offers_only_what_it_needs(app, client):
    code, sid, block = make(app, client, "Export live")
    page = client.get(f"/surveys/{sid}/data").data.decode()
    assert f"/presenter/{code}/audience-questions.csv" in page and f"/presenter/{code}/slides" in page and "durchgang" in page
    for gone in ('id="pool"', "Beschreibung erstellen", "(.sav)", "(.omv)", "Fragebogen für Papier", 'id="prereg"', 'id="zenodo"',
                 'id="erasure"', 'id="contact-data"', "Kommentare aus Testläufen"):
        assert gone not in page, gone
    assert 'id="export-form"' in page and 'id="export-file"' in page                  # the CSV still has its form


def test_audience_questions_on_the_results_page_not_in_the_presenter_view(app, client):
    code, sid, block = make(app, client, "Fragen Ergebnis")
    client.post(f"/presenter/{code}/settings", data={"part": "teilnahme", "qa": "1", "filter": "1"})
    steer(client, code, action="start")
    p = app.test_client()
    p.get(f"/live/{code}")
    p.post(f"/live/{code}/qa", data={"text": "Was ist ein p-Wert?"})
    page = client.get(f"/surveys/{sid}/results?go=1").data.decode()
    assert 'id="audience-questions"' in page and "Was ist ein p-Wert?" in page
    assert "fragen-publikum.csv" not in client.get(f"/presenter/{code}/speaker").data.decode()
    assert 'id="retention"' not in client.get(f"/surveys/{sid}/data").data.decode()


def test_no_project_field(app, client):
    # the groups tell who a live vote belongs to (Ralf, 2026-10-04)
    assert 'name="project"' not in client.get("/presenter/new").data.decode()


def test_the_projector_fits_its_slide(app, client):
    # the audience view is made smaller until the slide fits the window, a long wall or cloud says "+ 34 weitere"
    # (live.js fit; Ralf, 2026-10-12): the part to scale and the word in the vote's language
    code, sid, block = make(app, client, "Beamer")
    screen = client.get(f"/presenter/{code}/screen").data.decode()
    assert 'class="live-fit" data-fit' in screen and "+ {n} weitere" in screen
    js = client.get("/static/live.js").data.decode()
    assert "FIT_MIN = 0.6" in js and "presenterView" in js                       # to 60 %, not in the presenter view


def test_the_session_keeps_only_the_current_run(app, client):
    """Code review 2026-10-15, I9: the keys of earlier runs leave the cookie session when the device joins a new run,
    so it does not grow past the 4 KB a browser keeps."""
    code, sid, block = make(app, client, "Viele Durchgänge")
    q = add(app, client, sid, block, "rating", question="Wie war es?")
    steer(client, code, action="start")
    p = app.test_client()
    runs = []
    for n in range(3):
        if n:
            runs.append(steer(client, code, action="new_run", name=f"Lauf {n}")["run"]["id"])
        else:
            runs.append(steer(client, code, action="screen", item=q)["run"]["id"])
        assert send(p, code, {f"i{q}": "4"}).status_code == 200
    with p.session_transaction() as s:
        live = sorted(k for k in s if k.startswith(f"live:{sid}:"))
    assert live == [f"live:{sid}:{runs[-1]}", f"live:{sid}:{runs[-1]}:pid"]


def test_presenter_keys_leave_a_focused_control_alone():
    """Code review 2026-10-15, I7: the Space bar on a focused button presses it instead of turning the slide."""
    from pathlib import Path

    js = (Path(__file__).parents[2] / "app" / "static" / "live.js").read_text(encoding="utf-8")
    keys = js[js.index('document.addEventListener("keydown"', js.index("toggleCorner")):]
    assert 'at.closest("button, a, [role=button]")' in keys and 'at.closest("input, textarea, select, [contenteditable]")' in keys
    assert keys.index("[role=button]") < keys.index('step("next")')


def test_word_cloud_sets_several_words_and_a_limit_on_the_question(app, client):
    """Several words one after the other and at most so many per person: set on the word cloud, not on Einstellungen
    (Ralf, 2026-10-16); after the last one the phone thanks instead of showing the field again."""
    code, sid, block = make(app, client)
    q = add(app, client, sid, block, "text_input", question="Begriffe?")
    editor = client.get(f"/surveys/{sid}/editor/items/{q}?embed=1").data.decode()
    assert 'name="live_multi"' in editor and 'name="live_max"' in editor
    assert 'name="multi"' not in client.get(f"/presenter/{code}/settings").data.decode()
    client.post(f"/surveys/{sid}/editor/items/{q}?embed=1", data={"code": "q", "question": "Begriffe?", "live_multi_shown": "1",
                                                                   "live_multi": "1", "live_max": "2", "max_length": "2000"})
    with app.app_context():
        assert db.session.get(Item, q).config["live_multi"] is True and db.session.get(Item, q).config["live_max"] == 2
    steer(client, code, action="start")
    p = app.test_client()
    page = p.get(f"/live/{code}").data.decode()
    assert "data-multi" in page and "Noch 2 möglich." in page
    r = send(p, code, {f"i{q}": "eins"}).json
    assert {k: r[k] for k in ("ok", "key", "left")} == {"ok": True, "key": f"q-{q}-1", "left": 1}
    r = send(p, code, {f"i{q}": "zwei"}).json
    assert r["ok"] and r["key"] == f"full-{q}" and key(p, code) == f"full-{q}"
    late = p.post(f"/live/{code}/answer", data={"ref": f"q-{q}", f"i{q}": "drei"})
    assert late.status_code == 409 and late.json["error"] == "full"
    with app.app_context():
        assert len(db.session.scalars(db.select(Response).filter_by(survey_id=sid)).all()) == 2
    # the tick taken off again: one answer per device, the limit no longer counts
    client.post(f"/surveys/{sid}/editor/items/{q}?embed=1", data={"code": "q", "question": "Begriffe?", "live_multi_shown": "1",
                                                                   "live_max": "", "max_length": "2000"})
    with app.app_context():
        assert db.session.get(Item, q).config["live_multi"] is False and db.session.get(Item, q).config["live_max"] is None
    assert key(p, code) in (f"answered-{q}", "end")


def test_word_cloud_refuses_repeats_lists_own_words_and_takes_them_back(app, client):
    """One device cannot push a word up with repeats; the phone lists its own words and × takes one back, which
    frees its place under the limit (Ralf, 2026-10-16)."""
    code, sid, block = make(app, client)
    q = add(app, client, sid, block, "text_input", question="Tiere?")
    with app.app_context():
        item = db.session.get(Item, q)
        item.config = {**item.config, "live_multi": True, "live_max": 2}
        db.session.commit()
    steer(client, code, action="start")
    p = app.test_client()
    first = send(p, code, {f"i{q}": "Geier"}).json
    assert first["ok"] and first["word"]["text"] == "Geier"
    again = send(p, code, {f"i{q}": "  geier "})
    assert again.status_code == 400 and "schon gesendet" in again.json["errors"][str(q)]
    second = send(p, code, {f"i{q}": "Adler"}).json
    assert second["key"] == f"full-{q}"
    page = p.get(f"/live/{code}").data.decode()
    assert "Geier" in page and "Adler" in page and page.count('class="live-withdraw"') == 2 and "so viele Antworten" in page
    other = app.test_client()                                          # another phone may send the same word
    assert send(other, code, {f"i{q}": "Geier"}).json["ok"]
    # someone else's word cannot be taken back from this phone
    assert p.post(f"/live/{code}/withdraw", data={"ref": f"q-{q}", "word": other_id(app, sid, "Geier", second=True)}).status_code == 404
    r = p.post(f"/live/{code}/withdraw", data={"ref": f"q-{q}", "word": second["word"]["id"]}).json  # a further word: its row goes
    assert {k: r[k] for k in ("ok", "key", "left")} == {"ok": True, "key": f"q-{q}-1", "left": 1}
    r = p.post(f"/live/{code}/withdraw", data={"ref": f"q-{q}", "word": first["word"]["id"]}).json   # the first: the answer goes
    assert r["key"] == f"q-{q}-0" and r["left"] == 2
    with app.app_context():
        rows = db.session.scalars(db.select(Response).filter_by(survey_id=sid)).all()
        assert sorted(str((x.answers or {}).get(str(q))) for x in rows) == ["Geier", "None"]
    assert send(p, code, {f"i{q}": "Geier"}).json["ok"]               # taken back, it may be sent again
    steer(client, code, action="results_on")
    assert client.get(f"/presenter/{code}/result.json").json["current"]["rows"][0][:2] == ["Geier", 2]


def other_id(app, sid, word, second=False):
    with app.app_context():
        rows = [r for r in db.session.scalars(db.select(Response).filter_by(survey_id=sid).order_by(Response.id))
                if (r.answers or {}) and word in (r.answers or {}).values()]
        return rows[-1 if second else 0].id


def test_typed_other_answers_reach_the_screen_and_the_slides(app, client):
    """Ralf, 2026-10-16: what was typed at "Anderes, und zwar …" of a single or multiple choice showed nowhere."""
    code, sid, block = make(app, client)
    q = add(app, client, sid, block, "multiple_choice", question="Womit kommen Sie?", options=["Rad", "Bus", "Anderes"])
    with app.app_context():
        item = db.session.get(Survey, sid).blocks[0].items[-1]
        item.config = {**item.config, "options": [{**o, "text": True} if o["code"] == "3" else o for o in item.config["options"]]}
        db.session.commit()
    steer(client, code, action="start")
    people = [app.test_client() for _ in range(3)]
    for p, data in zip(people, [{f"i{q}": ["1", "3"], f"i{q}_t_o3": "Roller"}, {f"i{q}": "3", f"i{q}_t_o3": "roller"},
                                {f"i{q}": "3", f"i{q}_t_o3": "Zu Fuß"}]):
        assert send(p, code, data).json["ok"]
    current = client.get(f"/presenter/{code}/result.json").json["current"]
    assert current["rows"][0] == ["Anderes", 3]
    assert [(t, n) for t, n, _ in current["notes"]] == [("Roller", 2), ("Zu Fuß", 1)]     # counted together as in the cloud
    mod = client.get(f"/presenter/{code}/moderation.json").json                          # the presenter can hide one
    assert mod["item"] == q and {e["text"] for e in mod["entries"]} == {"Roller", "Zu Fuß"}
    steer(client, code, action="hide", key="zu fuß")
    assert [t for t, _, _ in client.get(f"/presenter/{code}/result.json").json["current"]["notes"]] == ["Roller"]
    assert "Roller" in client.get(f"/presenter/{code}/slides").data.decode()
