import csv
import io

from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models import Response, Survey
from tests.conftest import CONSENT_OK, add, fill_consent


def build(client):
    """block 1: consent (item 1) | block 2: likert (reverse s2, randomized) + age | page break | info"""
    fill_consent(client)
    add(client, "likert")                                   # 2
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "TA"), ("answer_mode", "required"), ("points", "5"), ("labeling", "full"), ("randomize", "1"),
        ("stmt_id", "s1"), ("stmt_code", ""), ("stmt_text", "A"),
        ("stmt_id", "s2"), ("stmt_code", ""), ("stmt_text", "B"), ("stmt_reverse", "s2")]))
    add(client, "text_input")                               # 3
    client.post("/surveys/1/editor/items/3", data={"code": "age", "validation": "decimal", "question": "Alter"})
    add(client, "page_break")                               # 4
    add(client, "info")                                     # 5
    client.post("/surveys/1/editor/items/5", data={"code": "bye", "question": "Fast fertig"})


def uid(app):
    with app.app_context():
        return db.session.get(Survey, 1).code


def consent_form():
    return dict(CONSENT_OK)


def test_closed_until_started(app, client, survey):
    build(client)
    p = app.test_client()
    html = p.get(f"/s/{uid(app)}").data.decode()
    assert "nicht geöffnet" in html
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id))) == 0


def test_full_participation_and_export(app, client, survey):
    build(client)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    url = f"/s/{uid(app)}"
    p = app.test_client()
    assert "Einwilligung" in p.get(url).data.decode() or "Fortsetzen" in p.get(url).data.decode()
    p.post(url, data=consent_form())
    html = p.post(url, data={"nav": "next"}).data.decode()  # likert required
    assert "has-error" in html
    p.post(url, data={"nav": "next", "i2_s1": "2", "i2_s2": "2", "i3": "41,5"})
    html = p.get(url).data.decode()
    assert "Fast fertig" in html and "Absenden" in html
    p.post(url, data={"nav": "next"})
    assert "Vielen Dank" in p.get(url).data.decode()  # revisiting shows the thank-you page

    with app.app_context():
        r = db.session.scalar(db.select(Response))
        assert r.status == "completed" and not r.is_preview and set(r.layout["stmt"]["2"]) == {"s1", "s2"}
        assert db.session.get(Survey, 1).completed_count() == 1
        assert not any(c in Response.__table__.columns for c in ("ip", "user_agent"))

    data = client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig")
    row = list(csv.DictReader(io.StringIO(data)))[0]
    assert row["TA_1"] == "2" and row["TA_2"] == "4"      # reverse coded
    assert row["age"] == "41.5" and row["consent"] == "1" and row["status"] == "completed"
    assert set(row["TA_order"].split("|")) == {"TA_1", "TA_2"}
    semi = client.get("/surveys/1/export/data.csv?sep=semicolon").data.decode("utf-8-sig")
    assert "41,5" in semi and ";" in semi.splitlines()[0]
    book = client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    assert "umgepolt, im Export bereits rekodiert" in book and "TA_order" in book


def test_resume_and_randomization_is_stable(app, client, survey):
    build(client)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    url = f"/s/{uid(app)}"
    p = app.test_client()
    p.get(url)
    p.post(url, data=consent_form())
    first = p.get(url).data.decode()
    again = p.get(url).data.decode()
    order = lambda html: [html.index('name="i2_s1"'), html.index('name="i2_s2"')]
    assert (order(first)[0] < order(first)[1]) == (order(again)[0] < order(again)[1])
    orders = set()
    for _ in range(20):  # different participants get different orders
        q = app.test_client()
        q.get(url)
        q.post(url, data=consent_form())
        h = q.get(url).data.decode()
        orders.add(h.index('name="i2_s1"') < h.index('name="i2_s2"'))
    assert orders == {True, False}


