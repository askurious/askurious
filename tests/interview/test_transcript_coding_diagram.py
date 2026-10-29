"""Interviews kodieren: the diagram of the category system as PNG and PDF besides the SVG, and in the Word coding guide
after the research question (Ralf 2026-10-29). PNG drawn with Pillow (app/svg_png.py), PDF with reportlab like the
figures of the results report; no new dependency."""
import io
import zipfile

from PIL import Image

from app import svg_png
from tests.interview.test_transcript_coding_review import _setup, _sub


def test_png_and_pdf_of_the_diagram(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    tired = _sub(client, base, main, "Erschöpfung")
    _sub(client, base, tired, "Abendliche Müdigkeit")
    png = client.get(f"{base}/map.png")
    assert png.status_code == 200 and png.mimetype == "image/png" and "attachment" in png.headers["Content-Disposition"]
    image = Image.open(io.BytesIO(png.data))
    assert image.size[0] > 1000 and image.size[0] > image.size[1]                 # wide, at twice the SVG's size
    pdf = client.get(f"{base}/map.pdf")
    assert pdf.status_code == 200 and pdf.data.startswith(b"%PDF") and pdf.mimetype == "application/pdf"
    page = client.get(f"{base}?view=categories&show=diagram").data.decode()
    assert "map.png" in page and "map.pdf" in page and "Diagramm als SVG" in page
    assert bea.get(f"{base}/map.png").status_code == 200                           # members see the diagram too


def test_the_coding_guide_holds_the_diagram(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    _sub(client, base, main, "Erschöpfung")
    guide = client.get(f"{base}/kodierleitfaden.docx")
    with zipfile.ZipFile(io.BytesIO(guide.data)) as z:
        media = [n for n in z.namelist() if n.startswith("word/media/")]
        body = z.read("word/document.xml").decode()
    assert media and media[0].endswith(".png")
    assert body.index("Forschungsfrage") < body.index("<w:drawing")                 # after the research question
    assert 'w:orient="landscape"' in body                                            # a wide diagram: a page across


def test_the_renderer_draws_text_shapes_and_paths():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="200" height="60" viewBox="0 0 200 60">'
           '<rect width="100%" height="100%" fill="#ffffff"/><rect x="5" y="5" width="60" height="30" rx="6" fill="#ff0000"/>'
           '<circle cx="100" cy="20" r="5" fill="#0000ff"/><path d="M 10 50 C 40 50, 60 40, 190 40" stroke="#00ff00" stroke-width="2" fill="none"/>'
           '<text x="190" y="20" text-anchor="end" font-weight="bold" fill="#000000">Müdigkeit</text></svg>')
    image = Image.open(io.BytesIO(svg_png.render(svg, scale=1))).convert("RGB")
    assert image.size == (200, 60)
    assert image.getpixel((30, 20)) == (255, 0, 0) and image.getpixel((100, 20)) == (0, 0, 255)
    assert any(image.getpixel((x, y))[1] > 200 and image.getpixel((x, y))[0] < 80 for x in range(150, 190) for y in range(36, 45))
    assert any(image.getpixel((x, 16)) == (0, 0, 0) or sum(image.getpixel((x, 16))) < 200 for x in range(120, 190))
