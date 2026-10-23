"""Package Q6 of the security and performance audit 2026-10-09b: small performance fixes with unchanged output
(P3 Word questionnaire cells, P4/V3 construct names of the consent, P6 Administration > Server, P12 exports)."""
import io
import zipfile

from sqlalchemy import event

from app.extensions import db
from app.models import Survey
from tests.conftest import add


class Statements:
    """Counts the SQL statements while it is open; .text holds them."""

    def __init__(self, app):
        self.engine = db.engine
        self.text = []

    def _seen(self, conn, cursor, statement, *args):
        self.text.append(statement)

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._seen)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._seen)

    def count(self, part):
        return sum(part in s for s in self.text)


def _constructs(client, *keys):
    for key in keys:
        add(client, f"construct:{key}")


# --- P3 ---------------------------------------------------------------------------------------------------------

def test_p3_word_questionnaire_is_the_same_as_with_table_cell(app, client, survey, monkeypatch):
    """The cells are read once per table; the document is the same as with python-docx's table.cell per cell."""
    from app import questionnaire_docx

    for kind in ("likert", "semantic", "slider", "side_by_side", "maxdiff"):
        add(client, kind)
    _constructs(client, "bfi10")

    def body():
        with app.test_request_context():
            data = questionnaire_docx.build(db.session.get(Survey, 1), "codebook", ui="de")
        raw = data.getvalue() if hasattr(data, "getvalue") else data
        z = zipfile.ZipFile(io.BytesIO(raw))
        return z.read("word/document.xml")

    new = body()

    class ByCell:  # the former way: table.cell(r, c) on every access
        def __init__(self, table):
            self.table = table

        def __getitem__(self, r):
            return type("Row", (), {"__getitem__": lambda _, c: self.table.cell(r, c)})()

    monkeypatch.setattr(questionnaire_docx, "_matrix", ByCell)
    assert body() == new
    assert b"BFI" in new


# --- P4 / V3 ----------------------------------------------------------------------------------------------------

def test_p4_construct_names_ask_once_per_request(app, client, survey):
    from app.items import consent as consent_mod

    _constructs(client, "bfi10", "gender", "tb")  # tb: kut needs a documented permission (PR #1187)
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        for b in s.blocks:
            list(b.items)
        with Statements(app) as seen:
            first = consent_mod._construct_names(s)
            assert consent_mod._construct_names(s, "en") and consent_mod._construct_names(s) == first
            copy = s.consent_item.kind.pool_copy(s.consent_item)
        assert seen.count("SELECT construct.id") == 1 and seen.count("tag.slug") == 1
        assert len(first) == 2 and "Big Five" in first[0]  # gender is a standard question: named only without scales
        assert copy["names"] == first and not consent_mod._only_standard(s)
    with app.test_request_context():  # a new request asks again and sees a removed construct
        s = db.session.get(Survey, 1)
        tb = next(i for b in s.blocks for i in b.items if (i.config.get("construct") or {}).get("key") == "tb")
        tb.config = {k: v for k, v in tb.config.items() if k != "construct"}
        db.session.commit()
        assert len(consent_mod._construct_names(s)) == 1


def test_p4_only_standard_questions(app, client, survey):
    from app.items import consent as consent_mod

    _constructs(client, "gender")
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        assert consent_mod._only_standard(s) and consent_mod._construct_names(s)
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        for b in s.blocks:
            b.items = [i for i in b.items if not (i.config or {}).get("construct")]
        db.session.flush()
        assert not consent_mod._only_standard(s) and consent_mod._construct_names(s) == []
        db.session.rollback()


def test_p4_privacy_page_of_participants_asks_once(app, client, survey):
    _constructs(client, "bfi10", "kut")
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.status = Survey.RUNNING
        db.session.commit()
        uid = s.public_key
    with app.app_context(), Statements(app) as seen:
        page = app.test_client().get(f"/s/{uid}/privacy").data.decode()
    assert "Metaanalysen" in page
    assert seen.count("SELECT construct.id") <= 1


# --- P6 ---------------------------------------------------------------------------------------------------------

def test_p6_server_tab_keeps_counts_and_schema_comparison_a_minute(app, client, monkeypatch):
    from app import dbmove

    with app.app_context(), Statements(app) as seen:
        assert client.get("/admin/database").status_code == 200
        first = seen.count("count(*)")
        assert first > 10
        seen.text.clear()
        assert client.get("/admin/database").status_code == 200
        assert seen.count("count(*)") == 0  # kept
        now = dbmove.time.monotonic()
        monkeypatch.setattr(dbmove.time, "monotonic", lambda: now + dbmove.STATUS_SECONDS + 1)
        client.get("/admin/database")
        assert seen.count("count(*)") == first  # read again after the minute
        seen.text.clear()
        client.post("/admin/database", data={"action": "check", "host": "", "name": "x", "user": "u"})
        assert seen.count("count(*)") == first  # an action on the page reads them anew


# --- P12 --------------------------------------------------------------------------------------------------------

def test_p12_codebook_asks_for_the_constructs_once(app, client, survey):
    from app import export

    _constructs(client, "bfi10", "kut", "gender")
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        with Statements(app) as seen:
            text = export.codebook_csv(s)
        assert seen.count("SELECT construct.id") == 1
        assert "Big Five" in text or "BFI" in text


def test_p12_constructs_find_outside_prefetch_and_request(app, client, survey):
    from app import constructs

    _constructs(client, "bfi10")
    with app.app_context():  # no request: an ordinary query
        s = db.session.get(Survey, 1)
        constructs.prefetch(s)
        assert constructs.find("bfi10").key == "bfi10" and constructs.find("nope") is None
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        constructs.prefetch(s)
        with Statements(app) as seen:
            assert constructs.find("bfi10").key == "bfi10"
            assert constructs.find("kut").key == "kut"  # not in the survey: asked for
        assert seen.count("SELECT construct.id") == 1


def test_p12_survey_file_reads_images_in_one_query(app, client, survey):
    from app import export, transfer

    for _ in range(3):
        add(client, "likert")
    with app.app_context():
        s = db.session.get(Survey, 1)
        before = transfer.dump(s, with_files=False)
        db.session.expire_all()
        s = db.session.get(Survey, 1)
        with Statements(app) as seen:
            after = transfer.dump(s, with_files=False)
        assert seen.count("FROM asset") == 1 and seen.count("FROM item") == 1
        assert {k: v for k, v in after.items() if k != "exported_at"} == {
            k: v for k, v in before.items() if k != "exported_at"}
        export.preload(s, assets=True)  # all loaded: nothing to ask
        with Statements(app) as seen:
            export.preload(s, assets=True)
        assert seen.text == []
