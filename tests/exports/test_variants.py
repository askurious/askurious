"""How versions of a stimulus differ, without a base text (app/variants.py)."""
import itertools

import pytest

from app import variants

GAIN = ("**Meldung**\n\nIn einer Studie wurden mit Unterstützung des Systems 90 von 100 Tumoren früh erkannt. Das sehen "
        "Fachleute als großen Gewinn für die Sicherheit.")
LOSS = ("**Meldung**\n\nIn einer Studie wurden trotz Unterstützung des Systems 10 von 100 Tumoren nicht erkannt. Das sehen "
        "Fachleute als erhebliches Risiko für die Sicherheit.")
CONTROL = "**Meldung**\n\nIn einer Studie wurde das System an Aufnahmen aus mehreren Kliniken erprobt."


@pytest.fixture(autouse=True)
def context(app):
    with app.app_context():
        yield


def _text(tokens):
    return "".join(word for word, _ in tokens)


def _level(segs, level):
    return "".join(_text(tokens) for tokens, _ in variants.flatten(segs, level))


def _shared(segs):
    return [_text(s.tokens) for s in segs if not s.branches]


def test_every_version_comes_back_word_for_word():
    segs = variants.align([GAIN, LOSS, CONTROL])
    for level, text in enumerate((GAIN, LOSS, CONTROL)):
        assert _level(segs, level) == _text(variants.parse(text))


def test_what_all_share_needs_no_base_and_no_order_of_versions():
    texts = (GAIN, LOSS, CONTROL)
    wanted = {" ".join(_shared(variants.align(texts)))}
    for order in itertools.permutations(texts):
        wanted.add(" ".join(_shared(variants.align(order))))
    assert len(wanted) == 1 and "In einer Studie" in wanted.pop()


def test_versions_that_resemble_each_other_part_only_at_the_word_that_differs():
    segs = variants.align([GAIN, LOSS, CONTROL])
    own = [_text(tokens) for tokens, shared in variants.flatten(segs, 0) if shared == 1]
    some = [_text(tokens) for tokens, shared in variants.flatten(segs, 0) if shared == 2]
    assert "mit" in own and "90" in own and "früh" in own
    assert any("Unterstützung des Systems" in part for part in some)  # gain and loss share it, the control does not
    assert all("Unterstützung" not in part for part in own)


def test_a_lone_shared_word_does_not_cut_the_text():
    segs = variants.align(["alpha beta und gamma delta", "epsilon zeta und eta theta"])
    assert all(not s.tokens for s in segs)  # "und" alone is no shared text


def test_the_same_words_in_bold_are_a_different_stimulus():
    segs = variants.align(["Ein ganz **wichtiger** Satz hier steht", "Ein ganz wichtiger Satz hier steht"])
    assert any(s.branches for s in segs) and variants.counts(segs, 2)[1] == [1, 1]


def test_same_versions_count_as_one_and_empty_ones_stay_empty():
    segs = variants.align(["gleich gleich gleich", "gleich gleich gleich"])
    assert len(segs) == 1 and not segs[0].branches and segs[0].levels == (0, 1)
    assert variants.align(["", ""]) == []
    segs = variants.align(["etwas Text steht hier", ""])
    assert _level(segs, 0) == "etwas Text steht hier" and _level(segs, 1) == ""


def test_equal_versions_are_one_branch():
    segs = variants.align(["Vorne steht es gleich. A hier", "Vorne steht es gleich. A hier", "Vorne steht es gleich. B dort drüben"])
    fork = next(s for s in segs if s.branches)
    assert [levels for levels, _ in fork.branches] == [(0, 1), (2,)]


def test_chinese_is_compared_by_character():
    assert len(variants.parse("人工智能")) == 4


def test_very_long_versions_are_compared_as_a_whole(monkeypatch):
    monkeypatch.setattr(variants, "MAX_TOKENS", 5)
    segs = variants.align(["eins zwei drei vier fünf sechs", "eins zwei drei vier fünf sieben"])
    assert len(segs) == 1 and segs[0].branches


A_TEXT = "Kopf steht hier\n\nDie Schlagzeile der Studie lautet groß und klar\n\nDer ganze Absatz steht gleich da. Das System rettet viele Leben im Einsatz. Der Schluss bleibt gleich hier."
B_TEXT = "Kopf steht hier\n\nDie Schlagzeile der Studie lautet klein und klar\n\nDer ganze Absatz steht gleich da. Das System kostet viele Leben im Einsatz. Der Schluss bleibt gleich hier."


def _layout(segs, n):
    return [(kind, _text(content) if kind == "same" else [_text(t for tokens, _ in level for t in tokens) for level in content])
            for kind, content in variants.rows(segs, n)]


def test_a_sentence_with_a_difference_stays_whole_in_every_version():
    layout = _layout(variants.align([A_TEXT, B_TEXT]), 2)
    assert [kind for kind, _ in layout] == ["same", "differ", "same", "differ", "same"]
    assert layout[1][1][0].strip() == "Die Schlagzeile der Studie lautet groß und klar"  # its shared start stays in the row
    assert layout[3][1][1].strip() == "Das System kostet viele Leben im Einsatz."
    assert layout[4][1] == "Der Schluss bleibt gleich hier."


def test_several_differences_in_a_sentence_are_one_row():
    one = "Ein Bild zeigt eine lächelnde Ärztin auf einer hellen Station mit warmem Licht und grüner Anzeige."
    two = "Ein Bild zeigt eine besorgte Ärztin auf einer dunklen Station mit kaltem Licht und roter Anzeige."
    assert [kind for kind, _ in _layout(variants.align([one, two]), 2)] == ["differ"]


def test_rows_lose_no_word():
    texts = (A_TEXT, B_TEXT, "Kopf steht hier\n\nGanz etwas anderes steht hier. Der Schluss bleibt gleich hier.")
    segs = variants.align(texts)
    for level, text in enumerate(texts):
        back = "".join(content if kind == "same" else content[level] for kind, content in _layout(segs, 3))
        assert back == _text(variants.parse(text))


def test_trim_takes_the_space_at_both_ends_only():
    pieces = [(((" ", ()), ("a", ()), (" ", ())), 2), (((" ", ()),), 2), ((("b", ()), (" ", ())), 1)]
    trimmed = variants.trim(pieces)
    assert _text(trimmed[0][0]).startswith("a") and _text(trimmed[-1][0]).endswith("b")
