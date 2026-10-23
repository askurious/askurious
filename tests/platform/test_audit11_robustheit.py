"""Audit 2026-10-10, package U6 "Robustheit": italic in linear time and a limit for every text of an element (T1),
a planned start from a participant's link only for its survey and throttled (T3), mail series that survive a mail
server being down (C1), huge numbers in addresses and forms (C2), "select, then insert" with a savepoint (C4), an IAT
without terms (C5)."""
import random
import re
import smtplib
import time
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, text

from app import lifecycle, mailqueue, participate
from app.extensions import db, get_or_create
from app.items.base import TEXT_MAX, _italic, inline_rich, plain, rich, too_long_text
from app.models import EditMark, Item, MailOutbox, Survey, SurveyPin, User, utcnow
from tests.conftest import add, needs_sqlite

# --- T1: italic ------------------------------------------------------------------------------------------------------

OLD_ITALIC = re.compile(r"(?<![*\w])\*(?![\s*])(.+?)(?<![\s*])\*(?![*\w])")


def test_italic_gives_what_the_regex_gave():
    """On random short texts (where the old regex is quick) the one pass gives the same, with and without marks."""
    rnd = random.Random(20261010)
    for _ in range(40_000):
        s = "".join(rnd.choice("*a _\n.ä1~^`") for _ in range(rnd.randint(0, 16)))
        assert _italic(s) == OLD_ITALIC.sub(r"<em>\1</em>", s), repr(s)
        assert _italic(s, "", "") == OLD_ITALIC.sub(r"\1", s), repr(s)


def test_italic_on_normal_texts():
    assert str(inline_rich("Das ist *wichtig* und **fett**.")) == "Das ist <em>wichtig</em> und <strong>fett</strong>."
    assert plain("*a* b *c d*") == "a b c d"
    assert "<em>" not in str(inline_rich("5 * 3 * 2 und a*b*c"))
    assert str(rich("*eins*\n*zwei")) == "<p><em>eins</em><br>*zwei</p>"


ADVERSARIAL = {"italic": " *a" * 20_000, "bold": "**a" + " *" * 30_000, "sub": " ~a" * 20_000, "sup": " ^a" * 20_000,
               "code": "`" + "a" * 60_000, "tag": "<su" * 20_000, "placeholder": "{{a|" * 15_000,
               "lines": "*a\n" * 20_000, "stars": "*" * 60_000, "star_a": "*a" * 30_000,
               "mixed": "*a **b ~c ^d `e <b>" * 3_000}


@pytest.mark.parametrize("name", sorted(ADVERSARIAL))
def test_formatting_is_quick_on_60_kb(name):
    adversarial = ADVERSARIAL[name]
    # CPU time, not the wall clock. The linear work takes under 0.1 s here and about 0.3 s on GitHub's runners (CI,
    # 2026-10-19); a backtracking pattern on 60 kB takes seconds to minutes, so 0.5 s still tells the two apart.
    for show in (plain, rich, inline_rich):
        took = min(_timed(show, adversarial) for _ in range(2))
        assert took < 0.5, (show.__name__, name, took)


def _timed(show, value):
    started = time.process_time()
    show(value)
    return time.process_time() - started


def test_too_long_text_keeps_what_was_there():
    long = "x" * (TEXT_MAX + 1)
    assert too_long_text({"de": {"question": "kurz", "options": {"a": long}}}) == TEXT_MAX + 1
    assert too_long_text({"de": {"labels": ["a", long + "y"]}}) == TEXT_MAX + 2
    assert too_long_text({"de": {"question": "x" * TEXT_MAX}}) is None
    # saved before the limit: still editable as long as that text stays as it is
    assert too_long_text({"de": {"question": long, "help": "neu"}}, {"de": {"question": long}}) is None


def test_every_text_of_an_element_has_the_limit_in_the_editor(app, client, survey):
    add(client, "single_choice")
    url = "/surveys/1/editor/items/2"
    form = {"code": "q", "question": "Frage", "option_id": ["", ""], "option_code": ["1", "2"],
            "option_label": ["Ja", "x" * (TEXT_MAX + 1)]}
    r = client.post(url, data={**form, "autosave": "1"})
    assert r.json["status"] == "invalid" and "zu lang" in r.json["message"]
    page = client.post(url, data=form, follow_redirects=True).data.decode()
    assert "zu lang" in page
    with app.app_context():
        assert "x" * 100 not in str(db.session.get(Item, 2).texts)
    form["option_label"] = ["Ja", "Nein"]
    assert client.post(url, data={**form, "autosave": "1"}).json["status"] == "saved"


def test_a_file_with_a_text_beyond_the_limit_is_refused(app, client, survey):
    import json

    from app import transfer

    add(client, "info")
    with app.test_request_context():
        data = json.loads(transfer.dumps(db.session.get(Survey, 1), with_files=False))
        info = next(i for b in data["blocks"] for i in b["items"] if i["type"] == "info")
        info.setdefault("texts", {}).setdefault("de", {})["question"] = " *a" * 20_000
        with pytest.raises(transfer.Refused, match="höchstens"):
            transfer.create(transfer.loads(json.dumps(data).encode()), db.session.get(User, 1))
        info["texts"]["de"]["question"] = " *a" * 1000
        new, _ = transfer.create(transfer.loads(json.dumps(data).encode()), db.session.get(User, 1))
        assert new.id != 1


