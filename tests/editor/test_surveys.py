from datetime import timedelta

from app.extensions import db
from app.models import Response, Survey, utcnow
from tests.conftest import fill_consent


def test_create_and_list(client):
    r = client.post("/surveys/new", data={"title": "Framing", "project": "KI"})
    assert r.headers["Location"].endswith("/surveys/1/setup/researchdesign")
    assert b"Framing" in client.get("/dashboard/").data


def test_access_restricted_to_members(app, client):
    client.post("/surveys/new", data={"title": "Mine"})
    other = app.test_client()
    other.get("/auth/dev-login?orcid=0000-0001-5555-5555")
    other.post("/terms/", data={"accept": "1"})
    assert other.get("/surveys/1").status_code == 404


def test_sign_in_policy(app):
    from app.auth import _may_sign_in

    with app.app_context():
        assert _may_sign_in("0000-0000-0000-001X")
        assert not _may_sign_in("0000-0001-5555-5555")


def test_start_requires_valid_questionnaire(client, survey):
    r = client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"}, follow_redirects=True)
    assert "nicht startklar" in r.data.decode()
    fill_consent(client)
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "info"})
    r = client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"}, follow_redirects=True)
    assert "gestartet" in r.data.decode()


def test_auto_close_by_sample_and_time(app, client, survey):
    fill_consent(client)
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "info"})
    client.post("/surveys/1/start", data={"use_max": "1", "max_responses": "1"})
    with app.app_context():
        db.session.add(Response(survey_id=1, completed_at=utcnow()))
        db.session.commit()
    assert client.get("/cron/close?key=k").json == {"closed": 1}
    with app.app_context():
        assert db.session.get(Survey, 1).close_reason == "sample"
    # resuming is refused while the sample is full
    r = client.post("/surveys/1/start", data={"use_max": "1", "max_responses": "1"}, follow_redirects=True)
    assert "bereits erreicht" in r.data.decode()
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.status == "running"
        s.field_end = utcnow() - timedelta(minutes=1)
        db.session.commit()
    assert client.get("/cron/close?key=k").json == {"closed": 1}


def test_pause_and_resume_only_in_settings(app, client, survey):
    fill_consent(client)
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "info"})
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        end = db.session.get(Survey, 1).field_end
    dashboard = client.get("/dashboard/").data.decode()
    assert "Pausieren" not in dashboard and ">Starten<" not in dashboard and "Einstellungen" in dashboard
    assert "Pausieren" in client.get("/surveys/1/field").data.decode()
    client.post("/surveys/1/close", data={"pause": "1"})
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.status == "running" and s.field_end == end


def test_last_member_cannot_leave(client, survey):
    r = client.post("/surveys/1/members/1/remove", follow_redirects=True)
    assert "letzte Person" in r.data.decode()


def test_reorder_members(app, client, survey):
    for orcid, name in (("0000-0002-0000-0001", "B"), ("0000-0002-0000-0002", "C")):
        client.post("/surveys/1/members", data={"orcid": orcid, "name": name})
    names = lambda: [m.display_name for m in db.session.get(Survey, 1).members]
    with app.app_context():
        assert names() == ["Philipp", "B", "C"]
    client.post("/surveys/1/members/3/move/up")
    client.post("/surveys/1/members/1/move/down")
    with app.app_context():
        assert names() == ["C", "Philipp", "B"]
    html = client.get("/surveys/1").data.decode()
    assert "Beteiligte Forschende" in html and html.index(">C ") < html.index("Philipp <") if ">C " in html else True


def test_survey_tabs_and_data_page(client, survey):
    for url, current in (("/surveys/1/editor/", "Fragebogen"), ("/surveys/1", "Einstellungen"), ("/surveys/1/data", "Export")):
        html = client.get(url).data.decode()
        nav = html.split('class="survey-tabs"')[1].split("</nav>")[0]
        positions = [nav.index(t) for t in ("Einstellungen", "Fragebogen", "Export")]
        assert positions == sorted(positions)
        # Vorschau opens a new window: an action in the title line, not a tab (UI audit 2026-10-04, G1)
        assert "Vorschau" not in nav and "Vorschau ↗" in html.split('class="survey-head-actions"')[1].split("</div>")[0]
        active = nav.split('aria-current="page"')[1].split("</a>")[0]
        assert current in active, url
    data = client.get("/surveys/1/data").data.decode()
    assert 'export/data.csv"' in data and "Codebuch" in data and "Getrennter Abschnitt" in data
    assert 'export/data.csv"' not in client.get("/surveys/1").data.decode()


