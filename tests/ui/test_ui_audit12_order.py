"""UI/usability audit 2026-10-12, package 2: answers that are ranges of numbers are offered as "ordinal" (U22),
part-worths of ordered attributes keep the order of their levels, as the tick "Stufen sind geordnet" says (U23), and
ordered answers side by side do not wrap into a grid on a phone (U37)."""
from app import choice_model, design
from app.models import Item


def test_ranges_of_numbers_read_as_ordered():
    assert design._number_steps(["unter 5 GB", "5 bis 20 GB", "20 bis 50 GB", "mehr als 50 GB"])
    assert design._number_steps(["unter 1.500 €", "1.500 bis unter 2.500 €", "2.500 bis unter 3.500 €",
                                 "5.000 € und mehr"])
    assert design._number_steps(["60 und älter", "40 bis 59", "18 bis 39"])  # falling is an order too
    assert not design._number_steps(["Bus", "Bahn 2", "Rad"])                  # not every answer has a number
    assert not design._number_steps(["5 Äpfel", "2 Birnen", "9 Kiwis"])        # numbers out of order


def test_levels_with_one_open_end_keep_their_order():
    assert choice_model.levels_look_ordered(["5 GB", "20 GB", "50 GB", "unbegrenzt"])
    assert choice_model.levels_look_ordered(["monatlich kündbar", "12 Monate", "24 Monate"])
    assert not choice_model.levels_look_ordered(["rot", "5 GB", "blau"])       # the gap is in the middle
    assert not choice_model.levels_look_ordered(["rot", "grün", "blau"])


def _model(ordered):
    levels = [{"label": label, "beta": beta, "se": 0.1} for label, beta in
              (("rot", -0.5), ("grün", 0.6), ("blau", 0.1))]
    return {"attributes": [{"label": "Farbe", "levels": levels, "ordered": ordered}], "q": 1.96}


def test_the_tick_decides_the_order_of_the_bars():
    assert choice_model.charts(_model(None))[0]["order"] == [1, 2, 0]    # nominal: largest on top
    assert choice_model.charts(_model(True))[0]["order"] == [0, 1, 2]    # ticked: as the levels stand
    assert choice_model.charts(_model(False))[0]["order"] == [1, 2, 0]


def test_the_tick_does_not_count_as_a_new_design(app):
    attrs = [{"id": "a1", "code": "A1", "levels": [{"id": "l1"}, {"id": "l2"}]}]
    before = Item(type="conjoint", config={"attributes": attrs})
    after = Item(type="conjoint", config={"attributes": [{**attrs[0], "ordered": True}]})
    with app.app_context():
        assert before.kind.answer_structure(before) == after.kind.answer_structure(after)


def test_ordered_answers_side_by_side_go_below_each_other_on_a_phone():
    css = open("app/static/participant.css", encoding="utf-8").read()
    assert ".p-options.horizontal.is-ordered { flex-wrap: nowrap; }" in css
    assert "@media (max-width: 600px) { .p-options.horizontal.is-ordered { flex-direction: column; } }" in css
    template = open("app/templates/items/single_choice/participant.html", encoding="utf-8").read()
    assert "is-ordered" in template and "looks_ordered(item)" in template


def test_the_editor_keeps_the_tick_and_starts_from_the_labels(app, client, survey):
    from app.extensions import db
    from tests.analysis.test_cbc_results import setup
    from tests.analysis.test_conjoint import E, conjoint_form

    setup(client)                                       # "Preis": 50 €, 100 €, 150 € (a2) reads as ordered
    html = client.get(E + "/items/2").get_data(as_text=True)
    assert 'name="attr_ordered" value="a2" checked' in html and 'name="attr_ordered_shown"' in html
    form = conjoint_form()
    form.add("attr_ordered_shown", "1")
    form.add("attr_ordered", "a1")                      # the look ticked, the price not
    client.post(E + "/items/2", data=form)
    with app.app_context():
        attrs = {a["id"]: a for a in db.session.get(Item, 2).config["attributes"]}
        assert attrs["a1"]["ordered"] is True and attrs["a2"]["ordered"] is False
