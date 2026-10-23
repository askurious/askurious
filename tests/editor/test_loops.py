import re
import csv
import io
import zipfile
from collections import Counter

from werkzeug.datastructures import MultiDict

from app import loops
from app.extensions import db
from app.models import Block, Item, Response, Survey
from tests.conftest import CONSENT_OK, add, fill_consent, fill_table

E = "/surveys/1/editor"


def loop_url(client, block_id=2):
    """Create a loop around the block (the editor's "🔁 Schleife") and return its page URL."""
    r = client.post(f"{E}/blocks/{block_id}/loop")
    return r.headers["Location"].split("?")[0]


def loop_id(app, block_id=2):
    with app.app_context():
        return loops.node_of(db.session.get(Block, block_id))["id"]


def setup(client, mode="all", k="2", order="fixed", balanced=True):
    fill_consent(client)
    add(client, "single_choice")                         # 2
    client.post(E + "/items/2", data=MultiDict([
        ("code", "accept"), ("question", "Würden Sie einen {{robot}} {{setting}} nutzen?"), ("answer_mode", "required"),
        ("option_id", "a"), ("option_code", "1"), ("option_label", "ja"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "nein ({{robot}})")]))
    url = loop_url(client)
    fill_table(client, url, robot=["Pflegeroboter", "Serviceroboter"], setting=["zu Hause", "im Heim"])
    with client.application.app_context():
        loop = loops.config(db.session.get(Block, 2))
    form = [("name", "Vignetten"), ("code", "vignette"), ("mode", mode), ("k", k), ("order", order)]
    if balanced:
        form.append(("balanced", "1"))
    for c in loop["columns"]:
        form += [("col_id", c["id"]), ("col_name", c["name"])]
    for r in loop["rows"]:
        form.append(("row_id", r["id"]))
        for c in loop["columns"]:
            form.append((f"cell_{r['id']}_{c['id']}", r["values"][c["id"]]["de"]))
    client.post(url, data=MultiDict(form))
    return url


def test_generate_and_edit_table(app, client, survey):
    url = setup(client)
    with app.app_context():
        loop = loops.config(db.session.get(Block, 2))
        assert [c["name"] for c in loop["columns"]] == ["robot", "setting"] and len(loop["rows"]) == 4
        assert loops.variables(db.session.get(Block, 2), loop["rows"][3]["id"], "de") == {
            "robot": "Serviceroboter", "setting": "im Heim"}
        # english translation of one cell, falls back per cell
        r0, c0 = loop["rows"][0]["id"], loop["columns"][0]["id"]
    client.post(url + "?lang=en", data={f"cell_{r0}_{c0}": "care robot"})
    with app.app_context():
        block = db.session.get(Block, 2)
        assert loops.variables(block, r0, "en") == {"robot": "care robot", "setting": "zu Hause"}
        assert len(loops.config(block)["rows"]) == 4  # translation view keeps the structure
    assert "{{robot}}" in client.get(E + "/items/2").data.decode()  # variable chips
    assert "🔁 Vignetten" in client.get(E + "/").data.decode()
    flow_page = client.get("/surveys/1/flow/").data.decode()
    assert "🔁" in flow_page and "Vignetten" in flow_page and "vignette_order" in flow_page


def test_piping_answers_per_pass_and_export(app, client, survey):
    setup(client)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        uid = db.session.get(Survey, 1).code
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    seen = []
    for n, choice in enumerate(["1", "2", "1", "2"]):
        html = p.get(f"/s/{uid}").data.decode()
        seen.append(html.split('class="p-question"')[1].split("<")[0].split(">")[1])
        assert "nein (" in html and "{{" not in html
        assert "has-error" in p.post(f"/s/{uid}", data={"nav": "next"}).data.decode()  # required per pass
        p.post(f"/s/{uid}", data={"nav": "next", "i2": choice})
    assert seen == ["Würden Sie einen Pflegeroboter zu Hause nutzen?", "Würden Sie einen Pflegeroboter im Heim nutzen?",
                    "Würden Sie einen Serviceroboter zu Hause nutzen?", "Würden Sie einen Serviceroboter im Heim nutzen?"]
    assert "Vielen Dank" in p.get(f"/s/{uid}").data.decode()
    row = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))[0]
    assert [row[f"accept_{n}"] for n in range(1, 5)] == ["1", "2", "1", "2"] and row["vignette_order"] == "1|2|3|4"
    book = client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    assert "1 = robot: Pflegeroboter, setting: zu Hause" in book
    names = [r["variable"] for r in csv.DictReader(io.StringIO(book))]
    assert [c for c in names if c.startswith("accept")] == [f"accept_{n}" for n in range(1, 5)]  # as in data.csv
    sps = next(zipfile.ZipFile(io.BytesIO(client.get("/surveys/1/export/spss.zip").data)).read(n).decode("utf-8-sig")
               for n in zipfile.ZipFile(io.BytesIO(client.get("/surveys/1/export/spss.zip").data)).namelist()
               if n.endswith(".sps"))
    assert "/accept_3 1 'ja'" in sps  # value labels per loop column
    # what _n stands for: the values of that table row, in the variable labels and on the results page
    assert "[Durchgang 3: Serviceroboter · zu Hause]" in book and "Durchgang 3: Serviceroboter · zu Hause" in sps
    results = client.get("/surveys/1/results").data.decode()
    assert "<code>_2</code> Pflegeroboter · im Heim" in results