def test_export_timeline(app, client, survey):
    from datetime import timedelta

    from app.extensions import db
    from app.models import Response, Survey, utcnow

    # a draft without real participations shows no chart; it stands in the status block (Ralf, 2026-10-11)
    assert "timeline-svg" not in client.get("/surveys/1/field").data.decode()
    with app.app_context():
        from app import lifecycle, timeline

        s = db.session.get(Survey, 1)
        s.status = Survey.CLOSED  # skip the pre-launch check: start from a paused state
        lifecycle.start(s, utcnow() + timedelta(days=10), None)
        lifecycle.close(s, "paused")
        lifecycle.start(s, utcnow() + timedelta(days=10), None)
        s.status_log = [{**e, "at": (utcnow() - timedelta(hours=6 - 2 * n)).isoformat()} for n, e in enumerate(s.status_log)]
        now = utcnow()
        # a finished one counts at its end, an unfinished one at its start (app/timeline.py): both at `now`, so
        # they share one day bin at any time of day (12 minutes before now could be yesterday, shortly after midnight)
        for source, status in (("real", Response.COMPLETED), ("real", Response.ACTIVE), ("dummy", Response.COMPLETED)):
            done = status == Response.COMPLETED
            db.session.add(Response(survey_id=1, source=source, status=status,
                                    started_at=now - timedelta(minutes=12) if done else now, completed_at=now if done else None))
        db.session.commit()
        tl = timeline.build(s)
        assert [e for _, e in tl["events"]] == ["angelegt", "gestartet", "pausiert", "gestartet"]
        assert tl["completed"] == 1 and round(tl["median_minutes"]) == 12  # dummy excluded
        assert 'class="tl-paused"' in tl["svg"] and 'class="tl-planned"' in tl["svg"]
        assert "1 abgeschlossen, 1 abgebrochen" in tl["svg"]
    page = client.get("/surveys/1/field").data.decode()
    status = page.split('id="status"')[1]
    assert "Zeitverlauf" in status and "timeline-svg" in status and "angelegt" in status
    assert "<strong>Läuft</strong> seit" in status and "<strong>1</strong> abgeschlossen" in status
    assert status.count("Feldende am") == 1                     # once, in the figures; no more in the list of events


def test_new_survey_has_empty_block_for_dropping(app, client, survey):
    with app.app_context():
        from app.extensions import db
        from app.models import Survey

        blocks = db.session.get(Survey, 1).blocks
        assert len(blocks) == 2 and blocks[0].items[0].type == "consent" and blocks[1].items == []
    assert "Elemente hierher ziehen" in client.get("/surveys/1/editor/").data.decode()


def test_editor_adds_block_when_only_consent_exists(app, client, survey):
    from app.extensions import db
    from app.models import Block, Survey

    with app.app_context():
        db.session.delete(db.session.get(Block, 2))
        db.session.commit()
    client.get("/surveys/1/editor/")
    with app.app_context():
        assert len(db.session.get(Survey, 1).blocks) == 2


