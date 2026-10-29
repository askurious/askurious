"""The fonts of the PDFs (app/pdf_fonts.py): every survey language instead of "?" for all but Western European
letters. Noto Sans lies in the repository with its licence; Arabic and Chinese come with the Docker image and are
left out without an error where they are missing."""
import io
import os
import re

import pytest
from pypdf import PdfReader

from app import paper_pdf, pdf_fonts, study_material
from app.extensions import db
from app.models import Item, Survey
from tests.conftest import add, fill_consent

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SAMPLES = {"pl": "Zażółć gęślą jaźń, Łódź", "tr": "Şişli’de ığdır ve Ğ", "ru": "Съешь ещё этих мягких булок"}
ARABIC, CHINESE = "مرحبا بالعالم", "你好世界"


@pytest.fixture
def no_extra_fonts(tmp_path, monkeypatch):
    """A server without the large fonts of the image (Plesk, a development machine)."""
    monkeypatch.setenv("PDF_FONT_DIR", str(tmp_path))


def _text(body):
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(body)).pages)


def _pdf(texts, style_font=pdf_fonts.REGULAR):
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate

    style = ParagraphStyle("b", fontName=pdf_fonts.font(), fontSize=11, leading=15)
    out = io.BytesIO()
    SimpleDocTemplate(out, invariant=1).build([pdf_fonts.paragraph(pdf_fonts.markup(t), style) for t in texts])
    return out.getvalue()


def test_noto_sans_lies_in_the_repository_with_its_licence():
    folder = os.path.join(ROOT, "app", "data", "fonts")
    for _, name in pdf_fonts.OWN:
        with open(os.path.join(folder, name), "rb") as f:
            assert f.read(4) == b"\x00\x01\x00\x00", name  # TrueType outlines (reportlab reads no CFF)
    with open(os.path.join(folder, "OFL.txt"), encoding="utf-8") as f:
        licence = f.read()
    assert "SIL Open Font License, Version 1.1" in licence and "The Noto Project Authors" in licence
    notice = open(os.path.join(ROOT, "NOTICE"), encoding="utf-8").read()
    assert "app/data/fonts/**" in notice and "app/data/fonts/OFL.txt" in notice


def test_polish_turkish_and_russian_come_out_as_written(no_extra_fonts):
    text = _text(_pdf(list(SAMPLES.values())))
    for sample in SAMPLES.values():
        assert sample in text
    assert "?" not in text


def test_without_the_arabic_and_chinese_fonts_their_letters_stay_question_marks(no_extra_fonts):
    assert not pdf_fonts.available("NotoSansArabic") and not pdf_fonts.available("NotoSansSC")
    text = _text(_pdf([f"Arabisch: {ARABIC}", f"Chinesisch: {CHINESE}"]))
    assert "Arabisch: " in text and "Chinesisch: ????" in text and "?????" in text


def test_arabic_is_joined_and_turned_round_line_by_line():
    shown = pdf_fonts.visual(ARABIC)
    assert shown != ARABIC and not re.search("[ا-ي]", shown)  # joined forms (U+FExx) only
    assert pdf_fonts.rtl(ARABIC) and not pdf_fonts.rtl("Frage 1: " + ARABIC)
    # the first word of the reading order stands at the right end of the drawn line
    first = pdf_fonts.visual("مرحبا")
    assert shown.endswith(first)
    assert pdf_fonts.visual("2026 " + ARABIC).startswith(pdf_fonts.visual(ARABIC.split()[1]))


def test_chinese_breaks_between_characters_and_long_words_are_cut(no_extra_fonts):
    lines = pdf_fonts.lines(CHINESE * 20, 10, 60)
    assert len(lines) > 5 and all(pdf_fonts.width(line, 10) <= 60 for line in lines)
    assert pdf_fonts.lines("ein Wort\n\nund noch eins", 10, 1000) == ["ein Wort", "", "und noch eins"]


def test_standard_courier_falls_back_to_noto_sans_per_character():
    assert pdf_fonts.runs("Łódź", "Courier") == [("NotoSans", "Ł"), ("Courier", "ód"), ("NotoSans", "ź")]
    assert pdf_fonts.runs("abc 123", "Courier") == [("Courier", "abc 123")]


def test_paper_offers_polish_turkish_and_russian_but_not_arabic(no_extra_fonts):
    assert {"pl", "tr", "ru"} <= set(paper_pdf.languages())
    assert "ar" not in paper_pdf.languages() and "zh-Hans" not in paper_pdf.languages()


def test_survey_material_prints_the_question_in_every_script(app, client, survey, no_extra_fonts):
    fill_consent(client)
    add(client, "single_choice")
    question = " / ".join(SAMPLES.values()) + " / " + ARABIC
    with app.app_context():
        s = db.session.get(Survey, 1)
        item = db.session.scalar(db.select(Item).filter_by(type="single_choice"))
        texts = dict(item.texts or {})
        texts[s.default_language] = {**texts.get(s.default_language, {}), "question": question}
        item.texts = texts
        db.session.commit()
    with app.test_request_context():
        body = study_material.build(db.session.get(Survey, 1), "de")
    text = _text(body)
    for sample in SAMPLES.values():
        assert sample in text


def test_the_build_fetches_the_large_fonts_against_fixed_digests():
    from tools import fetch_pdf_fonts as fetch

    names = [name for name, _, _ in fetch.FILES]
    for family, regular, bold in pdf_fonts.EXTRA:
        assert regular in names and bold in names
    for name, url, digest in fetch.FILES:
        assert url.startswith("https://")
        assert name.endswith(".txt") or re.fullmatch(r"[0-9a-f]{64}", digest)
    dockerfile = open(os.path.join(ROOT, "deploy", "docker", "Dockerfile"), encoding="utf-8").read()
    assert "python /tmp/fetch_pdf_fonts.py /usr/share/fonts/askurious" in dockerfile
    assert f"ENV PDF_FONT_DIR={pdf_fonts.EXTRA_DIR}" in dockerfile