def test_decline_and_sample_limit(app, client, survey):
    build(client)
    client.post("/surveys/1/start", data={"use_max": "1", "max_responses": "1"})
    url = f"/s/{uid(app)}"
    d = app.test_client()
    d.get(url)
    d.post(url, data={"i1": "0"})
    assert "nicht teilnehmen möchten" in d.get(url).data.decode()
    p = app.test_client()
    p.get(url)
    p.post(url, data=consent_form())
    p.post(url, data={"nav": "next", "i2_s1": "1", "i2_s2": "1"})
    p.post(url, data={"nav": "next"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.status == "closed" and s.close_reason == "sample"
    assert "Diese Befragung ist beendet." in app.test_client().get(url).data.decode()   # closed for good, not "currently"
    data = client.get("/surveys/1/export/data.csv?all=1").data.decode("utf-8-sig")
    assert [r["status"] for r in csv.DictReader(io.StringIO(data))] == ["completed"]  # declines are not stored
    with app.app_context():
        assert db.session.get(Survey, 1).declined_count == 1
        assert db.session.scalar(db.select(db.func.count(Response.id)).filter_by(is_preview=False)) == 1
    assert "nicht teilnehmen möchten" in d.get(url).data.decode()  # same browser sees the decline page again


def test_language_selection(app, client, survey):
    build(client)
    client.post("/surveys/1/editor/items/5?lang=en", data={"question": "Almost done"})
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    url = f"/s/{uid(app)}"
    p = app.test_client()
    html = p.get(url, headers={"Accept-Language": "en-US,en;q=0.9"}).data.decode()
    assert 'lang="en"' in html and "By clicking “Next”" in html
    p.post(url, data=consent_form())
    p.post(url, data={"nav": "next", "i2_s1": "1", "i2_s2": "1"})
    assert "Almost done" in p.get(url).data.decode()
    assert "Fast fertig" in p.get(url + "?lang=de").data.decode()


def test_export_requires_membership(app, client, survey):
    other = app.test_client()
    other.get("/auth/dev-login?orcid=0000-0001-5555-5555")
    other.post("/terms/", data={"accept": "1"})
    assert other.get("/surveys/1/export/data.csv").status_code == 404


def test_sources_test_dummy_real(app, client, survey):
    build(client)
    client.post("/surveys/1/meta", data={"title": "Test", "languages": ["de", "en"], "default_language": "de",
                                          "allow_back": "1", "keep_test_runs": "1"})
    # a preview run (test)
    P = "/surveys/1/editor/preview"
    client.post(P, data=CONSENT_OK)
    client.post(P, data={"nav": "next", "i2_s1": "3", "i2_s2": "3"})
    client.post(P, data={"nav": "next"})
    client.get(P + "?restart=1")  # kept, because keep_test_runs is on
    # dummy data through the real flow
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    client.post("/surveys/1/dummy", data={"n": "5"})
    # one real participant
    with app.app_context():
        uid = db.session.get(Survey, 1).code
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=consent_form())
    p.post(f"/s/{uid}", data={"nav": "next", "i2_s1": "1", "i2_s2": "1"})
    p.post(f"/s/{uid}", data={"nav": "next"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.completed_count() == 1  # only real participants count
        dummies = db.session.scalars(db.select(Response).filter_by(source="dummy")).all()
        assert len(dummies) == 5 and all(r.status == "completed" for r in dummies)
        assert all(set(r.answers["2"]) == {"s1", "s2"} for r in dummies)
    # the data pool counts every source, although test runs and dummies are stored with is_preview
    pool = client.get("/surveys/1/results").data.decode().split('id="pool"')[1].split("</form>")[0]   # "Grundlage der Auswertung"
    assert "echte Teilnahmen (1)" in pool and "Testdurchläufe (1)" in pool and "Dummy-Daten (5)" in pool
    real_only = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))
    assert [r["source"] for r in real_only] == ["real"]
    everything = list(csv.DictReader(io.StringIO(client.get(
        "/surveys/1/export/data.csv?source=real&source=test&source=dummy").data.decode("utf-8-sig"))))
    assert sorted(r["source"] for r in everything) == ["dummy"] * 5 + ["real", "test"]
    client.post("/surveys/1/dummy/delete", data={"source": "dummy"})
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id)).filter_by(source="dummy")) == 0