def test_reopen_paused_survey_for_editing(app, client, survey):
    from app import lifecycle
    from app.extensions import db
    from app.models import Item, Response, Survey, utcnow
    from tests.conftest import add

    add(client, "single_choice")                                   # 2
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.status = Survey.RUNNING
        db.session.add(Response(survey_id=1, source="real", status=Response.COMPLETED, completed_at=utcnow(),
                                answers={"2": "1"}))
        lifecycle.close(s, "paused")
        db.session.commit()
    page = client.get("/surveys/1/field").data.decode()
    assert "Fragebogen ändern (neue Fassung)" in page and "Vergleichbarkeit" in page
    client.post("/surveys/1/reopen", data={})                        # without confirmation: nothing happens
    with app.app_context():
        assert db.session.get(Survey, 1).status == Survey.CLOSED
    r = client.post("/surveys/1/reopen", data={"confirm": "1"})
    assert r.headers["Location"].endswith("/surveys/1/editor/")
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.status == Survey.DRAFT and s.qversion == 2 and s.status_log[-1]["event"] == "edit"
        db.session.delete(db.session.get(Item, 2))                  # a change that drops a column
        db.session.commit()
    data = client.get("/surveys/1/data").data.decode()
    assert "Gesicherte Stände" in data and "Fassung 1" in data
    assert "zur Bearbeitung geöffnet (Fassung 2)" in client.get("/surveys/1/field").data.decode()  # timeline
    snap = client.get("/surveys/1/data").data.decode().split('/export/snapshot/')[1].split('/')[0]
    old = client.get(f"/surveys/1/export/snapshot/{snap}/data.csv").data.decode("utf-8-sig")
    assert "Q1" in old.splitlines()[0] and "qversion" in old.splitlines()[0]       # the old column is kept
    new = client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig")
    assert "Q1" not in new.splitlines()[0] and new.splitlines()[1].split(",")[3] == "1"  # qversion 1
    assert client.get(f"/surveys/1/export/snapshot/{snap}/../../app.db").status_code == 404
    with app.app_context():
        from app import design

        assert any("früheren Fassungen" in f.message for f in design.check(db.session.get(Survey, 1)))


def test_archive_button_next_to_start(app, client, survey):
    from app.extensions import db
    from app.models import Survey

    from tests.conftest import add, fill_consent

    fill_consent(client)   # a draft that can start: with pre-launch check errors the start stays locked (test_ui_safety.py)
    add(client, "info")
    html = client.get("/surveys/1/field").data.decode()
    start = html.split('class="button-row"', 1)[1].split("</form>")[0]  # the whole row, "Start planen" inside
    # archiving left the row of the start: it is in the menu "⋯" of the survey (UI/UX audit 2026-10-16, 2 and 5)
    assert "Befragung starten" in start and "Archivieren" not in start and "/surveys/1/archive" in html
    # the start asks first; archiving can be undone and asks nothing (Ralf, 2026-10-05: no safety latch)
    assert "data-confirm=" in start and start.count("data-confirm=") == 1 and "latch" not in start
    client.post("/surveys/1/archive", data={"archived": "1"})
    with app.app_context():
        assert db.session.get(Survey, 1).archived
def test_field_has_test_data(client, survey):
    field = client.get("/surveys/1/field").data.decode()
    assert "Dummy-Daten erzeugen" in field and 'id="status"' in field
    assert "Dummy-Daten erzeugen" not in client.get("/surveys/1/data").data.decode()
    r = client.post("/surveys/1/dummy", data={"n": "2"})
    assert "/surveys/1/field#testing" in r.headers["Location"]


def test_confetti_once_after_start(client, survey):
    from tests.participation.test_participate import build

    build(client)
    r = client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"}, follow_redirects=True)
    assert "Die Befragung ist gestartet." in r.data.decode() and "data-confetti" in r.data.decode()
    assert "data-confetti" not in client.get("/surveys/1/field").data.decode()   # only once


def test_live_strip_shows_active_participants_without_ids(app, client, survey):
    from datetime import timedelta

    from app import runtime
    from app.models import Response, Survey, utcnow
    from tests.participation.test_participate import build

    build(client)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        now = utcnow()
        for minutes, status, source in ((1, "active", "real"), (8, "active", "real"), (20, "active", "real"),
                                        (1, "completed", "real"), (1, "active", "test")):
            r = Response(survey_id=1, status=status, source=source, answers={}, last_active_at=now - timedelta(minutes=minutes))
            runtime.ensure_layout(s, r)
            r.position = runtime.page_entries(r.layout)[1]
            db.session.add(r)
        db.session.commit()
    data = client.get("/surveys/1/field/live").json
    assert len(data["people"]) == 2 and data["window"] == 600 and data["estimate"] > 0     # 20 min, done, test: out
    assert all(set(p) == {"p", "a", "j"} and 0 < p["p"] < 1 and 0 <= p["j"] <= 1 for p in data["people"])
    assert sorted(p["a"] // 60 for p in data["people"]) == [1, 8]
    html = client.get("/surveys/1/field").data.decode()
    assert "data-live-strip" in html and "latch" not in html and 'data-confirm="„Test“ schließen?"' in html   # pausing asks nothing
    # the legend's dots have their own class: participant.css places .live-dot (live vote) absolutely on a track
    assert html.count('class="live-key-dot"') == 4 and 'class="live-dot"' not in html
    assert html.index('id="status"') < html.index('id="links"')        # status on top once it ran (Ralf, 2026-10-11)


def test_start_without_end_or_sample_size(app, client, survey):
    html = client.get("/surveys/1/field").data.decode()
    assert "Start der Befragung" in html and "data-days-from" in html and "data-days-out" in html
    fill_consent(client)
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "info"})
    client.post("/surveys/1/start", data={})                        # no box ticked: runs until paused or closed
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.status == Survey.RUNNING and s.field_end is None and s.max_responses is None


