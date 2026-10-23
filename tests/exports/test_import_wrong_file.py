"""Reading in a file that is no Askurious survey file says so (pre-test 2026-10-16: a questionnaire from Word could
not be uploaded, and nothing said why)."""
import io


def test_a_word_file_is_refused_with_a_reason(app, client):
    page = client.post("/surveys/new/import", data={"file": (io.BytesIO(b"PK\x03\x04word"), "Fragebogen.docx")},
                       content_type="multipart/form-data", follow_redirects=True).data.decode()
    assert "„Fragebogen.docx“ ist keine Befragungsdatei von Askurious" in page and "Word, PDF oder Excel" in page


def test_the_card_says_which_files_it_takes(app, client):
    page = client.get("/surveys/new?start=datei").data.decode()
    assert "aus Askurious exportierte Befragungsdatei" in page and "kein Word, PDF oder Excel" in page