def test_dummies_get_plausible_completion_times(app, client, survey):
    from app import dummy, duration, export

    build(client)
    client.post("/surveys/1/meta", data={"title": "Test", "languages": ["de"], "default_language": "de",
                                          "allow_back": "1", "page_timing": "1"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert dummy.generate(s, 40, seed=3) == 40
        typical = max(duration.estimate(s)["typical"], dummy.MIN_SECONDS)
        times = export.completion_seconds(s, sources=["dummy"])
        assert len(times) == 40 and min(times) >= 5                      # not all 0 s as before
        assert 0.6 * typical <= export.median_seconds(s, sources=["dummy"]) <= 1.6 * typical
        assert len(set(times)) > 10                                       # spread out, not one value
        for r in db.session.scalars(db.select(Response).filter_by(source="dummy")):
            pages = r.paradata["_t"]
            assert pages and abs(sum(pages.values()) - (r.completed_at - r.started_at).total_seconds()) < 1


def test_test_runs_replaced_when_not_kept(app, client, survey):
    build(client)
    P = "/surveys/1/editor/preview"
    client.get(P)
    client.get(P + "?restart=1")
    client.get(P + "?restart=1")
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id)).filter_by(source="test")) == 1


def test_privacy_page_for_participants(app, client, survey):
    build(client)
    fill_consent(client, contact_email="studie@uni.de")
    with app.app_context():  # the deletion code is off by default since the pre-test 2026-10-16
        from app.extensions import db as _db
        from app.models import Survey as _Survey

        _db.session.get(_Survey, 1).erase_code = True
        _db.session.commit()
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    url = f"/s/{uid(app)}"
    p = app.test_client()
    page = p.get(url).data.decode()
    assert 'class="p-brand"><svg class="wordmark " viewBox="0 0 6249 1325" role="img" aria-label="Askurious.de">' in page and f"{url}/privacy" in page
    html = p.get(f"{url}/privacy?back=//evil.example").data.decode()
    assert "Philipp" in html and "studie@uni.de" in html and "Zurück zur Befragung" in html
    with app.app_context():
        assert f"ID der Befragung: <code dir=\"ltr\">{db.session.get(Survey, 1).code}</code>" in html   # to quote in an enquiry
    assert f'href="{url}"' in html and "evil.example" not in html        # only a local way back
    body = html.split('<main')[1].split('</main>')[0]
    # no other links than back, deleting one's answers, the accessibility statement and the complaint to the operators
    # (local), never the ORCID
    assert body.count("<a ") == 6 and 'href="/sources"' in body and f'href="{url}/accessibility?' in body and 'href="/imprint"' in body and f'href="{url}/complaint?' in body and "0000-0003" not in html
    assert f'href="{url}/delete?' in body


def test_opening_the_link_stores_nothing_until_the_first_submit(app, client, survey):
    build(client)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    url = f"/s/{uid(app)}"

    def stored():
        with app.app_context():
            return db.session.scalar(db.select(db.func.count(Response.id)).filter_by(is_preview=False))

    bot = app.test_client()
    for _ in range(3):                                               # crawlers and link previews only open the link
        assert "Einwilligung" in bot.get(url).data.decode()
    assert stored() == 0
    r = app.test_client().post(url, data=consent_form())             # no GET before: back to the start, nothing stored
    assert r.status_code == 302 and r.headers["Location"].endswith(url)
    assert stored() == 0
    p = app.test_client()
    p.get(url)
    with p.session_transaction() as sess:
        pending = dict(sess["n1"])
    p.post(url, data=consent_form())                                 # the first "Next" stores the participation
    assert stored() == 1
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(is_preview=False))
        assert r.pid == pending["pid"] and r.started_at.isoformat().startswith(pending["at"][:19]) and r.position == 1
    d = app.test_client()                                            # declining on the first page: only a count
    d.get(url)
    d.post(url, data={**consent_form(), "i1": "0"})
    with app.app_context():
        assert stored() == 1 and db.session.get(Survey, 1).declined_count == 1