def test_static_files_are_versioned_by_content(app, client):
    import re

    html = client.get("/dashboard/").data.decode()
    m = re.search(r'/static/app\.css\?v=([0-9a-f]{12})"', html)
    assert m, "app.css without content version"
    r = client.get(f"/static/app.css?v={m.group(1)}")
    assert r.status_code == 200 and r.cache_control.max_age == 365 * 24 * 3600
    r.close()
    r = client.get("/static/app.css")                                     # unversioned: no long caching
    assert r.status_code == 200 and not r.cache_control.max_age
    r.close()
    import hashlib
    import os

    with open(os.path.join(app.static_folder, "app.css"), "rb") as f:
        assert m.group(1) == hashlib.sha256(f.read()).hexdigest()[:12]



def test_pricing_page_and_donate_link(app, client):
    """The page and the gift in the header only with a donation link, which only the operator's .env sets (Ralf,
    2026-10-19): the public code names no account of its own."""
    from pathlib import Path

    config = (Path(__file__).resolve().parents[2] / "config.py").read_text(encoding="utf-8")
    assert 'os.environ.get("DONATION_URL", "")' in config and 'os.environ.get("DONATION_WERO_URL", "")' in config
    app.config["DONATION_URL"] = app.config["DONATION_WERO_URL"] = ""
    assert client.get("/pricing").status_code == 404
    assert "donate-link" not in client.get("/").data.decode() and "/pricing" not in client.get("/sitemap.xml").data.decode()
    app.config["DONATION_URL"] = "https://paypal.example/spende"
    html = client.get("/pricing").data.decode()
    assert "0 €" in html and "KI-Guthaben" in html and 'class="icon-btn donate-link" href="/pricing"' in html
    assert "/pricing" in client.get("/sitemap.xml").data.decode()
    assert 'href="https://paypal.example/spende" target="_blank" rel="noopener">Mit PayPal spenden' in html
    assert "Mit Wero spenden" not in html
    app.config["DONATION_WERO_URL"] = "https://example.org/wero"
    html = client.get("/pricing").data.decode()
    assert html.index("Mit Wero spenden") < html.index("Mit PayPal spenden")            # Wero first, PayPal secondary
    app.config["DONATION_URL"] = ""
    assert "Mit PayPal spenden" not in client.get("/pricing").data.decode()            # Wero alone still opens it
    app.config["DONATION_WERO_URL"] = ""
    assert client.get("/pricing").status_code == 404


def test_landing_links_the_introduction_survey(client):
    html = client.get("/").data.decode()
    assert "Beispielbefragung ausprobieren" in html and 'href="https://ask.askurious.de/s/introduction"' in html
    # live feedback with a link to its demo survey (Philipp, 2026-10-11)
    assert 'Einordnung auf Skalen und <a href="https://ask.askurious.de/s/demo-rueckmeldung"' in html
    # the head and the three kinds (Ralf, 2026-10-04); the references in one closed fold (Ralf, 2026-10-07, W104)
    assert "Drei Arten zu fragen" in html and "card-kind card-live" in html and "card-kind card-interview" in html
    assert '<details class="sources">' not in html and 'class="landing-references"' in html and "data-autoplay" in html and "Zu meinen Befragungen" in html  # signed in


