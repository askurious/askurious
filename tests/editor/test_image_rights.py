"""Attribution survives image derivatives; private camera/location data still does not."""
import csv
import io
import os
import zipfile
import zlib

import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from app import image_rights, media, zenodo


XMP = b'''<x:xmpmeta xmlns:x="adobe:ns:meta/" xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
 xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/"
 xmlns:xmpRights="http://ns.adobe.com/xap/1.0/rights/" xmlns:cc="http://creativecommons.org/ns#"
 xmlns:exif="http://ns.adobe.com/exif/1.0/">
 <rdf:RDF><rdf:Description photoshop:Credit="Photo agency" exif:GPSLatitude="PRIVATE LOCATION">
 <dc:creator><rdf:Seq><rdf:li>Anna Example</rdf:li><rdf:li>Max Example</rdf:li></rdf:Seq></dc:creator>
 <dc:rights><rdf:Alt><rdf:li>Copyright 2026 Anna and Max</rdf:li></rdf:Alt></dc:rights>
 <dc:source>https://photos.example/original</dc:source>
 <xmpRights:UsageTerms>CC BY 4.0</xmpRights:UsageTerms>
 <cc:license rdf:resource="https://creativecommons.org/licenses/by/4.0/"/>
 </rdf:Description></rdf:RDF></x:xmpmeta>'''


def photo(fmt="JPEG", size=(80, 40), xmp=False, animated=False):
    exif = Image.Exif()
    exif[315], exif[33432] = "Anna Example", "Copyright 2026 Anna"
    exif[271], exif[274] = "PRIVATE CAMERA", 6
    exif[34853] = {1: "N", 2: (50.0, 46.0, 30.0)}
    img = Image.new("RGB", size, (80, 20, 170))
    out, options = io.BytesIO(), {"exif": exif}
    if xmp:
        if fmt == "PNG":
            info = PngInfo()
            info.add_itxt("XML:com.adobe.xmp", XMP.decode())
            options["pnginfo"] = info
        else:
            options["xmp"] = XMP
    if animated:
        options.update(save_all=True, append_images=[Image.new("RGB", size, (20, 80, 170))], duration=[100, 200], loop=0)
    img.save(out, format=fmt, **options)
    return out.getvalue()


def assert_private_removed(data):
    assert b"PRIVATE CAMERA" not in data and b"PRIVATE LOCATION" not in data
    with Image.open(io.BytesIO(data)) as result:
        assert not result.getexif().get(271) and not result.getexif().get(34853)


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
def test_upload_preserves_exif_copyright_and_rotates_without_gps(fmt):
    result = media.strip_metadata(photo(fmt))
    assert_private_removed(result)
    with Image.open(io.BytesIO(result)) as img:
        assert img.size == (40, 80)
        assert img.getexif()[315] == "Anna Example"
        assert img.getexif()[33432] == "Copyright 2026 Anna"


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
def test_xmp_creators_credit_source_and_license_survive_conversion(fmt):
    original = photo(fmt, xmp=True)
    for transformed in (media.strip_metadata(original), media.fit_still(original, 20),
                        media.participant_upload(original, 20), media.optimize(original, 20),
                        zenodo._jpeg(media.participant_upload(original, 20))):
        assert_private_removed(transformed)
        rights = image_rights.from_bytes(transformed)
        assert rights["creator"] == ["Anna Example", "Max Example"]
        assert rights["copyright"] == ["Copyright 2026 Anna", "Copyright 2026 Anna and Max"]
        assert rights["credit"] == ["Photo agency"]
        assert rights["source"] == ["https://photos.example/original"]
        assert rights["license"] == ["CC BY 4.0"]
        assert rights["license_url"] == ["https://creativecommons.org/licenses/by/4.0/"]


@pytest.mark.parametrize("fmt", ["PNG", "WEBP"])
def test_animation_preserves_rights_and_timing_without_private_metadata(fmt):
    result = media.strip_metadata(photo(fmt, xmp=True, animated=True))
    assert_private_removed(result)
    assert image_rights.from_bytes(result)["credit"] == ["Photo agency"]
    with Image.open(io.BytesIO(result)) as image:
        assert image.n_frames == 2
        durations = []
        for index in range(image.n_frames):
            image.seek(index)
            image.load()
            durations.append(image.info["duration"])
        assert durations == [100, 200]