def test_robots_and_nofollow(client):
    txt = client.get("/robots.txt").data.decode()
    assert "Disallow: /s/" in txt and "Disallow: /surveys/" in txt
    assert 'href="https://ask.askurious.de/s/introduction" target="_blank" rel="noopener nofollow"' in client.get("/").data.decode()


def _pos(html):
    import re

    return re.search(r'name="pos" value="([^"]+)"', html).group(1)


def test_page_sent_twice_does_not_skip_the_next_page(app, client, survey):
    """A double click (or an older page from the browser history) arrives when the response is already
    on another page: it must not be checked against that page, which would skip optional questions."""
    fill_consent(client)
    add(client, "text_input")                               # 2
    client.post("/surveys/1/editor/items/2", data={"code": "a", "question": "Erste"})
    add(client, "page_break")                               # 3
    add(client, "text_input")                               # 4
    client.post("/surveys/1/editor/items/4", data={"code": "b", "question": "Zweite"})
    add(client, "page_break")                               # 5
    add(client, "info")                                     # 6
    client.post("/surveys/1/editor/items/6", data={"code": "bye", "question": "Fast fertig"})
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    url = f"/s/{uid(app)}"
    p = app.test_client()
    p.get(url)
    p.post(url, data=consent_form())
    first = p.get(url).data.decode()
    assert "Erste" in first
    sent = {"nav": "next", "pos": _pos(first), "i2": "x"}
    assert p.post(url, data=sent).status_code == 302
    again = p.post(url, data=sent)                           # the same page once more
    assert again.status_code == 302
    second = p.get(url).data.decode()
    assert "Zweite" in second and "Fast fertig" not in second and _pos(second) != _pos(first)
    with app.app_context():
        assert db.session.scalar(db.select(Response)).answers.get("2") == "x"
    # "Back" from a stale page moves nothing either
    p.post(url, data={"nav": "back", "pos": _pos(first)})
    assert "Zweite" in p.get(url).data.decode()
    # the current page works as before, with and without the field (older pages still open at a deploy)
    p.post(url, data={"nav": "next", "pos": _pos(second), "i4": "y"})
    assert "Fast fertig" in p.get(url).data.decode()


def test_english_messages_for_choice_and_likert(app, client, survey):
    from app.items.base import AnswerError
    from app.models import Item

    add(client, "single_choice")                            # 2
    add(client, "multiple_choice")                          # 3
    add(client, "likert")                                   # 4
    with app.app_context():
        for item_id, form in ((2, {}), (3, {}), (4, {})):
            item = db.session.get(Item, item_id)
            item.answer_mode = "required"
            for lang, german in (("de", True), ("en", False)):
                try:
                    item.kind.parse_answer(item, MultiDict(form), lang)
                    raise AssertionError("required item accepted an empty answer")
                except AnswerError as e:
                    assert str(e).startswith("Bitte") is german, (item.type, lang, str(e))
        multi = db.session.get(Item, 3)
        multi.config = {**multi.config, "min": 2, "max": 2, "options": [{"code": str(n), "label": {"de": "x"}} for n in (1, 2, 3)]}
        for values, word in ((["1"], "at least 2"), (["1", "2", "3"], "at most 2")):
            try:
                multi.kind.parse_answer(multi, MultiDict([("i3", v) for v in values]), "en")
                raise AssertionError("limit not checked")
            except AnswerError as e:
                assert word in str(e)


def test_auto_next_page_keeps_buttons_for_no_script_and_return(app, client, survey):
    """Pages that continue by themselves hide the buttons; without JavaScript they must still be there."""
    from app import runtime
    from app.models import Item

    fill_consent(client)
    add(client, "assign")                                   # 2
    with app.app_context():
        item = db.session.get(Item, 2)
        item.config = {**item.config, "auto_next": True}
        db.session.commit()
        assert runtime.auto_next_ok([item])
    html = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    assert "data-auto-next-nav" in html and "<noscript><div class=\"p-nav\">" in html
    assert html.count('class="p-button p-next" type="submit" name="nav" value="next"') == 2  # hidden for the script, plain inside noscript