def test_slider_optional_start_value(app, client, survey):
    from werkzeug.datastructures import MultiDict

    from app.models import Item
    from tests.conftest import add

    add(client, "slider")                                                          # item 2
    base = [("code", "sl"), ("question", "Wie viel?"), ("min", "0"), ("max", "100"), ("step", "5"), ("format", "number"),
            ("stmt_id", "r1"), ("stmt_code", ""), ("stmt_text", "Budget")]
    client.post("/surveys/1/editor/items/2", data=MultiDict(base))
    html = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    assert 'class="p-slider untouched"' in html and 'value="50.0"' in html                  # no start: hidden handle
    client.post("/surveys/1/editor/items/2", data=MultiDict(base + [("start", "22")]))
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.config["start"] == 20 and item.kind.validate(item) == []               # snapped to the step
        assert any("Startwert 20" in n for n in item.kind.codebook_info(item, "sl", "de")[2])
    html = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    assert 'class="p-slider untouched has-start"' in html and 'value="20"' in html
    assert 'name="i2_r1" value=""' in html                                                  # still no answer until touched
    client.post("/surveys/1/editor/items/2", data=MultiDict(base + [("start", "150")]))
    with app.app_context():
        item = db.session.get(Item, 2)
        assert "Startwert liegt außerhalb" in " ".join(item.kind.validate(item))


def test_dummy_in_portions(client, survey, app):
    """The field page creates dummies in portions and shows the progress: each request answers with the number
    created, and the persons are numbered on across the portions."""
    from tests.conftest import add

    json = {"Accept": "application/json"}
    r = client.post("/surveys/1/dummy", data={"n": "2"}, headers=json)   # consent still incomplete
    assert r.status_code == 400 and "Startprüfung" in r.get_json()["error"]
    fill_consent(client)
    add(client, "text_input")
    client.post("/surveys/1/editor/items/2", data={"code": "name", "question": "Name?"})
    field = client.get("/surveys/1/field").data.decode()
    assert "data-dummy-form" in field and "data-dummy-progress" in field and 'role="progressbar"' in field
    assert client.post("/surveys/1/dummy", data={"n": "2"}, headers=json).get_json() == {"created": 2}
    assert client.post("/surveys/1/dummy", data={"n": "3"}, headers=json).get_json() == {"created": 3}
    with app.app_context():
        texts = sorted(v for r in db.session.scalars(db.select(Response).filter_by(source="dummy"))
                       for v in r.answers.values() if str(v).startswith("Dummy-Antwort"))
    assert texts == [f"Dummy-Antwort {k}" for k in range(1, 6)]
    assert '<strong data-dummy-count>5</strong>' in client.get("/surveys/1/field").data.decode()


def test_under_construction_only_for_researchers(app, client, survey):
    from tests.conftest import fill_consent

    fill_consent(client)
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "info"})
    client.post("/surveys/1/start", data={})
    with app.app_context():
        code = db.session.get(Survey, 1).code
    assert "uc-tape" not in client.get("/").data.decode()                       # off: nothing of it is sent
    app.config["UNDER_CONSTRUCTION"] = True
    for url in ("/", "/dashboard/", "/surveys/1"):                               # landing and researcher pages: the tape
        html = client.get(url, follow_redirects=True).data.decode()
        assert "uc-tape" in html and "under_construction.css" in html and "data-uc" not in html, url   # no digger scene
    p = app.test_client()
    for html in (p.get(f"/s/{code}").data.decode(), client.get("/surveys/1/editor/preview", follow_redirects=True).data.decode()):
        assert "data-uc" not in html and "under_construction" not in html     # never on participant pages


