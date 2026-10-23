from datetime import timedelta
from types import SimpleNamespace

import pytest

from app import ai
from app.extensions import db
from app.models import AiCall, utcnow


class Fake:
    """Answers like the OpenAI SDK, with the usage it reports."""

    class responses:
        @staticmethod
        def create(**kw):
            if kw["input"] == "fail":
                raise RuntimeError("quota")
            return SimpleNamespace(output_text="Text", usage=SimpleNamespace(input_tokens=1200, output_tokens=300))

    class images:
        @staticmethod
        def generate(**kw):
            return SimpleNamespace(data=[SimpleNamespace(b64_json="aGk=")], usage=None)


def _call(app, fn):
    with app.test_request_context():
        from flask_login import login_user

        from app.models import User

        login_user(db.session.scalar(db.select(User).filter_by(is_admin=True)))
        return fn()


def test_calls_are_logged_without_person_and_kept_two_years(app, client, monkeypatch):
    monkeypatch.setattr(ai, "_client", lambda: Fake)
    with app.app_context():
        db.session.add(AiCall(at=utcnow() - timedelta(days=731), kind="text", purpose="consent", model="old"))
        db.session.commit()
    _call(app, lambda: ai.generate_text("i", "p", purpose="construct_ideas"))
    _call(app, lambda: ai.generate_image("p", "1536x1024", purpose="image_set", item_type="conjoint"))
    with pytest.raises(ai.AIError):
        _call(app, lambda: ai.generate_text("i", "fail", purpose="consent", item_type="consent"))
    with app.app_context():
        rows = list(db.session.scalars(db.select(AiCall).order_by(AiCall.id)))
        assert [r.model for r in rows].count("old") == 0                                # older than two years: deleted
        text, image, failed = rows
        assert (text.purpose, text.input_tokens, text.output_tokens, text.ok) == ("construct_ideas", 1200, 300, True)
        assert (image.kind, image.images, image.size, image.item_type) == ("image", 1, "1536x1024", "conjoint")
        assert (failed.ok, failed.purpose, failed.input_tokens) == (False, "consent", None)
        # the person who clicked, for their page "KI-Nutzung" (2026-10-10, E6); still no survey, no content
        assert not hasattr(AiCall, "survey_id") and not hasattr(AiCall, "prompt")


def test_admin_page_has_the_periods_of_befragungen(app, client, survey):
    """The page KI switches like Befragungen: 24 Stunden, 7 Tage, 30 Tage, 12 Monate and "seit" a date; "24 Stunden"
    instead of "Heute" on both (Ralf, 2026-10-19). Old links (day, week, month) still open their period."""
    from datetime import date

    from app import admin_stats, ai_stats

    with app.app_context():
        now = utcnow()
        for days in (0.5, 3, 20, 200, 500):
            db.session.add(AiCall(at=now - timedelta(days=days), kind="text", purpose="consent", model="gpt-x"))
        db.session.commit()
        assert ai_stats.report("24h", now=now)["total"]["calls"] == 1
        assert ai_stats.report("30", now=now)["total"]["calls"] == 3
        year = ai_stats.report("365", now=now)
        assert year["total"]["calls"] == 4 and year["prev"]["calls"] == 1 and year["step"] == "Woche"
        old = ai_stats.report("since", date(2000, 1, 1), now=now)                   # capped by nothing: every call
        assert old["total"]["calls"] == 5 and not old["compare"] and old["since"] == date(2000, 1, 1)
        assert ai_stats.report("since", None, now=now)["period"] == ai_stats.DEFAULT  # no date: the standard
        assert ai_stats.report("month", now=now)["period"] == "30" and ai_stats.report("bogus", now=now)["period"] == "7"
    page = client.get("/admin/ai").data.decode()
    survey_page = client.get("/admin/surveys").data.decode()
    for label in admin_stats.period_labels().values():
        assert f">{label}</a>" in page and f">{label}</a>" in survey_page
    assert "Heute</a>" not in page + survey_page and 'aria-current="page">7 Tage</a>' in page
    page = client.get("/admin/ai?period=since&since=2026-01-01").data.decode()
    assert 'value="2026-01-01"' in page and "davor:" not in page
    saved = client.post("/admin/ai?period=since&since=2026-01-01", data={"text_model": [""], "text_in": [""], "text_out": [""]})
    assert "period=since" in saved.location and "since=2026-01-01" in saved.location   # saving keeps the period