def test_lost_session_and_unknown_link_get_a_participant_page(app, client, survey):
    build(client)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    url = f"/s/{uid(app)}"
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        p = app.test_client()
        page = p.get(url).data.decode()
        assert 'name="csrf_token"' in page
        lost = app.test_client().post(url, data={"nav": "next"})   # no cookie, no token: blocked cookies
        html = lost.data.decode()
        assert lost.status_code == 400 and "Ihre Sitzung konnte nicht fortgesetzt werden" in html
        assert "p-container" in html and f'href="{url}"' in html and "Bad Request" not in html
        en = app.test_client().post(url, data={"nav": "next"}, headers={"Accept-Language": "en"})
        assert "Your session could not be continued" in en.data.decode()
    finally:
        app.config["WTF_CSRF_ENABLED"] = False
    missing = app.test_client().get("/s/gibtesnicht")
    assert missing.status_code == 404 and "Diese Befragung wurde nicht gefunden" in missing.data.decode()
    # a page sent by a browser that has no participation (any more) opens the survey instead of "not open"
    r = app.test_client().post(url, data={"nav": "next"})
    assert r.status_code == 302 and r.headers["Location"].endswith(url)


def test_back_and_language_switch_keep_a_half_filled_required_question(app, client, survey):
    """"Zurück speichert" also when a required matrix is not complete yet; the language switch on a page
    sends the page along and keeps it too."""
    fill_consent(client)
    add(client, "info")                                     # 2
    add(client, "page_break")                               # 3
    add(client, "likert")                                   # 4
    client.post("/surveys/1/editor/items/4", data=MultiDict([
        ("code", "TA"), ("answer_mode", "required"), ("points", "5"), ("labeling", "full"),
        ("stmt_id", "s1"), ("stmt_code", ""), ("stmt_text", "A"),
        ("stmt_id", "s2"), ("stmt_code", ""), ("stmt_text", "B")]))
    add(client, "text_input")                               # 5
    client.post("/surveys/1/editor/items/5", data={"code": "note", "question": "Notiz"})
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    url = f"/s/{uid(app)}"
    p = app.test_client()
    p.get(url)
    p.post(url, data=consent_form())
    p.post(url, data={"nav": "next"})                        # info page
    page = p.get(url).data.decode()
    assert 'data-set-lang="en"' in page
    p.post(url, data={"nav": "back", "i4_s1": "2", "i5": "halb"})   # one of two required statements
    with app.app_context():
        r = db.session.scalar(db.select(Response))
        assert r.answers["4"] == {"s1": 2} and r.answers["5"] == "halb"
    p.post(url, data={"nav": "next"})
    again = p.get(url).data.decode()
    assert 'name="i4_s1" value="2" checked' in again and "halb" in again
    # the language switch: same page, other language, input kept; "Next" still asks for the rest
    r = p.post(url, data={"set_lang": "en", "i4_s1": "3", "i5": "half"})
    assert r.status_code == 302
    en = p.get(url).data.decode()
    assert 'lang="en"' in en and 'name="i4_s1" value="3" checked' in en and "half" in en
    assert "Please answer all statements (1 still open)." in p.post(url, data={"nav": "next", "i4_s1": "3"}).data.decode()


def test_numbers_with_thousands_separators_follow_the_language():
    from app.items.text import _num

    assert [_num(v, "de") for v in ("1.000", "1.299,5", "1,5", "1.5", "0.125", "12.345.678", " 3 ", "1 000")] == \
           [1000, 1299.5, 1.5, 1.5, 0.125, 12345678, 3, 1000]
    assert [_num(v, "en") for v in ("1,000", "1,299.5", "1.5", "1,5", "1.000", "0,125")] == [1000, 1299.5, 1.5, 1.5, 1.0, 0.125]
    for bad in ("abc", "1,2,3x", "nan", "inf", ""):
        try:
            _num(bad, "de")
            raise AssertionError(bad)
        except ValueError:
            pass