# --- T3: a planned start from a participant's link ------------------------------------------------------------------


def test_the_link_starts_only_its_survey_and_at_most_once_a_minute(app, client, survey, monkeypatch):
    calls = []
    monkeypatch.setattr(lifecycle, "start_due_surveys", lambda now=None, survey_id=None: calls.append(survey_id) or 0)
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        participate._start_if_due(s)
        participate._start_if_due(s)
        assert calls == [1]  # the second request within the minute does not try again
        participate._start_tried[1] -= participate.START_TRY_SECONDS
        participate._start_if_due(s)
        assert calls == [1, 1]


def test_a_start_failing_with_an_error_is_given_up_after_some_rounds(app, client, survey, monkeypatch):
    from tests.participation.test_participate import build
    from tests.analysis.test_start_plan import _due_now, _plan

    build(client)
    _plan(client)
    _due_now(app)

    def broken(*args, **kwargs):
        raise KeyError("a bug")

    monkeypatch.setattr(lifecycle, "start_committed", broken)
    for n in range(1, lifecycle.START_TRIES):
        with app.test_request_context():
            assert lifecycle.start_due_surveys() == 0
            s = db.session.get(Survey, 1)
            db.session.refresh(s)
            assert s.start_at is not None and s.start_plan["tries"] == n  # tried again in the next round
    with app.test_request_context():
        assert lifecycle.start_due_surveys() == 0
        s = db.session.get(Survey, 1)
        db.session.refresh(s)
        assert s.start_at is None and "interner Fehler" in s.start_plan["failed"] and s.status == "draft"
        assert lifecycle.start_due_surveys() == 0  # not again


def test_only_the_named_survey_is_started(app, client, survey):
    with app.test_request_context():
        db.session.get(Survey, 1).start_at = utcnow() - timedelta(minutes=1)
        db.session.commit()
        assert lifecycle.start_due_surveys(survey_id=2) == 0
        assert db.session.get(Survey, 1).start_at is not None  # not claimed


# --- C1: mail server down ---------------------------------------------------------------------------------------------


class _Server:
    """A fake SMTP server: down (no connection) while `down` > 0, one pass at a time; refuses the addresses in
    `refused`."""

    def __init__(self):
        self.down, self.refused, self.sent = 0, set(), []

    def smtp(self, *args, **kwargs):
        server = self

        class SMTP:
            def __init__(self, *a, **k):
                if server.down:
                    raise ConnectionRefusedError("down")

            def send_message(self, msg, to_addrs=None):
                if to_addrs[0] in server.refused:
                    raise smtplib.SMTPRecipientsRefused({to_addrs[0]: (550, b"no such user")})
                server.sent.append(to_addrs[0])

            def quit(self):
                pass

        return SMTP(*args, **kwargs)


def _waiting():
    return db.session.scalar(db.select(db.func.count(MailOutbox.id)).where(MailOutbox.sent_at.is_(None)))


def test_a_mail_server_down_for_ten_passes_loses_no_mail(app, monkeypatch):
    server = _Server()
    monkeypatch.setattr(smtplib, "SMTP", server.smtp)
    app.config.update(MAIL_HOST="smtp.example.org", MAIL_FROM="noreply@example.org", MAIL_SECURITY="none")
    with app.app_context():
        for n in range(20):
            mailqueue.enqueue(f"p{n}@example.org", "Einladung", "Text", kind="links")
        db.session.commit()
        server.down = 1
        for _ in range(10):  # ten passes of _loop, one a minute
            assert mailqueue.send_some(60) == 0
            assert _waiting() == 20
        assert set(db.session.scalars(db.select(MailOutbox.attempts))) == {0}
        assert set(db.session.scalars(db.select(MailOutbox.claimed_at))) == {None}
        server.down = 0
        assert mailqueue.send_some(60) == 20 and len(server.sent) == 20 and _waiting() == 0


def test_a_refused_address_counts_and_waits_longer_each_time(app, monkeypatch):
    server = _Server()
    server.refused.add("gone@example.org")
    monkeypatch.setattr(smtplib, "SMTP", server.smtp)
    app.config.update(MAIL_HOST="smtp.example.org", MAIL_FROM="noreply@example.org", MAIL_SECURITY="none")
    with app.app_context():
        mailqueue.enqueue("gone@example.org", "Einladung", "Text", kind="links")
        mailqueue.enqueue("ok@example.org", "Einladung", "Text", kind="links")
        db.session.commit()
        assert mailqueue.send_some(60) == 1  # the pass goes on after a refused address
        row = db.session.scalar(db.select(MailOutbox).where(MailOutbox.sent_at.is_(None)))
        assert row.attempts == 1
        assert mailqueue.send_some(60) == 0 and db.session.get(MailOutbox, row.id).attempts == 1  # waits first
        for attempts in range(1, mailqueue.MAX_ATTEMPTS):
            row = db.session.get(MailOutbox, row.id)
            assert row.attempts == attempts
            # the wait grows: 2, 4, 8, 16 minutes
            ready = row.claimed_at + timedelta(minutes=mailqueue.CLAIM_MINUTES)
            assert abs((ready - utcnow()) - mailqueue.retry_wait(attempts)) < timedelta(seconds=30)
            row.claimed_at -= mailqueue.retry_wait(attempts)  # the wait is over
            db.session.commit()
            mailqueue.send_some(60)
        assert _waiting() == 0  # given up after MAX_ATTEMPTS refusals


