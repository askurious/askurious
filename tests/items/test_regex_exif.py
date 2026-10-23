import io
import re
import time

from app.extensions import db
from app.items.text import MAX_REGEX_INPUT, risky_pattern
from app.models import Item
from tests.conftest import add

E = "/surveys/1/editor"


def test_risky_patterns_are_recognised():
    for pattern in (r"(a+)+$", r"(\w*)*", r"([a-z]+\d*)+x", r"(?:a|b+)*c", r"(a{1,200}){1,200}", r"((ab)+)+"):
        assert risky_pattern(pattern), pattern
    for pattern in (r"[A-Z]{2}\d{4}", r"\d+", r"[a-z]+-[a-z]+", r"(ab|cd)+", r"(\d{2}\.){2}\d{4}", r"\w+@\w+\.\w+",
                    r"(a{1,3}){1,3}", "(broken"):
        assert not risky_pattern(pattern), pattern


def test_risky_pattern_is_checked_in_linear_time(app, client, survey):
    """With RE2 (audit 2026-10-13, KR-1) a pattern that backtracks in Python's `re` is applied, in linear time."""
    import pytest

    from app.items.base import AnswerError

    add(client, "text_input")
    client.post(E + "/items/2", data={"code": "code", "validation": "regex", "pattern": "(a+)+$", "max_length": "5000"})
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.config["max_length"] == MAX_REGEX_INPUT                     # own patterns check short inputs
        assert not item.kind.validate(item)
        started = time.process_time()
        with pytest.raises(AnswerError):                                         # would take minutes with `re`
            item.kind.parse_answer(item, {"i2": "a" * 40 + "!"}, "de")
        assert item.kind.parse_answer(item, {"i2": "aaa"}, "de") == "aaa"
        assert time.process_time() - started < 1


def test_pattern_the_probes_miss_cannot_block_a_worker():
    """Audit 2026-10-13, KR-1: `^(abc|ab|c)*d$` passed the structural check and the probes, and "abc"*n + "x"
    doubled the time of `re` with every 3 characters (76 characters: 3.7 s, 500 allowed)."""
    from app.items.text import pattern_matches, pattern_problem

    pattern = r"^(abc|ab|c)*d$"
    assert pattern_problem(pattern) is None
    started = time.process_time()
    assert pattern_matches(pattern, "abc" * 166 + "x") is False
    assert pattern_matches(pattern, "abcabd") is True
    assert time.process_time() - started < 0.5


def test_pattern_re2_cannot_read_is_reported_and_not_applied(app, client, survey):
    """Audit 2026-10-13, KR-1: back-references and lookaround have no linear-time check; the pre-launch check names them
    and the answer passes instead of being matched with the backtracking `re`."""
    add(client, "text_input")
    client.post(E + "/items/2", data={"code": "code", "validation": "regex", "pattern": r"(?=\d)(\w)\1"})
    with app.app_context():
        item = db.session.get(Item, 2)
        assert any("nicht geprüft werden kann" in p for p in item.kind.validate(item))
        assert item.kind.parse_answer(item, {"i2": "xy"}, "de") == "xy"


def test_word_digit_and_space_classes_keep_reading_every_script():
    r"""RE2 reads \w \d \s as ASCII; the translation keeps Python's meaning (audit 2026-10-13, KR-1)."""
    from app.items.text import pattern_matches

    assert pattern_matches(r"\w+", "Müller") is True
    assert pattern_matches(r"[\w-]+", "Jean-Luc") is True
    assert pattern_matches(r"\d{2}", "١٢") is True
    assert pattern_matches(r"a\sb", "a b") is True
    assert pattern_matches(r"\W", "ä") is False
    assert pattern_matches(r"\\w", "\\w") is True                           # an escaped backslash stays one
    assert pattern_matches(r"ab\Z", "ab") is True


def test_safe_pattern_still_checks_and_long_input_is_refused(app, client, survey):
    import pytest

    from app.items.base import AnswerError

    add(client, "text_input")
    client.post(E + "/items/2", data={"code": "code", "validation": "regex", "pattern": r"[A-Z]{2}\d{4}"})
    with app.app_context():
        item = db.session.get(Item, 2)
        assert not item.kind.validate(item)
        assert item.kind.parse_answer(item, {"i2": "AB1234"}, "de") == "AB1234"
        with pytest.raises(AnswerError):
            item.kind.parse_answer(item, {"i2": "ab1234"}, "de")
        item.config = {**item.config, "max_length": 20000}                       # an item saved before the limit
        with pytest.raises(AnswerError):
            item.kind.parse_answer(item, {"i2": "A" * (MAX_REGEX_INPUT + 1)}, "de")


def _photo(fmt):
    from PIL import Image

    img = Image.new("RGB", (40, 20), (200, 30, 30))
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"          # Make
    exif[0x0112] = 6                     # Orientation: rotate 90° when shown
    exif[0x8825] = {1: "N", 2: (50.0, 46.0, 30.0), 3: "E", 4: (6.0, 5.0, 0.0)}   # GPS position
    out = io.BytesIO()
    img.save(out, format=fmt, exif=exif)
    return out.getvalue()


def test_uploads_lose_exif_and_keep_their_look(app, client, survey):
    from PIL import Image

    add(client, "image")
    for fmt, mime in (("JPEG", "image/jpeg"), ("PNG", "image/png"), ("WEBP", "image/webp")):
        data = _photo(fmt)
        assert Image.open(io.BytesIO(data)).getexif().get(0x010F) == "PhoneMaker"
        client.post(E + "/items/2/upload", data={"file": (io.BytesIO(data), "x")}, content_type="multipart/form-data")
        page = client.get(E + "/blocks/2/preview", follow_redirects=True).data.decode()
        r = client.get(re.search(r'/media/[^"]+', page).group(0))
        body = r.data
        r.close()                                                                # Windows: frees the file for the next upload
        stored = Image.open(io.BytesIO(body))
        assert stored.format == "WEBP" and r.mimetype == "image/webp"
        assert not dict(stored.getexif()) and b"PhoneMaker" not in body
        assert stored.size == (20, 40)                                           # the EXIF rotation was applied


def test_unreadable_image_is_refused_and_gif_stays_a_gif(app, client, survey):
    from PIL import Image

    from app import media

    add(client, "image")
    r = client.post(E + "/items/2/upload", data={"file": (io.BytesIO(b"\x89PNG\r\n\x1a\nnot an image"), "x.png")},
                    content_type="multipart/form-data", follow_redirects=True)
    assert "konnte nicht gelesen werden" in r.data.decode()
    out = io.BytesIO()
    Image.new("P", (4, 4)).save(out, format="GIF")
    kept = Image.open(io.BytesIO(media.strip_metadata(out.getvalue())))  # written anew, no longer as it came (DA-2)
    assert kept.format == "GIF" and kept.size == (4, 4)
