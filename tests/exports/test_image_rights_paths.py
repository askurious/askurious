"""Attribution survives real upload, transfer and document-conversion boundaries."""
import io
import json
import os
from zipfile import ZipFile

import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from app import image_rights, media, questionnaire_docx, svg_safe
from app.extensions import db
from app.models import Asset, BlogImage, Survey
from tests.conftest import add
from tests.exports.test_transfer import _upload
from tests.platform.test_blog import _new


def attributed_png():
    info = PngInfo()
    info.add_itxt("Author", "Ada Example")
    info.add_itxt("Copyright", "Copyright 2026 Ada Example")
    info.add_itxt("License", "CC BY 4.0")
    info.add_itxt("License URL", "https://creativecommons.org/licenses/by/4.0/")
    info.add_itxt("Comment", "PRIVATE editing history")
    exif = Image.Exif()
    exif[272] = "PRIVATE camera model"
    out = io.BytesIO()
    Image.new("RGB", (80, 40), "blue").save(out, "PNG", pnginfo=info, exif=exif)
    return out.getvalue()


def assert_rights(data):
    rights = image_rights.from_bytes(data)
    assert rights["creator"] == ["Ada Example"]
    assert rights["copyright"] == ["Copyright 2026 Ada Example"]
    assert rights["license"] == ["CC BY 4.0"]
    assert rights["license_url"] == ["https://creativecommons.org/licenses/by/4.0/"]
    assert b"PRIVATE" not in data


def test_plain_license_url_query_parameters_are_preserved():
    info = PngInfo()
    link = "https://example.test/terms?x=1&copy=2&lang=en"
    info.add_itxt("License URL", link)
    out = io.BytesIO()
    Image.new("RGB", (2, 2)).save(out, "PNG", pnginfo=info)
    converted = media.participant_upload(out.getvalue())
    assert image_rights.from_bytes(converted)["license_url"] == [link]


def test_rights_survive_upload_document_conversion_and_survey_transfer(app, client, survey):
    add(client, "image")
    response = client.post("/surveys/1/editor/items/2/upload",
                           data={"file": (io.BytesIO(attributed_png()), "picture.png")},
                           content_type="multipart/form-data")
    assert response.status_code == 302
    with app.app_context():
        original = db.session.get(Survey, 1)
        asset = db.session.scalar(db.select(Asset))
        path = os.path.join(media.upload_folder(original.uid), asset.filename)
        with open(path, "rb") as source:
            stored = source.read()
        assert_rights(stored)
        assert Image.open(io.BytesIO(stored)).format == "WEBP"
        document = questionnaire_docx.build(original)
    with ZipFile(io.BytesIO(document)) as archive:
        images = [name for name in archive.namelist() if name.startswith("word/media/")]
        assert len(images) == 1
        assert_rights(archive.read(images[0]))

    exported = json.loads(client.get("/surveys/1/export/survey.json").data)
    assert "ist als Entwurf angelegt" in _upload(client, exported, title="Imported with attribution")
    with app.app_context():
        imported = db.session.scalar(db.select(Survey).filter_by(title="Imported with attribution"))
        image = next(a for b in imported.blocks for i in b.items for a in i.assets)
        with open(os.path.join(media.upload_folder(imported.uid), image.filename), "rb") as source:
            assert_rights(source.read())


def test_blog_upload_preserves_rights_while_removing_private_metadata(app, client):
    post = _new(client, title_de="Attribution", body_de="Example")
    response = client.post(f"/admin/blog/{post}/images",
                           data={"image": (io.BytesIO(attributed_png()), "picture.png")},
                           content_type="multipart/form-data")
    assert response.status_code == 302
    with app.app_context():
        name = db.session.scalar(db.select(BlogImage)).filename
    response = client.get(f"/blog/media/{post}/{name}")
    assert response.status_code == 200
    assert_rights(response.data)


SVG_RIGHTS = b'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"
 xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
 xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:cc="http://creativecommons.org/ns#">
 <metadata><rdf:RDF><rdf:Description>
 <dc:creator><rdf:Seq><rdf:li>Ada Example</rdf:li></rdf:Seq></dc:creator>
 <dc:rights>Copyright 2026 Ada Example</dc:rights>
 <cc:license rdf:resource="https://creativecommons.org/licenses/by/4.0/"/>
 <dc:description>PRIVATE editing history</dc:description>
 <script xmlns="http://www.w3.org/2000/svg">alert(1)</script>
 </rdf:Description></rdf:RDF></metadata><rect width="10" height="10"/></svg>'''


def test_svg_rights_are_rebuilt_without_scripts_or_editor_history():
    cleaned, width, height = svg_safe.clean(SVG_RIGHTS)
    assert (width, height) == (10, 10)
    assert image_rights.extract_xmp(cleaned) == {
        "creator": ["Ada Example"], "copyright": ["Copyright 2026 Ada Example"],
        "license_url": ["https://creativecommons.org/licenses/by/4.0/"]}
    assert b"PRIVATE" not in cleaned and b"<script" not in cleaned and b"alert(1)" not in cleaned
    assert svg_safe.clean(cleaned)[0] == cleaned  # transfer/re-upload does not lose or duplicate attribution
    # A large drawing still retains its small rights packet.
    large = SVG_RIGHTS.replace(b"<rect", b"<!--" + b"x" * (130 * 1024) + b"--><rect")
    assert image_rights.extract_xmp(svg_safe.clean(large)[0])["creator"] == ["Ada Example"]
    many_shapes = SVG_RIGHTS.replace(b"<metadata>", b'<path d="M0 0L1 1"/>' * 4200 + b"<metadata>")
    import xml.etree.ElementTree as ET

    tree = ET.fromstring(svg_safe.clean(many_shapes)[0])
    packet = tree.find("{http://www.w3.org/2000/svg}metadata")
    assert image_rights.extract_xmp_tree(packet)["creator"] == ["Ada Example"]


def test_svg_attribution_alone_is_not_a_drawing():
    with pytest.raises(svg_safe.Refused, match="keine Zeichnung"):
        svg_safe.clean(SVG_RIGHTS.replace(b'<rect width="10" height="10"/>', b""))