def test_png_text_and_legacy_iptc_credit_survive():
    info = PngInfo()
    for name, value in {"Author": "Anna M\u00fcller", "Copyright": "Copyright Anna", "Credit": "Museum",
                        "Source": "Museum archive", "License": "Permission for this study",
                        "License URL": "https://museum.example/terms", "Comment": "PRIVATE COMMENT"}.items():
        info.add_itxt(name, value)
    out = io.BytesIO()
    Image.new("RGB", (20, 10)).save(out, format="PNG", pnginfo=info)
    clean = media.participant_upload(out.getvalue())
    assert b"PRIVATE COMMENT" not in clean
    assert image_rights.from_bytes(clean)["creator"] == ["Anna M\u00fcller"]
    assert image_rights.from_bytes(clean)["license"] == ["Permission for this study"]

    # A real Photoshop APP13/IPTC-IIM resource, not a mocked parser return value.
    iim = b""
    for tag, value in ((80, b"Anna M\xfcller"), (110, b"Museum"), (115, b"Archive 12"), (116, b"Copyright Museum")):
        iim += bytes((28, 2, tag)) + len(value).to_bytes(2, "big") + value
    resource = b"Photoshop 3.0\x00" + b"8BIM\x04\x04\x00\x00" + len(iim).to_bytes(4, "big") + iim
    resource += b"\x00" if len(iim) % 2 else b""
    raw = photo()
    raw = raw[:2] + b"\xff\xed" + (len(resource) + 2).to_bytes(2, "big") + resource + raw[2:]
    rights = image_rights.from_bytes(media.strip_metadata(raw))
    assert "Anna M\u00fcller" in rights["creator"] and rights["credit"] == ["Museum"]
    assert rights["source"] == ["Archive 12"] and "Copyright Museum" in rights["copyright"]


def test_only_bounded_plain_rights_are_kept():
    assert image_rights.extract_xmp(b'<!DOCTYPE x [<!ENTITY secret "value">]><x>&secret;</x>') == {}
    assert image_rights.extract_xmp(XMP + b" " * image_rights.MAX_PACKET) == {}
    assert image_rights.extract_xmp(b'<?xml version="1.0" encoding="not-an-encoding"?><x/>') == {}
    hostile = XMP.replace(b"Photo agency", b"&lt;script&gt;secret&lt;/script&gt;Museum")
    hostile = hostile.replace(b"https://creativecommons.org/licenses/by/4.0/", b"javascript:alert(1)")
    rights = image_rights.extract_xmp(hostile)
    assert rights["credit"] == ["Museum"] and not rights.get("license_url")
    info = PngInfo()
    info.add_itxt("Copyright", "x" * 100_000)
    out = io.BytesIO()
    Image.new("RGB", (20, 10)).save(out, format="PNG", pnginfo=info)
    rights = image_rights.from_bytes(media.strip_metadata(out.getvalue()))
    assert len(rights["copyright"][0]) == image_rights.MAX_VALUE


@pytest.mark.parametrize("value,expected", [("AT&T", "AT&T"), ("R&D", "R&D"), ("Copyright by AT&T", "Copyright by AT&T"),
                                           ("Copyright <![private]>", "Copyright"),
                                           ("Copyright <Studio> Ltd", "Copyright <Studio> Ltd")])
def test_plain_attribution_names_survive_ampersands_and_broken_markup(value, expected):
    info = PngInfo()
    info.add_itxt("Copyright", value)
    out = io.BytesIO()
    Image.new("RGB", (20, 10)).save(out, format="PNG", pnginfo=info)
    assert image_rights.from_bytes(media.strip_metadata(out.getvalue()))["copyright"] == [expected]


def _png_chunk(kind, data):
    return len(data).to_bytes(4, "big") + kind + data + zlib.crc32(kind + data).to_bytes(4, "big")


@pytest.mark.parametrize("kind,payload", [(b"tEXt", b"Copyright\x00Late copyright"),
                                         (b"zTXt", b"Copyright\x00\x00" + zlib.compress(b"Late copyright")),
                                         (b"iTXt", b"Copyright\x00\x00\x00\x00\x00Late copyright"),
                                         (b"iTXt", b"Copyright\x00\x01\x00\x00\x00" + zlib.compress(b"Late copyright"))])