def test_admin_page_periods_tables_and_prices(app, client, survey, monkeypatch):
    with app.app_context():
        now = utcnow()
        for at, purpose, kind, item in ((now - timedelta(hours=2), "scale_items", "text", "likert"),
                                        (now - timedelta(days=3), "construct_ideas", "text", None),
                                        (now - timedelta(days=20), "image", "image", "image")):
            db.session.add(AiCall(at=at, kind=kind, purpose=purpose, item_type=item, model="gpt-x" if kind == "text" else "img-x",
                                  input_tokens=1_000_000 if kind == "text" else None, output_tokens=500_000 if kind == "text" else None,
                                  images=1 if kind == "image" else 0, size="1536x1024" if kind == "image" else None))
        db.session.commit()
    day = client.get("/admin/ai?period=day").data.decode()
    assert "Skalen-Items formulieren" in day and "Konstrukt-Ideen" not in day and "keine Kostenangabe" in day
    week = client.get("/admin/ai?period=week").data.decode()
    assert "Konstrukt-Ideen (ganzer Fragebogen)" in week and "Bild (einzeln)" not in week
    month = client.get("/admin/ai").data.decode()                                       # default: week
    assert "Bild (einzeln)" not in month
    month = client.get("/admin/ai?period=month").data.decode()
    assert "Bild (einzeln)" in month and "likert" in month and 'class="aic-chart"' in month
    client.post("/admin/ai?period=month", data={"text_model": ["gpt-x", ""], "text_in": ["2,00", ""], "text_out": ["8", ""],
                                                 "image_model": [""], "image_size": [""], "image_price": [""]})
    month = client.get("/admin/ai?period=month").data.decode()
    assert "12,00 €" in month and "+ Aufrufe ohne Kostenangabe" in month               # the image has no price yet
    client.post("/admin/ai?period=month", data={"text_model": ["gpt-x", ""], "text_in": ["2,00", ""], "text_out": ["8", ""],
                                                 "image_model": ["img-x", ""], "image_size": ["1536x1024", ""], "image_price": ["0,25", ""]})
    month = client.get("/admin/ai?period=month").data.decode()
    assert "12,25 €" in month                                                            # 2 × (2 + 4) € + 0.25 €
    assert app.test_client().get("/admin/ai").status_code == 302                         # not signed in: to the login
    client.post("/surveys/1/members", data={"orcid": "0000-0001-5555-5555", "name": "Kollegin", "role": "editor"})
    other = app.test_client()                                                            # signed in, not an admin
    other.get("/auth/dev-login?orcid=0000-0001-5555-5555&name=Kollegin")
    other.post("/terms/", data={"accept": "1"})
    assert other.get("/admin/ai").status_code == 403


def test_researchers_overview(app, client, survey, monkeypatch):
    """The list of researchers (no institutions from ORCID any more, Ralf 2026-10-16: organisations replace them)."""
    import re

    from app.models import Response, Survey

    with app.app_context():
        s = db.session.get(Survey, 1)
        for status, source, preview in (("completed", "real", False), ("completed", "real", False), ("active", "real", False),
                                         ("completed", "test", True), ("completed", "dummy", True)):
            s.responses.append(Response(status=status, source=source, is_preview=preview, answers={}))
        db.session.commit()
    html = client.get("/admin/people").data.decode()
    assert "Forschende" in html and "KI-Nutzung" in html and 'href="https://orcid.org/0000-0000-0000-001X"' in html
    text = re.sub(r"<[^>]+>", " ", html)
    assert "0000-0000-0000-001X" not in text                                   # ORCID iDs only as a link, never as text
    row = html.split('class="list-table is-num admin-people admin-people-all"')[1].split("</tr>")[1]
    # after number and groups (the organisation shows its name since #1298, no longer "–"): 1 survey, not running,
    # own, 2 real completed
    assert re.findall(r"<td>(\d+|–)</td>", row)[2:6] == ["1", "–", "1", "2"]
    assert "/admin/affiliations" not in html and "von ORCID holen" not in html     # no institutions from ORCID
    assert 'href="/admin/surveys"' in client.get("/dashboard/", follow_redirects=True).data.decode()   # chart icon in the header


