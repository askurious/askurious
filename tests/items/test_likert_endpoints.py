"""„nur Endpunkte verbalisiert“: no wording for the steps in between, neither in the editor nor in what is saved,
exported or offered for review (Fehlerbericht 2026-10-06: a 4-step agreement scale showed all four labels)."""

import re

from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models import Item
from tests.conftest import add

E = "/surveys/1/editor/items/2"
BASE = [("code", "TA"), ("points", "4"), ("anchors", "agree"), ("stmt_id", "s1"), ("stmt_code", ""), ("stmt_text", "A")]
AGREE4 = ["stimme überhaupt nicht zu", "stimme eher nicht zu", "stimme eher zu", "stimme voll und ganz zu"]


def labels(app, lang="de"):
    with app.app_context():
        return db.session.get(Item, 2).texts[lang]["labels"]


def test_editor_suggests_no_wording_for_the_steps_in_between(app, client, survey):
    add(client, "likert")
    client.post(E, data=MultiDict(BASE + [("labeling", "full")]))
    assert labels(app) == AGREE4
    # an item saved before the fix still has the wording in between: the editor shows it empty and locked
    with app.app_context():
        i = db.session.get(Item, 2)
        i.config = {**i.config, "labeling": "endpoints"}
        db.session.commit()
    page = client.get(E).data.decode()
    shown = re.findall(r'<input name="label" aria-label="Beschriftung Stufe \d+" value="([^"]*)"', page)
    assert shown == [AGREE4[0], "", "", AGREE4[-1]]
    assert page.count('placeholder="ohne Beschriftung"') == 2
    assert "richtig: Stufe 2" in page  # the attention check names the step, not its hidden wording


def test_saving_with_ends_only_keeps_the_ends_in_every_language(app, client, survey):
    add(client, "likert")
    client.post(E, data=MultiDict(BASE + [("labeling", "full")]))
    form = [("label", x) for x in ("gar nicht", "", "", "völlig")]
    client.post(E, data=MultiDict(BASE + [("labeling", "endpoints")] + form))
    assert labels(app) == ["gar nicht", "", "", "völlig"]
    assert labels(app, "en") == ["strongly disagree", "", "", "strongly agree"]
    # a new number of steps or answer scale with ends only: the preset gives the ends, nothing in between
    client.post(E, data=MultiDict([(k, "5" if k == "points" else v) for k, v in BASE] + [("labeling", "endpoints")]))
    assert labels(app) == ["stimme überhaupt nicht zu", "", "", "", "stimme voll und ganz zu"]
    with app.app_context():
        i = db.session.get(Item, 2)
        assert i.kind.check_text(i, "de", 2) == "Bitte wählen Sie hier die Stufe 2 von links."


def test_back_to_all_steps_takes_the_wording_of_the_answer_scale(app, client, survey):
    add(client, "likert")
    client.post(E, data=MultiDict(BASE + [("labeling", "endpoints")]))
    assert labels(app) == [AGREE4[0], "", "", AGREE4[-1]]
    # without the script the form sends the empty steps; the server fills them from the answer scale
    form = [("label", x) for x in (AGREE4[0], "", "", AGREE4[-1])]
    client.post(E, data=MultiDict(BASE + [("labeling", "full")] + form))
    assert labels(app) == AGREE4
    # own wording: the steps in between stay empty to be written
    client.post(E, data=MultiDict(BASE + [("labeling", "endpoints")]))
    own = [(k, "custom" if k == "anchors" else v) for k, v in BASE]
    client.post(E, data=MultiDict(own + [("labeling", "full")] + form))
    assert labels(app) == [AGREE4[0], "", "", AGREE4[-1]]


def test_old_wording_in_between_reaches_no_export_or_review(app, client, survey):
    from app import text_review

    add(client, "likert")
    client.post(E, data=MultiDict(BASE + [("labeling", "full")]))
    with app.app_context():
        i = db.session.get(Item, 2)
        i.config = {**i.config, "labeling": "endpoints"}  # stored before the fix: wording in between still there
        db.session.commit()
        i = db.session.get(Item, 2)
        assert i.kind.labels(i, "de") == [AGREE4[0], "", "", AGREE4[-1]]
        assert [p[1] for p in text_review.parts(i) if p[0].startswith("labels/")] == ["Skalenstufe 1", "Skalenstufe 4"]
    codebook = client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    assert "stimme voll und ganz zu" in codebook
    assert "stimme eher nicht zu" not in codebook


def test_construct_with_ends_only_stays_unchanged_after_saving(app, client, survey):
    """BFI-10 keeps "2", "3", "4" in between in its template; saving it from the editor empties them, which is no
    modification (only the ends are shown)."""
    from app import constructs

    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:bfi10"})
    with app.app_context():
        i = db.session.get(Item, 2)
        lt = i.texts["de"]
        i.texts = {**i.texts, "de": {**lt, "labels": [lt["labels"][0], "", "", "", lt["labels"][-1]]}}
        db.session.commit()
        i = db.session.get(Item, 2)
        assert constructs.modifications(i, constructs.get("bfi10")) == []
        # switched to all steps, "2", "3", "4" would be shown: that is a change of the scale labels
        i.config = {**i.config, "labeling": "full"}
        i.texts = {**i.texts, "de": lt}
        assert any("scale labels" in m for m in constructs.modifications(i, constructs.get("bfi10")))


def test_eight_to_ten_steps_get_graded_suggestions(app, client, survey):
    """Ralf, 2026-10-06: with "alle Stufen verbalisiert" 8 to 10 steps had empty steps in between. Every answer
    scale now suggests a label for every step; ends, "plain" and "rather" are the wording of the seven-step scale."""
    from app.items.likert import ANCHOR_CHOICES, FINE, anchor_labels

    for key, _ in ANCHOR_CHOICES:
        for lang in ("de", "en"):
            seven = anchor_labels(lang, 7, key)
            for points in (8, 9, 10):
                found = anchor_labels(lang, points, key)
                assert len(found) == points and all(found) and len(set(found)) == points, (key, lang, points)
                assert found[0] == seven[0] and found[-1] == seven[-1]
            if key in FINE:
                fine = FINE[key][lang]
                assert [fine[i] for i in (0, 2, 3, 5, 7, 8, 10)] == seven, (key, lang)
    assert anchor_labels("de", 9) == ["stimme überhaupt nicht zu", "stimme nicht zu", "stimme eher nicht zu",
                                      "stimme wenig zu", "weder noch", "stimme etwas zu", "stimme eher zu", "stimme zu",
                                      "stimme voll und ganz zu"]
    add(client, "likert")
    client.post(E, data=MultiDict([(k, "9" if k == "points" else v) for k, v in BASE] + [("labeling", "full")]))
    assert labels(app) == anchor_labels("de", 9) and labels(app, "en") == anchor_labels("en", 9)