def test_k_of_n_balanced_random_order(app, client, survey):
    setup(client, mode="k", k="2", order="random")
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        uid = db.session.get(Survey, 1).code
    for _ in range(8):  # a participation is stored (and counts for balancing) with the first submitted page
        q = app.test_client()
        q.get(f"/s/{uid}")
        q.post(f"/s/{uid}", data=CONSENT_OK)
    with app.app_context():
        layouts = [r.layout for r in db.session.scalars(db.select(Response).filter_by(is_preview=False))]
        lid = loops.node_of(db.session.get(Block, 2))["id"]
        shown = Counter(rid for l in layouts for rid in l["loops"][lid])
        assert all(len(l["loops"][lid]) == 2 for l in layouts)
        assert sorted(shown.values()) == [4, 4, 4, 4]  # balanced: every row equally often
        pages = [p for p in layouts[0]["pages"] if p.get("row")]
        assert [p["iter"] for p in pages] == [1, 2] and pages[0]["iters"] == 2
    from app import design

    with app.app_context():
        assert design.stats(db.session.get(Survey, 1))["paths"] == 1  # loops (micro-scenarios) do not count as paths


def test_checks(app, client, survey):
    setup(client)
    add(client, "info", block=2)                          # 3
    client.post(E + "/items/3", data={"code": "x", "question": "{{unknown}}"})
    client.post(E + "/blocks")                            # block 3 without loop
    add(client, "info", block=3)                          # 4
    client.post(E + "/items/4", data={"code": "y", "question": "Hallo {{robot}}"})
    from app import design

    with app.app_context():
        msgs = [f.message for f in design.check(db.session.get(Survey, 1))]
    assert any("unbekannter Platzhalter {{unknown}}" in m for m in msgs)
    assert any("{{robot}} wird nicht ersetzt" in m for m in msgs)


def test_dynamic_image_keys_follow_used_variables(app, client, survey):
    setup(client)
    add(client, "image")                                  # 3
    with app.app_context():
        item = db.session.get(Item, 3)
        item.config = {**item.config, "source": "ai", "prompt": "A {{robot}} in a living room"}
        db.session.commit()
        keys = item.kind.required_keys(item)
        assert len(keys) == 2  # only robot is used: 2 images for 4 rows
        assert any("2 von 2 Bildern fehlen" in p for p in item.kind.validate(item))
    assert "Dynamische Bilder" in client.get(E + "/items/3/images").data.decode()


def test_loop_over_two_blocks_runs_them_together(app, client, survey):
    from app import flow

    setup(client)                                         # loop around block 2 (question "accept")
    client.post(E + "/blocks")                            # block 3
    add(client, "info", block=3)                          # 3
    client.post(E + "/items/3", data={"code": "vign", "question": "Szenario: {{robot}} {{setting}}"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        lid = loops.node_of(db.session.get(Block, 2))["id"]
        flow.place(s, "k3", "k2", "before")              # drag block 3 into the loop, before block 2
        db.session.commit()
        assert [b.id for b in loops.blocks_in(s, loops.node_of(db.session.get(Block, 3)))] == [3, 2]
        from app import runtime
        import random

        layout = runtime.build_layout(s, random.Random(1))
        passes = [(p["block"], p["iter"]) for p in layout["pages"] if p.get("row")]
        assert passes == [(3, 1), (2, 1), (3, 2), (2, 2), (3, 3), (2, 3), (3, 4), (2, 4)]
        assert layout["loops"][lid] and "3" not in layout["loops"]
        from app import design

        assert not [f for f in design.check(s) if "Schleife" in f.message and f.severity == "error"]


def test_loop_constraints(app, client, survey):
    from app import design, flow

    setup(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        lid = loops.node_of(db.session.get(Block, 2))["id"]
        rand = flow.add_node(s, "rand")
        flow.place(s, rand["id"], lid, "into")           # a randomizer inside a loop is not allowed
        db.session.commit()
        assert any("Darf nur Blöcke enthalten" in f.message for f in design.check(s))
        flow.remove(s, rand["id"])
        flow.place(s, "k1", lid, "into")                 # the consent inside a loop ...
        db.session.commit()
    client.get(E + "/")                                   # ... is taken out again when the editor opens
    with app.app_context():
        assert loops.node_of(db.session.get(Block, 1)) is None


def test_loop_variables_in_statements_labels_and_poles(app, client, survey):
    setup(client)                                          # block 2 in a loop with {{robot}} and {{setting}}
    add(client, "likert", block=2)                         # 3
    client.post(E + "/items/3", data=MultiDict([
        ("code", "trust"), ("points", "5"), ("anchors", "custom"), ("labeling", "full"),
        ("stmt_id", "s1"), ("stmt_code", ""), ("stmt_text", "Ich vertraue dem {{robot}}"),
        ("label", "gar nicht ({{robot}})"), ("label", "b"), ("label", "c"), ("label", "d"), ("label", "sehr")]))
    add(client, "semantic", block=2)                       # 4
    client.post(E + "/items/4", data=MultiDict([
        ("code", "feel"), ("points", "5"), ("pair_id", "p1"), ("pair_code", ""),
        ("pair_left", "kalt {{setting}}"), ("pair_right", "warm {{setting}}")]))
    html = client.get(E + "/blocks/2/preview", follow_redirects=True).data.decode()
    assert "{{" not in html.split('<main')[1].split('</main>')[0]            # the page (not the comment panel)
    assert "Ich vertraue dem Pflegeroboter" in html or "Ich vertraue dem Serviceroboter" in html
    assert "gar nicht (Pflegeroboter)" in html or "gar nicht (Serviceroboter)" in html
    assert re.search(r"kalt (zu Hause|im Heim)", html)