def test_admin_ranking_of_surveys_by_participations(app, client, survey):
    import re

    from app.models import Response, Survey

    client.post("/surveys/new", data={"title": "Zweite", "languages": ["de"], "default_language": "de"})
    with app.app_context():
        first, second = db.session.get(Survey, 1), db.session.get(Survey, 2)
        for s, n in ((first, 1), (second, 3)):
            for _ in range(n):
                s.responses.append(Response(status="completed", source="real", is_preview=False, answers={}, completed_at=utcnow()))
        second.responses.append(Response(status="completed", source="dummy", is_preview=True, answers={}))   # not counted
        db.session.commit()
        codes = (first.code, second.code)
    html = client.get("/admin/surveys").data.decode()
    table = html.split('class="list-table is-num admin-ranking"')[1].split("</table>")[0]
    assert table.index(f"<code>{codes[1]}</code>") < table.index(f"<code>{codes[0]}</code>")   # most participations first
    assert re.search(r"Zweite.*?<strong>3</strong>", table, re.S)
    assert "admin-ranking" not in client.get("/dashboard/", follow_redirects=True).data.decode()   # only on the admin page


def test_models_appear_by_themselves(app, client):
    app.config.update(OPENAI_TEXT_MODEL="text-neu", OPENAI_IMAGE_MODEL="bild-neu", AI_PRICES_FILE=str(app.instance_path) + "/prices-test.json")
    with app.app_context():
        db.session.add(AiCall(at=utcnow() - timedelta(days=200), kind="text", purpose="consent", model="text-alt"))
        db.session.commit()
    html = client.get("/admin/ai").data.decode()
    assert "<code>text-neu</code>" in html and "<code>text-alt</code>" in html and "<code>bild-neu</code>" in html
    assert 'placeholder="neues Modell"' not in html and "Preise unten eintragen" not in html   # nothing typed but prices
    client.post("/admin/ai", data={"text_model": ["text-alt", "text-neu"], "text_in": ["1,5", ""], "text_out": ["6", ""],
                                   "image_model": "bild-neu", "image_size": "1536x1024", "image_price": "0,04"})
    from app import ai_stats

    with app.app_context():
        table = ai_stats.prices()
        assert table["text"] == {"text-alt": {"in": 1.5, "out": 6.0}} and table["image"] == {"bild-neu": {"1536x1024": 0.04}}


def test_every_purpose_in_the_code_has_a_label(app, client):
    """Code review 2026-10-15, K5: the purposes of the interview guide and the live poll assistants were missing, so
    the chart left them out and the tables showed the bare key."""
    import pathlib
    import re

    from app import ai_stats

    root = pathlib.Path(ai_stats.__file__).parent
    used = set()
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        used |= set(re.findall(r'purpose="([a-z_]+)"', text)) - {"setup"}            # "setup": a password token
        used |= set(re.findall(r'_call\(survey,.*?"(interview_[a-z]+)"\)', text, re.S))
    used |= {"interview_short", "interview_post"}                                       # f"interview_{which}"
    assert used - set(ai_stats.PURPOSES) == set()
    assert set(ai_stats.PURPOSES) == set(ai_stats.PURPOSES_EN)
    with app.app_context():
        db.session.add(AiCall(at=utcnow() - timedelta(hours=1), kind="text", purpose="interview_review", model="gpt-x"))
        db.session.add(AiCall(at=utcnow() - timedelta(hours=1), kind="text", purpose="unlisted_x", model="gpt-x"))
        db.session.commit()
    day = client.get("/admin/ai?period=day").data.decode()
    assert day.count("Interviewleitfaden: ganzen Leitfaden prüfen") >= 2 and "unlisted_x</span>" in day  # table, chart
