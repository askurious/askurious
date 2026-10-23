"""Moving images lose their metadata too (security and code audit 2026-10-13, DA-2): animated WebP and PNG and every
GIF were stored byte for byte, with EXIF, GPS position, XMP and comments, under /media for every participant."""
import io

import pytest
from PIL import Image

from app import media


def _moving(fmt):
    exif = Image.Exif()
    exif[0x010F] = "Kamera-XY"
    exif[0x8825] = {1: "N", 2: (50.0, 46.0, 30.0)}
    frames = [Image.new("RGB", (32, 24), c) for c in ((255, 0, 0), (0, 255, 0), (0, 0, 255))]
    options = {"save_all": True, "append_images": frames[1:], "duration": [120, 80, 200], "loop": 0}
    if fmt == "GIF":
        options["comment"] = b"GEHEIM GPS 50.77"
    else:
        options.update(exif=exif.tobytes(), xmp=b"<x:xmpmeta>GEHEIM GPS 50.77</x:xmpmeta>")
    out = io.BytesIO()
    frames[0].save(out, format=fmt, **options)
    return out.getvalue()


@pytest.mark.parametrize("fmt", ["WEBP", "PNG", "GIF"])
def test_moving_images_are_written_anew_without_metadata(fmt):
    data = _moving(fmt)
    assert b"GEHEIM" in data or b"Kamera-XY" in data
    out = media.strip_metadata(data)
    assert b"GEHEIM" not in out and b"Kamera-XY" not in out
    img = Image.open(io.BytesIO(out))
    assert img.format == fmt and img.n_frames == 3 and img.size == (32, 24)
    assert not img.getexif().get(0x010F)
    times = []
    for n in range(3):
        img.seek(n)
        img.load()
        times.append(img.info.get("duration"))
    assert [round(t) for t in times] == [120, 80, 200]


def _many_frames(side, frames):
    images = [Image.new("L", (side, side), (n * 37) % 256) for n in range(frames)]
    out = io.BytesIO()
    images[0].save(out, format="GIF", save_all=True, append_images=images[1:], duration=50, loop=0)
    return out.getvalue()


def test_animation_with_too_many_pixels_in_all_frames_is_refused():
    """Abgleich 2026-10-15 T03-N1: every frame is held until the image is written anew; 200 megapixels took about
    1 GB from a file of 0.4 MB. Now at most media.ANIMATED_MAX_PIXELS for all frames together."""
    assert media.ANIMATED_MAX_PIXELS <= 25_000_000
    with pytest.raises(ValueError):
        media.strip_metadata(_many_frames(1000, 26))
    assert Image.open(io.BytesIO(media.strip_metadata(_many_frames(200, 20)))).n_frames == 20


def test_import_budget_counts_every_frame(app):
    """Abgleich 2026-10-15 T03-N1: the pixel budget of an import counted only the first frame of a moving image."""
    from app import transfer

    gif = _many_frames(10, 5)  # 100 pixels a frame, 500 in all
    with app.app_context():
        budget = transfer.Budget()
        budget.pixels = 400
        with pytest.raises(transfer.Refused):
            transfer._Files({}, lambda name, most: gif, budget).image("a.gif")
