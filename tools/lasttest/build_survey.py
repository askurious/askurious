"""Build the survey of the load test as a file to read in (Befragungen > Befragung aus Datei einlesen).

    python tools/lasttest/build_survey.py

Writes tools/lasttest/lasttest-befragung.json. The survey is built through the app itself, in a throwaway
database, so the file always fits the current code: consent, the usual demographics of a new survey, then
eight pages with the common question types (matrix, single and multiple choice, drop-down, numbers, text).
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lasttest-befragung.json")
ADMIN = "0000-0000-0000-001X"


def main():
    from werkzeug.datastructures import MultiDict

    from app import create_app, transfer
    from app.extensions import db
    from app.models import Item, Survey
    from config import Config

    folder = tempfile.mkdtemp(prefix="lasttest-")

    class Build(Config):
        TESTING = True
        SECRET_KEY = "lasttest"
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{os.path.join(folder, 'build.db')}"
        SQLALCHEMY_ENGINE_OPTIONS = {}
        UPLOAD_FOLDER = os.path.join(folder, "uploads")
        AI_PRICES_FILE = os.path.join(folder, "ai_prices.json")
        WTF_CSRF_ENABLED = False
        DEV_LOGIN = True
        PUBLIC_BASE_URL = "http://localhost:5060"
        ADMIN_ORCIDS = [ADMIN]
        OPENAI_API_KEY = ""
        TRANSLATION_CHECK = False

    app = create_app(Build)
    c = app.test_client()
    c.get(f"/auth/dev-login?orcid={ADMIN}&name=Lasttest")
    c.post("/nutzungsbedingungen/", data={"accept": "1"})
    c.post("/surveys/new", data={"title": "Lasttest", "languages": ["de"], "default_language": "de"})
    with app.app_context():
        survey = db.session.scalar(db.select(Survey))
        code, block = survey.code, survey.blocks[-1].id
    base = f"/surveys/{code}/editor"
    c.post(f"/surveys/{code}/consent-facts", data={"min_age": "", "researchers": ADMIN})

    def add(kind, **data):
        c.post(f"{base}/blocks/{block}/items", data={"type": kind})
        with app.app_context():
            item_id = db.session.scalar(db.select(db.func.max(Item.id)))
        if data or kind not in ("page_break",):
            c.post(f"{base}/items/{item_id}", data=MultiDict(data.pop("_pairs", [])) if "_pairs" in data else data)
        return item_id

    with app.app_context():
        consent = db.session.get(Survey, survey.id).consent_item.id
    c.post(f"{base}/items/{consent}", data={
        "code": "consent", "question": "Einwilligung", "body": "Lasttest: bitte nicht für echte Daten verwenden.",
        "duration": "5", "confirm_voluntary": "1", "confirm_data": "1", "confirm_commitment": "1",
        "agree": "Fortsetzen", "disagree": "Nein"})

    def options(*labels):
        pairs = []
        for n, label in enumerate(labels, start=1):
            pairs += [("option_id", ""), ("option_code", str(n)), ("option_label", label)]
        return pairs

    def statements(prefix, n):
        pairs = []
        for k in range(1, n + 1):
            pairs += [("stmt_id", f"s{k}"), ("stmt_code", ""), ("stmt_text", f"{prefix} {k}")]
        return pairs

    for page in range(1, 9):
        add("page_break")
        add("likert", _pairs=[("code", f"m{page}"), ("question", f"Wie sehr stimmen Sie zu? (Seite {page})"),
                              ("answer_mode", "optional"), ("points", "5"), ("labeling", "full"), ("randomize", "1")]
            + statements("Aussage", 6))
        add("single_choice", _pairs=[("code", f"e{page}"), ("question", f"Welche Antwort passt? (Seite {page})"),
                                     ("answer_mode", "optional")] + options("ja", "eher ja", "eher nein", "nein"))
        if page % 2:
            add("multiple_choice", _pairs=[("code", f"v{page}"), ("question", "Was trifft zu?"), ("answer_mode", "optional")]
                + options("rot", "blau", "grün", "gelb", "schwarz"))
        else:
            add("single_choice", _pairs=[("code", f"d{page}"), ("question", "Bitte wählen"), ("answer_mode", "optional"),
                                         ("display", "dropdown")] + options(*[f"Option {k}" for k in range(1, 13)]))
        if page in (3, 6):
            add("text_input", code=f"z{page}", question="Eine Zahl, bitte", validation="integer", answer_mode="optional")
        if page == 8:
            add("text_input", code="txt", question="Möchten Sie noch etwas sagen?", answer_mode="optional")

    with app.app_context():
        survey = db.session.get(Survey, survey.id)
        with open(OUT, "w", encoding="utf-8") as f:
            f.write(transfer.dumps(survey, with_files=False))
        items = sum(len(b.items) for b in survey.blocks)
        db.engine.dispose()
    print(f"{OUT}: {items} Elemente")


if __name__ == "__main__":
    main()