def test_png_rights_after_pixels_are_preserved_without_decoding_during_read(kind, payload, monkeypatch):
    raw = photo("PNG")  # an early eXIf chunk: getexif() need not read the later text
    raw = raw[:-12] + _png_chunk(kind, payload) + raw[-12:]
    with Image.open(io.BytesIO(raw)) as image:
        position = image.fp.tell()
        monkeypatch.setattr(image, "load", lambda: pytest.fail("metadata read must not decode pixels"))
        assert "Late copyright" in image_rights.read(image)["copyright"]
        assert image.fp.tell() == position
    assert "Late copyright" in image_rights.from_bytes(media.strip_metadata(raw))["copyright"]


def test_png_metadata_read_limits_chunks_and_decompression(monkeypatch):
    raw = photo("PNG")
    additions = (_png_chunk(b"tEXt", b"Credit\x00" + b"x" * (image_rights.MAX_PACKET + 1))
                 + _png_chunk(b"zTXt", b"Credit\x00\x00" + zlib.compress(b"x" * (image_rights.MAX_PACKET + 1)))
                 + _png_chunk(b"iTXt", b"Credit\x00\x01\x00\x00\x00" + zlib.compress(b"Museum")))
    raw = raw[:-12] + additions + raw[-12:]
    with Image.open(io.BytesIO(raw)) as image:
        monkeypatch.setattr(image, "load", lambda: pytest.fail("metadata read must not decode pixels"))
        assert image_rights.read(image)["credit"] == ["Museum"]


def test_gif_reconstruction_keeps_only_marked_rights_comment():
    rights = {"creator": ["Anna Example"], "copyright": ["Copyright 2026 Anna"]}
    out = io.BytesIO()
    Image.new("P", (40, 20)).save(out, format="GIF", **image_rights.save_options(rights, "GIF"))
    result = media.fit_still(out.getvalue(), 10)
    assert image_rights.from_bytes(result) == rights
    with Image.open(io.BytesIO(result)) as image:
        assert image.size == (10, 5)


def test_logo_icon_and_thumbnail_keep_rights(app, survey):
    from app.extensions import db
    from app.models import Survey

    with app.app_context():
        s = db.session.get(Survey, 1)
        logo = media.save_logo(s, photo("PNG", size=(1500, 1500), xmp=True))
        icon = media.save_icon(s, logo["file"])
        thumb = media.thumbnail_name(s.uid, logo["file"])
        assert thumb != logo["file"]
        for name in (logo["file"], icon, thumb):
            with open(os.path.join(media.upload_folder(s.uid), name), "rb") as stream:
                data = stream.read()
            assert_private_removed(data)
            assert image_rights.from_bytes(data)["credit"] == ["Photo agency"]


def test_archive_contains_attribution_in_jpeg_and_csv(app, client, survey):
    from app.extensions import db
    from app.models import Asset, Item, Survey
    from tests.conftest import add

    add(client, "image")
    with app.app_context():
        s = db.session.get(Survey, 1)
        item = db.session.scalar(db.select(Item).filter_by(type="image"))
        name = media.save_bytes(s, media.participant_upload(photo("PNG", xmp=True)))
        item.assets.append(Asset(filename=name, status=Asset.APPROVED, source="upload"))
        db.session.commit()
        archive = zenodo.images(s)[0][1]
    with zipfile.ZipFile(io.BytesIO(archive)) as packed:
        rows = list(csv.DictReader(io.StringIO(packed.read("images.csv").decode("utf-8"))))
        assert len(rows) == 1 and rows[0]["source"] == "upload"
        assert rows[0]["rights_creator"] == "Anna Example\nMax Example"
        assert rows[0]["rights_credit"] == "Photo agency"
        assert rows[0]["rights_license"] == "CC BY 4.0"
        assert rows[0]["rights_license_url"] == "https://creativecommons.org/licenses/by/4.0/"
        archived = packed.read(rows[0]["file"])
        assert rows[0]["file"].endswith(".jpg")
        assert image_rights.from_bytes(archived)["credit"] == ["Photo agency"]
        assert_private_removed(archived)