def test_what_counts_as_a_refusal_of_the_mail():
    assert mailqueue._refused(smtplib.SMTPRecipientsRefused({"a@b": (550, b"no")}))
    assert mailqueue._refused(smtplib.SMTPDataError(554, b"rejected"))
    assert not mailqueue._refused(smtplib.SMTPDataError(451, b"try later"))
    assert not mailqueue._refused(smtplib.SMTPSenderRefused(550, b"no", "noreply@example.org"))
    assert not mailqueue._refused(smtplib.SMTPServerDisconnected("gone"))
    assert not mailqueue._refused(smtplib.SMTPAuthenticationError(535, b"auth"))
    assert not mailqueue._refused(ConnectionRefusedError())
    assert not mailqueue._refused(TimeoutError())


# --- C2: huge numbers -------------------------------------------------------------------------------------------------


def test_huge_numbers_in_addresses_give_no_500(app, client, survey):
    from werkzeug.routing import IntegerConverter

    big = "9" * 25
    checked = 0
    for rule in app.url_map.iter_rules():
        ints = [a for a in rule.arguments if isinstance(rule._converters.get(a), IntegerConverter)]
        if not ints or "GET" not in rule.methods:
            continue
        url = rule.rule
        for a in rule.arguments:
            url = re.sub(r"<(?:[^:<>]+:)?%s>" % a, big if a in ints else ("1" if a == "survey_id" else "x"), url)
        assert client.get(url).status_code < 500, url
        checked += 1
    assert checked > 20
    assert client.get(f"/surveys/1/editor/items/{2**31}/frame").status_code == 404


def test_huge_numbers_in_forms_give_no_500(app, client, survey):
    from app.ids import db_id

    big = "9" * 25
    assert client.post("/surveys/1/access-groups", data={"group": big, "role": "viewer"}).status_code < 500
    assert client.post("/surveys/1/design/apply", data={"design": big}).status_code < 500
    assert client.post("/admin/templates/org", data={"org": big, "action": "allow"}).status_code < 500
    with pytest.raises(ValueError):
        db_id(big)
    with pytest.raises(ValueError):
        db_id("-1")
    assert db_id(" 7") == 7


def test_an_overflow_elsewhere_is_answered_with_400(app):
    @app.get("/test-overflow")
    def overflow():
        raise OverflowError("Python int too large to convert to SQLite INTEGER")

    assert app.test_client().get("/test-overflow").status_code == 400


# --- C4: two requests at the same moment ------------------------------------------------------------------------------


@needs_sqlite
def test_get_or_create_takes_the_row_another_request_wrote(app, client, survey):
    """The other request wrote the row between our lookup and our write: our write fails in its savepoint, what
    the request changed before stays, and the row of the other is used."""
    with app.app_context():
        user_id = db.session.scalar(db.select(User.id))
        engine = create_engine(db.engine.url)
        with engine.begin() as other:
            other.execute(text("INSERT INTO survey_pin (user_id, survey_id, created_at) VALUES (:u, 1, :t)"),
                          {"u": user_id, "t": utcnow()})
        engine.dispose()
        db.session.get(Survey, 1).title = "Geändert"  # earlier work of the same request
        row = get_or_create(SurveyPin, known_missing=True, user_id=user_id, survey_id=1)
        db.session.commit()
        assert row.id and db.session.scalar(db.select(db.func.count(SurveyPin.id))) == 1
        assert db.session.get(Survey, 1).title == "Geändert"


def test_the_marks_use_get_or_create(app, client, survey):
    with app.app_context():
        user = db.session.scalar(db.select(User))
        first = get_or_create(EditMark, survey_id=1, key="settings", user_id=user.id)
        db.session.commit()
        assert get_or_create(EditMark, survey_id=1, key="settings", user_id=user.id).id == first.id
    client.post("/dashboard/pin/1")
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(SurveyPin.id))) == 1


# --- C5: IAT without terms --------------------------------------------------------------------------------------------


def test_an_iat_without_terms_shows_its_preview(app, client, survey):
    add(client, "iat")
    with app.app_context():
        item = db.session.scalar(db.select(Item).filter_by(type="iat"))
        item.config = {**item.config, "stimuli": []}
        db.session.commit()
        item_id = item.id
        order, blocks = item.kind.plan(db.session.get(Item, item_id), "seed")
        assert len(blocks) == 7 and all(b["trials"] == [] for b in blocks)
    assert client.get(f"/surveys/1/editor/items/{item_id}/frame").status_code == 200