def test_privacy_page_names_stored_identifiers_instead_of_anonymous(app, client, survey):
    build(client)
    plain = client.get(f"/s/{uid(app)}/privacy?lang=de").data.decode()
    assert "Ihre Antworten werden ohne Namen und ohne IP-Adresse gespeichert" in plain
    client.post("/surveys/1/panel", data=MultiDict([("param_name", "pid"), ("param_mode", "store"), ("param_required", "0"),
                                                     ("param_allowed", "")]))
    with app.app_context():
        from app import panel

        assert panel.stored_names(db.session.get(Survey, 1)) == ["pid"]
    de = client.get(f"/s/{uid(app)}/privacy?lang=de").data.decode()
    assert "ohne Namen und ohne IP-Adresse" not in de and "zusammen mit einer Kennung aus dem Aufruf-Link (pseudonym" in de
    en = client.get(f"/s/{uid(app)}/privacy?lang=en").data.decode()
    assert "without your name and without your IP address" not in en and "pseudonymous, see below" in en


def test_error_message_belongs_to_its_question_and_the_title_says_so(app, client, survey):
    """UI audit T7, T18, T19: the message carries an id the inputs point to, the consent shows it next to its
    ticks, the title announces the failed page; stars stand in their natural order; an untouched slider says
    what to do."""
    build(client)
    add(client, "rating")                                   # 6
    client.post("/surveys/1/editor/items/6", data={"code": "st", "question": "Sterne", "style": "stars", "points": "5"})
    add(client, "rating")                                   # 7
    client.post("/surveys/1/editor/items/7", data={"code": "sl", "question": "Regler", "style": "slider", "min": "0", "max": "100", "step": "1"})
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    url = f"/s/{uid(app)}"
    p = app.test_client()
    first = p.get(url).data.decode()
    assert "<title>Seite 1 – Test</title>" in first
    failed = p.post(url, data={"i1": "1"}).data.decode()     # agreed without the ticks
    assert "<title>Fehler – Seite 1 – Test</title>" in failed
    ticks, message, buttons = failed.index("p-confirms"), failed.index('id="msg1" data-item-message'), failed.index("p-consent-actions")
    assert ticks < message < buttons and failed.count("data-item-message") == 1
    p.post(url, data=consent_form())
    failed = p.post(url, data={"nav": "next"}).data.decode()  # required Likert left open
    assert '<p class="p-error" role="alert" id="msg2" data-item-message>' in failed
    p.post(url, data={"nav": "next", "i2_s1": "1", "i2_s2": "1"})
    page = p.get(url).data.decode()
    stars = page.split("data-stars")[1].split("</div>")[0]
    assert [stars.index(f'value="{n}"') for n in range(1, 6)] == sorted(stars.index(f'value="{n}"') for n in range(1, 6))
    assert 'aria-valuetext="noch nicht eingestellt"' in page and "Wählen Sie einen Wert auf der Linie" in page  # for every device (UI/UX audit 2026-10-16, 95)
    assert "data-slider-hint >" in page                       # shown while the slider is untouched


def test_declining_by_mistake_can_be_taken_back_and_closed_surveys_say_so(app, client, survey):
    """UI audit T8 and T30."""
    build(client)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    url = f"/s/{uid(app)}"
    p = app.test_client()
    p.get(url)
    declined = p.post(url, data={"i1": "0"}).data.decode()
    assert "nicht teilnehmen möchten" in declined and f'action="{url}/after-all"' in declined and "Doch teilnehmen" in declined
    assert "Doch teilnehmen" in p.get(url).data.decode()           # still offered when the link is opened again
    r = p.post(f"{url}/after-all")
    assert r.status_code == 302 and r.headers["Location"].endswith(url)
    assert "Fortsetzen" in p.get(url).data.decode()                # the consent again, a fresh participation
    p.post(url, data=consent_form())
    with app.app_context():
        assert db.session.get(Survey, 1).declined_count == 0       # taken back: no decline (audit 2026-10-10, T-3)
        assert db.session.scalar(db.select(db.func.count(Response.id)).filter_by(is_preview=False)) == 1
    # paused: "currently not open"; closed: "has ended"; an own text wins in both cases; no way back once closed
    d = app.test_client()
    d.get(url)
    d.post(url, data={"i1": "0"})
    client.post("/surveys/1/close", data={"pause": "1"})
    assert "derzeit nicht geöffnet" in app.test_client().get(url).data.decode()
    assert "Doch teilnehmen" not in d.get(url).data.decode()
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    client.post("/surveys/1/close")
    assert "Diese Befragung ist beendet." in app.test_client().get(url).data.decode()
    assert "This survey has ended." in app.test_client().get(url + "?lang=en").data.decode()
    client.post("/surveys/1/end-texts", data={"closed_de": "Eigener Text"})
    assert "Eigener Text" in app.test_client().get(url).data.decode()


