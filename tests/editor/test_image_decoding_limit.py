"""Uploaded and imported images are decoded at most media.DECODES_AT_ONCE at a time per process, and one import may
hold 300 megapixels instead of a gigapixel (audit 2026-10-13, DA-3): a WebP of 2 KB with 7000 x 7000 px took 3.4 s and
~190 MB, and parallel uploads added up without a bound."""
import io
import threading

import pytest

from app import media, transfer


def _png(w=20, h=10):
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (w, h), (200, 30, 30)).save(out, "PNG")
    return out.getvalue()


def test_decoding_waits_for_a_place_and_then_refuses(app, monkeypatch):
    monkeypatch.setattr(media, "DECODE_WAIT", 0.05)
    taken = [media._decoding.acquire(timeout=1) for _ in range(media.DECODES_AT_ONCE)]
    try:
        assert all(taken)
        with app.test_request_context():
            for call in (lambda: media.strip_metadata(_png()), lambda: media.fit_still(_png(), 10),
                         lambda: media.optimize(_png())):
                with pytest.raises(media.Busy):
                    call()
    finally:
        for _ in taken:
            media._decoding.release()


def test_one_place_is_enough_for_nested_decoding(app):
    """fit_still and save_logo call strip_metadata: the thread that holds a place does not wait for a second one."""
    media._decoding.acquire()
    try:
        done = []
        worker = threading.Thread(target=lambda: done.append(media.fit_still(_png(40, 20), 10)))
        with app.test_request_context():
            worker.start()
            worker.join(5)
        assert done and done[0]
    finally:
        media._decoding.release()


def test_import_pixel_budget_is_lower():
    assert transfer.MAX_PIXELS == 300_000_000
