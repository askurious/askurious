"""How the texts of a loop read with the values of each row (UI audit 2026-10-12, U45): the loop page lists every
text with a placeholder as participants read it in each pass, and the AI review of the wording gets these versions
too ("Bewertung: dem Fahrrad" shows before the field)."""
from app import loops, text_review
from app.extensions import db
from app.models import Block, Item, Survey
from tests.editor.test_loops import setup


def test_texts_read_with_every_row(app, client, survey):
    url = setup(client)
    with app.app_context():
        block = db.session.get(Block, 2)
        block.titles = {"de": "Bewertung: {{robot}}"}
        block.show_title = True
        db.session.commit()
        read = loops.read_with_rows(block, "Würden Sie einen {{robot}} {{setting}} nutzen? {{pid}}")
        assert [t for _n, t in read][:2] == ["Würden Sie einen Pflegeroboter zu Hause nutzen? {{pid}}",
                                             "Würden Sie einen Pflegeroboter im Heim nutzen? {{pid}}"]
        assert loops.read_with_rows(block, "ohne Platzhalter") == []
        found = loops.readings(db.session.get(Survey, 1), loops.node_of(block))
        assert found[0]["where"] == "Blocktitel" and found[0]["rows"][0] == (1, "Bewertung: Pflegeroboter")
        assert any(r["text"] == "nein ({{robot}})" for r in found)          # answer options too
    page = client.get(url, follow_redirects=True).get_data(as_text=True)
    assert "So lesen sich die Texte" in page and "Würden Sie einen Serviceroboter im Heim nutzen?" in page


def test_the_ai_review_reads_the_filled_in_versions(app, client, survey):
    setup(client)
    with app.app_context():
        block = db.session.get(Block, 2)
        block.titles = {"de": "Bewertung: {{robot}}"}
        block.show_title = True
        db.session.commit()
        facts = text_review._facts(db.session.get(Item, 2))
        question = next(p for p in facts["parts"] if p["id"] == "question")
        assert len(question["as_read"]) == 4 and "Pflegeroboter zu Hause" in question["as_read"][0]
        assert "as_read" not in next(p for p in facts["parts"] if p["text"] == "ja")
        assert '"as_read"' in text_review.RULES
        sent = {}

        def fake(instructions, facts_json, **kw):
            sent["facts"] = facts_json
            return '{"remarks": [], "parts": []}'

        import app.ai as ai_mod

        old = ai_mod.generate_text
        ai_mod.generate_text = fake
        try:
            text_review.review_survey(db.session.get(Survey, 1))
        finally:
            ai_mod.generate_text = old
        assert '"block_as_read"' in sent["facts"] and "Bewertung: Serviceroboter" in sent["facts"]