def test_numbers_and_hints_follow_the_page_language(app, client, survey):
    """UI audit T23, T24, T27."""
    from app.items.text import _fmt
    from app.models import Item

    assert [_fmt(0.5, "de"), _fmt(0.5, "en"), _fmt(2.0, "de"), _fmt(10, "en")] == ["0,5", "0.5", "2", "10"]
    add(client, "text_input")                               # 2
    client.post("/surveys/1/editor/items/2", data={"code": "x", "question": "Zahl", "validation": "decimal", "min": "0,5", "max": "2,5"})
    add(client, "multiple_choice")                          # 3
    with app.app_context():
        number = db.session.get(Item, 2)
        assert "from 0.5 to 2.5" in number.kind.range_hint(number, "en") and "von 0,5 bis 2,5" in number.kind.range_hint(number, "de")
        assert [number.kind.shown(number, v, lang) for v, lang in ((1.5, "de"), (1.5, "en"), (2.0, "de"), (None, "de"), ("1,5x", "de"))] \
            == ["1,5", "1.5", "2", "", "1,5x"]
        multi = db.session.get(Item, 3)
        for lo, hi, de, en in ((2, 4, "Mindestens 2 und höchstens 4 Antworten.", "At least 2 and up to 4 answers."),
                               (2, None, "Mindestens 2 Antworten.", "At least 2 answers."),
                               (None, 3, "Höchstens 3 Antworten.", "Up to 3 answers."), (2, 2, "Genau 2 Antworten.", "Exactly 2 answers.")):
            multi.config = {**multi.config, "min": lo, "max": hi}
            db.session.commit()
            assert de in client.get("/surveys/1/editor/items/3/frame?lang=de").data.decode()
            assert en in client.get("/surveys/1/editor/items/3/frame?lang=en").data.decode()


def test_several_participations_on_one_device_only_where_allowed(app, client, survey):
    """UI audit T9: with the setting, the end page lets the next person start in the same browser."""
    fill_consent(client)
    add(client, "info")                                     # 2
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    url = f"/s/{uid(app)}"
    p = app.test_client()

    def run():
        p.get(url)
        p.post(url, data=consent_form())
        p.post(url, data={"nav": "next"})
        return p.get(url).data.decode()

    done = run()
    assert "Vielen Dank" in done and "Neue Teilnahme an diesem Gerät" not in done
    p.post(f"{url}/after-all")                                    # without the setting the browser keeps its participation
    assert "Vielen Dank" in p.get(url).data.decode()
    settings = client.get("/surveys/1").data.decode()
    assert 'name="repeat_mode" value="multi" >' in settings and "beliebig oft (Laborrechner" in settings
    client.post("/surveys/1/panel", data={})                 # the entry settings keep it as it is
    with app.app_context():
        from app import panel

        assert not panel.multi(db.session.get(Survey, 1))
    client.post("/surveys/1/meta", data={"title": "Test", "languages": ["de", "en"], "multi_shown": "1", "multi": "1"})
    with app.app_context():
        from app import panel

        assert panel.multi(db.session.get(Survey, 1))
    done = p.get(url).data.decode()
    assert "Neue Teilnahme an diesem Gerät" in done and f'action="{url}/after-all"' in done
    assert p.post(f"{url}/after-all").status_code == 302
    assert "Fortsetzen" in p.get(url).data.decode()          # the consent for the next person
    # a participation that is still running is not dropped by that address
    p.post(url, data=consent_form())
    p.post(f"{url}/after-all")
    assert "Absenden" in p.get(url).data.decode() and "Fortsetzen" not in p.get(url).data.decode()
    p.post(url, data={"nav": "next"})
    with app.app_context():
        rows = db.session.scalars(db.select(Response).filter_by(is_preview=False)).all()
        assert [r.status for r in rows] == ["completed", "completed"]


