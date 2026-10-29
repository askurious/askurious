"""Interviews kodieren: searching the material (app/transcript_coding_search.py; Ralf 2026-10-29): terms with ODER,
upper and lower case, whole words, hits with place and context, pages, jumping to a hit, "Treffer kodieren als …"
with preview, storing and "Rückgängig", and the fold "Warum Treffer einzeln prüfen?"."""
from app import transcript_coding_search as search
from app.extensions import db
from app.models import Response
from tests.interview.test_transcript_coding import _transcript
from tests.interview.test_transcript_coding_review import _setup, _sub

TEXT = ("I: Wie geht es Ihnen abends? #00:00:04-0#\n\n"
        "B: Abends bin ich einfach fertig. Ich bin so müde. Dann noch die Mutter. #00:14:32-0#\n\n"
        "I: Und sonst?\n\n"
        "B: Müdigkeit kenne ich seit Jahren. Manchmal bin ich erschöpft, manchmal nicht müde.")


def test_terms_pattern_and_sentences():
    assert search.terms("müde ODER erschöpft, fertig | Müde") == ["müde", "erschöpft", "fertig"]
    assert search.terms('"nicht müde" OR Mutter') == ["nicht müde", "Mutter"]
    assert len(search.pattern(["MÜD"]).findall(TEXT)) == 3                         # müde, Müdigkeit, müde
    assert len(search.pattern(["müd"], words=True).findall(TEXT)) == 0            # whole words only
    assert len(search.pattern(["müde"], words=True).findall(TEXT)) == 2
    assert search.pattern(["nicht müde"]).search("ganz nicht\nmüde")              # any spaces between words
    start = TEXT.index("so müde")
    s0, s1 = search.sentence(TEXT, start, start + 7)
    assert TEXT[s0:s1] == "Ich bin so müde."
    c0, c1 = search.around(TEXT, start, start + 7)
    assert TEXT[c0:c1] == "B: Abends bin ich einfach fertig. Ich bin so müde. Dann noch die Mutter."
    u0, u1 = search.unit(TEXT, start, start + 7, "paragraph")
    assert TEXT[u0:u1] == "Abends bin ich einfach fertig. Ich bin so müde. Dann noch die Mutter."  # no label, no time mark


def test_search_page_hits_jump_and_code_with_undo(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    _transcript(app, rid, TEXT)
    cat = _sub(client, base, main, "Erschöpfung")
    page = client.get(f"{base}?q=müde+ODER+erschöpft&words=1").data.decode()
    assert "3 Treffer in 1 Interviews" in page and "<mark>müde</mark>" in page and "<mark>erschöpft</mark>" in page
    assert ", Pos. 2, 00:14:32" in page and "Warum Treffer einzeln prüfen?" in page
    assert f"/i/{rid}?at=" in page                                                # to the passage
    assert "Keine Treffer." in client.get(f"{base}?q=Urlaub").data.decode()
    assert "<mark>müde</mark>" in bea.get(f"{base}?q=müde").data.decode()         # members search what they see
    at = TEXT.index("so müde") + 3
    assert f'value="{rid}:{at}:{at + 4}"' in page                                   # the hit to tick
    data = {"q": "müde", "cat": cat, "unit": "sentence", "hit": [f"{rid}:{at}:{at + 4}"]}
    preview = client.post(f"{base}/search/code", data=data).data.decode()
    assert "„Ich bin so müde.“" in preview and "1 Stellen werden mit" in preview
    with app.app_context():
        assert not [c for c in db.session.get(Response, rid).interview.get("codings") or []]   # nothing stored yet
    done = client.post(f"{base}/search/code", data={**data, "action": "save"}).data.decode()
    assert "1 Stellen mit „Erschöpfung“ kodiert." in done
    with app.app_context():
        coded = db.session.get(Response, rid).interview["codings"]
        assert [c["quote"] for c in coded] == ["Ich bin so müde."]
        made = f"{rid}:{coded[0]['id']}"
    again = client.post(f"{base}/search/code", data={**data, "action": "save"}).data.decode()
    assert "1 übersprungen" in again                                              # already coded so: left out
    client.post(f"{base}/search/undo", data={"made": [made], "q": "müde"})
    with app.app_context():
        assert not db.session.get(Response, rid).interview["codings"]
    assert "Bitte eine Kategorie wählen." in client.post(f"{base}/search/code", data={**data, "cat": ""}, follow_redirects=True).data.decode()