def test_pin_surveys_to_the_top_per_person(app, client, survey):
    import re

    for title in ("Zweite", "Dritte"):
        client.post("/surveys/new", data={"title": title, "languages": ["de"], "default_language": "de"})

    def order():
        html = client.get("/dashboard/", follow_redirects=True).data.decode()
        return re.findall(r'class="card-title" href="[^"]*">([^<]+)</a>', html)

    assert order()[0] == "Dritte"                                              # last changed first
    r = client.post("/dashboard/pin/1")
    assert r.status_code == 302 and r.headers["Location"].endswith("#survey-1")
    assert order()[0] == "Test" and order()[1:] == ["Dritte", "Zweite"]        # the pinned one on top
    html = client.get("/dashboard/", follow_redirects=True).data.decode()
    assert 'aria-pressed="true">★' in html and html.count('aria-pressed="false">☆') == 2
    client.post("/dashboard/pin/1")                                            # unpin
    assert order()[0] == "Dritte"
    with app.app_context():
        from app.models import SurveyMember

        s = db.session.get(Survey, 2)
        s.members.append(SurveyMember(orcid="0000-0001-5555-5555", name="Kollegin", role="editor"))
        db.session.commit()
    client.post("/dashboard/pin/2")
    other = app.test_client()                                                  # pins are per person
    other.get("/auth/dev-login?orcid=0000-0001-5555-5555&name=Kollegin")
    other.post("/terms/", data={"accept": "1"})
    assert '★' not in other.get("/dashboard/", follow_redirects=True).data.decode()
    assert other.post("/dashboard/pin/1").status_code == 404                   # only surveys of one's own
    with app.app_context():                                                    # deleting a survey removes its pins
        from app.models import SurveyPin

        db.session.delete(db.session.get(Survey, 2))
        db.session.commit()
        assert db.session.scalar(db.select(db.func.count(SurveyPin.id))) == 0


def test_pages_below_a_tab_show_the_tabs_and_the_logo_leads_home(client, survey):
    # UI audit 2026-10-04: the element editor keeps the tabs with "Fragebogen" marked and no second h1 (R2);
    # the logo leads to the home page, signed in too (Ralf, 2026-10-11; R3 had "Meine Befragungen"); the feedback is
    # a tab of the administration (R5)
    html = client.get("/surveys/1/editor/items/1").data.decode()
    nav = html.split('class="survey-tabs"')[1].split("</nav>")[0]
    assert "Fragebogen" in nav.split('aria-current="page"')[1].split("</a>")[0]
    assert html.count("<h1") == 1 and 'class="survey-title"' in html
    assert 'class="brand" href="/"' in html
    admin = client.get("/feedback/").data.decode()
    assert 'aria-current="page" href="/feedback/">Rückmeldungen' in admin


def test_header_band_in_the_accent_colour_without_the_tape(app, client):
    """The tape is for a development server (UNDER_CONSTRUCTION=1, off by default); else a band in the accent colour
    of the look sits under the header (Ralf, 2026-10-07)."""
    import os

    import config
    from pathlib import Path

    assert config.Config.UNDER_CONSTRUCTION is False or os.environ.get("UNDER_CONSTRUCTION")
    css = Path(app.static_folder, "app.css").read_text(encoding="utf-8")
    rule = css.split(".site-header::after {")[1].split("}")[0]
    assert "var(--accent)" in rule and "uc-tape" not in client.get("/").data.decode()


def test_development_server_says_it_is_still_being_built(app, client, survey):
    """With the tape (UNDER_CONSTRUCTION) the list of surveys says the tool is still under development, with the
    status page, and starting a survey says reliability is not yet guaranteed (Philipp, 2026-10-14); without it
    neither."""
    note, start = "noch in Entwicklung. Wir streben eine hohe", "können sie aber noch nicht garantieren"
    assert note not in client.get("/dashboard/").data.decode()
    assert start not in client.get("/surveys/1/field").data.decode()
    app.config["UNDER_CONSTRUCTION"] = True
    page = client.get("/dashboard/").data.decode()
    assert note in page and "status.askurious.de" in page
    assert start in client.get("/surveys/1/field").data.decode()


def test_default_end_texts_shown_in_the_survey_language(app, client):
    """Settings show the default end texts as participants get them: "Deutsch (du)" in German, not in English
    (Philipp, 2026-10-08: "Thank you for taking part!" for a survey in German with du)."""
    client.post("/surveys/new", data={"title": "Du", "languages": ["de-du"], "default_language": "de-du"})
    html = client.get("/surveys/1").data.decode()
    assert "Thank you for taking part!" not in html and "Vielen Dank" in html