def test_constant_sum_says_what_is_left_and_typed_numbers_stay(app, client, survey):
    """UI audit T11 (the behaviour itself is in participant.js): the page carries the texts for "left" and
    "too many", and the server still refuses a total that is not right."""
    add(client, "constant_sum")                             # 2
    html = client.get("/surveys/1/editor/items/2/frame?lang=de").data.decode()
    assert 'data-csum-rest data-left="noch %s zu verteilen" data-over="%s zu viel" hidden' in html
    assert "eingetippte Zahlen bleiben, wie Sie sie eingeben" in html
    script = open("app/static/participant.js", encoding="utf-8").read()
    assert "function typed(i, target)" in script and 'x.addEventListener("change", function () { typed(i,' in script


def test_star_progress_and_page_heading_speak_to_screen_readers(app, client, survey):
    """UI audit T26, T28, T29, T33, T36: the star of a required question carries its word and is explained on
    the page; the progress bar says the page in words; every page has a heading and its own title."""
    build(client)
    client.post("/surveys/1/editor/items/3", data={"code": "age", "validation": "decimal", "question": "Alter", "answer_mode": "required"})
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    url = f"/s/{uid(app)}"
    p = app.test_client()
    first = p.get(url).data.decode()
    assert '<h1 class="p-sr-only">Test, Seite 1</h1>' in first
    assert 'aria-valuetext="Seite 1 von etwa' in first
    assert "p-required-note" not in first                    # the consent shows no star
    assert '<section class="p-consent" aria-labelledby="q1">' in first
    p.post(url, data=consent_form())
    page = p.get(url).data.decode()
    assert "<title>Seite 2 – Test</title>" in page and '<h1 class="p-sr-only">Test, Seite 2</h1>' in page
    star = '<span class="p-required"><span aria-hidden="true"> *</span><span class="p-sr-only"> (Pflichtfrage)</span></span>'
    assert star in page
    assert page.count('<p class="p-required-note"><span class="p-required" aria-hidden="true">*</span> Pflichtfrage</p>') == 1
    p.post(url, data={"nav": "next", "i2_s1": "1", "i2_s2": "1", "i3": "30"})
    page = p.get(url).data.decode()                          # the info page
    assert "<title>Seite 3 – Test</title>" in page and "p-required-note" not in page


def test_touch_targets_and_notes_without_javascript(app, client, survey):
    """UI audit T33, T36: ranking and image mark say what they need without JavaScript, the ranking hint for
    touch names only the arrows, a group without a question points to nothing, small buttons are 44 px."""
    add(client, "ranking")                                  # 2
    add(client, "image_mark")                               # 3
    add(client, "single_choice")                            # 4
    client.post("/surveys/1/editor/items/4", data={"code": "sc", "question": ""})
    frame = lambda n: client.get(f"/surveys/1/editor/items/{n}/frame?lang=de").data.decode()
    needs = "<noscript><p class=\"p-error\">Für diese Frage wird JavaScript benötigt.</p></noscript>"
    ranking = frame(2)
    assert needs in ranking and needs in frame(3)
    assert '<span class="p-if-touch">Bringen Sie die Elemente mit den Pfeilen' in ranking and '<span class="p-if-mouse">Ziehen Sie' in ranking
    assert '<fieldset class="p-fieldset" >' in frame(4)      # no question: nothing to point to
    css = open("app/static/participant.css", encoding="utf-8").read()
    assert ".p-rank-btns button { width: 44px; height: 44px;" in css
    assert "@media (hover: none) { .p-if-mouse { display: none; } .p-if-touch { display: inline; } }" in css
    marker = open("app/static/map_marker.js", encoding="utf-8").read()
    assert '"aria-labelledby": name + "_q"' in marker and '" (Pflichtfrage)"' in marker
