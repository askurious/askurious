"""Uploaded files that are small in bytes and huge once opened (audit of 2026-10-01, package 4)."""
import io
import types

import pytest
from PIL import Image

from app import media, svg_safe


def _png(width, height, mode="L", colour=255):
    out = io.BytesIO()
    Image.new(mode, (width, height), colour).save(out, format="PNG", compress_level=9)
    return out.getvalue()


def test_image_with_too_many_pixels_is_refused_before_decoding(app, monkeypatch):
    data = _png(8000, 7000)  # 56 megapixels in about 60 KB
    logo = _png(5000, 4000)  # 20 megapixels: too many for a logo
    assert len(data) < 200_000
    decoded = []
    real = Image.Image.load
    monkeypatch.setattr(Image.Image, "load", lambda self, *a, **kw: decoded.append(self.size) or real(self, *a, **kw))
    with pytest.raises(ValueError, match="zu viele Bildpunkte"):
        media.strip_metadata(data)
    with app.app_context():
        with pytest.raises(ValueError, match="zu viele Bildpunkte"):
            media.save_logo(types.SimpleNamespace(uid="t"), logo)
    assert decoded == []  # the size comes from the header


def test_images_within_the_limit_still_work(app):
    data = _png(3000, 2000, "RGB", (10, 20, 30))
    assert media._sniff(media.strip_metadata(data)) == "png"
    with app.app_context():
        saved = media.save_logo(types.SimpleNamespace(uid="t"), data)
        assert saved["kind"] == "png" and (saved["w"], saved["h"]) == (1200, 800)  # scaled to the logo size


def test_gif_dimensions_are_checked_too():
    out = io.BytesIO()
    Image.new("P", (9000, 8000)).save(out, format="GIF")
    with pytest.raises(ValueError, match="zu viele Bildpunkte"):
        media.strip_metadata(out.getvalue())


def test_upload_route_says_why(app, client, survey):
    from tests.conftest import add

    add(client, "image")
    r = client.post("/surveys/1/editor/items/2/upload", data={"file": (io.BytesIO(_png(8000, 7000)), "gross.png")},
                    content_type="multipart/form-data", follow_redirects=True)
    assert "zu viele Bildpunkte" in r.data.decode()


SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">{}</svg>'


def test_svg_with_nested_use_is_refused():
    """2 KB that the browser unfolds into a hundred billion shapes."""
    levels = ['<g id="a0"><rect width="1" height="1"/></g>']
    for n in range(1, 12):
        levels.append(f'<g id="a{n}">' + f'<use href="#a{n - 1}"/>' * 10 + "</g>")
    bomb = SVG.format("<defs>" + "".join(levels) + '</defs><use href="#a11"/>').encode()
    assert len(bomb) < 3000
    with pytest.raises(svg_safe.Refused, match="zu umfangreich"):
        svg_safe.clean(bomb)
    with pytest.raises(svg_safe.Refused, match="sich selbst"):
        svg_safe.clean(SVG.format('<g id="a"><use href="#a"/></g>').encode())
    # ordinary reuse stays
    ok = SVG.format('<defs><circle id="dot" r="1"/></defs>' + '<use href="#dot" x="1"/>' * 50).encode()
    assert svg_safe.clean(ok)[0].count(b"<use") == 50


def test_svg_values_call_known_functions_only():
    def cleaned(shape):
        return svg_safe.clean(SVG.format(shape).encode())[0].decode()

    assert "image-set" not in cleaned('<rect width="1" height="1" mask="image-set(\'//evil/x.png\' 1x)"/>')
    assert "image-set" not in cleaned('<rect width="1" height="1" style="mask: image-set(\'http://evil/x.png\' 1x); fill: red"/>')
    assert "fill: red" in cleaned('<rect width="1" height="1" style="mask: image-set(\'http://evil/x.png\' 1x); fill: red"/>')
    kept = cleaned('<g transform="translate(1 2) rotate(45)"><rect width="1" height="1" fill="rgb(1, 2, 3)" clip-path="url(#c)"/></g>')
    assert 'transform="translate(1 2) rotate(45)"' in kept and 'fill="rgb(1, 2, 3)"' in kept and 'clip-path="url(#c)"' in kept
    assert "M0 0 L(1)" in cleaned('<path d="M0 0 L(1)"/>') or "<path" in cleaned('<path d="M0 0 L1 1"/>')


@pytest.mark.parametrize("box", ["0 0 inf inf", "0 0 nan nan", "0 0 1e300 1e300", "0 0 -5 10"])
def test_svg_viewbox_must_be_a_finite_size(box):
    shape = '<rect width="1" height="1"/>'
    with pytest.raises(svg_safe.Refused, match="keine Größe"):
        svg_safe.clean(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{box}">{shape}</svg>'.encode())
    # with a usable width and height the drawing is taken, without the broken viewBox
    data, w, h = svg_safe.clean(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{box}" width="40" height="20">{shape}</svg>'.encode())
    assert (w, h) == (40, 20) and b'viewBox="0 0 40 20"' in data
