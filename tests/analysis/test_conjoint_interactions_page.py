"""The block "Wechselwirkungen zwischen Merkmalen" of the CBC results page (app/conjoint_interactions.py,
surveys/_conjoint_ww.html, 2026-11-24): start, stored search, table and dialog, CSV, taking a confirmed pair into the HB
model (the HB estimate is no longer current), the re-estimated HB with the interaction on the page, in the export, in
the simulator and in the report."""
import csv
import io
import math
import random

import numpy as np

from app.extensions import db
from app.models import Item, Response, Survey, new_pid
from tests.analysis.test_cbc_results import PRICES, setup

HB_FAST = {"HB_SYNC": True, "HB_BURN": 300, "HB_DRAWS": 400, "HB_THIN": 2}
LOOK = [0.6, 0.0, -0.6]
PRIVACY = [0.5, -0.5]
WW = [[1.2, -1.2], [0.0, 0.0], [-1.2, 1.2]]   # look x privacy, rows and columns sum to 0


def simulate(app, n, seed=3):
    rng = random.Random(seed)
    with app.app_context():
        s = db.session.get(Survey, 1)
        item = db.session.get(Item, 2)
        kind, c = item.kind, item.config
        codes = [a["code"] for a in c["attributes"]]

        def utility(profile):
            lv = dict(zip(codes, profile))
            return (LOOK[lv["look"]] + PRIVACY[lv["privacy"]] + WW[lv["look"]][lv["privacy"]]
                    - 0.015 * (PRICES[lv["price"]] - PRICES[1]))

        for _ in range(n):
            v = rng.randrange(len(c["design"]["versions"]))
            answer = {}
            for t in range(c["tasks"]):
                u = [utility(kind.profile(item, v, t, a)) - math.log(-math.log(rng.random()))
                     for a in range(c["alternatives"])]
                answer[f"t{t}"] = max(range(len(u)), key=u.__getitem__) + 1
            s.responses.append(Response(answers={"2": answer}, layout={"cj": {"2": {"version": v, "order": []}}},
                                        status=Response.COMPLETED, pid=new_pid(s.code)))
        db.session.commit()


def test_search_confirm_take_over_and_use_everywhere(app, client, survey):
    from app import choice_predict, conjoint_hb, conjoint_interactions as ww

    setup(client, price_attr=True, tasks=10, versions=6)
    app.config.update(HB_FAST)
    simulate(app, 160)
    html = client.get("/surveys/1/results").data.decode()
    assert "Wechselwirkungen zwischen Merkmalen" in html and "Suche starten" in html
    assert 'class="res-dist res-choice-fold ww-part" id="ww-2" data-ww-item="2">' in html     # closed: nothing yet
    client.post("/surveys/1/results/hb/2", data={"source": "real"})
    r = client.post("/surveys/1/results/ww/2", data={"source": "real", "splits": "3"})
    assert r.status_code == 302 and r.location.endswith("#ww-2")
    with app.app_context():
        item = db.session.get(Item, 2)
        stored = ww.load(item)
        assert stored and stored["with_hb"] and stored["confirm"] == {"mode": "split", "splits": 3, "needed": 2}
        attrs = [a["code"] for a in item.config["attributes"]]
        top = stored["rows"][0]
        seen = [(attrs[r["a"]], attrs[r["b"]], r["status"], r["p_holm"], r["simple"].get("p"), (r["hb"] or {}).get("p"),
                 r.get("cells"), (r.get("confirm") or {}).get("delta")) for r in stored["rows"]]
        assert {attrs[top["a"]], attrs[top["b"]]} == {"look", "privacy"} and top["status"] == "confirmed", seen
        # a pair sharing an attribute with the strong true interaction may be flagged too: with only 6 versions its
        # interaction columns correlate with the true ones in the design (a limit of the search pair by pair)
        ids = [a["id"] for a in item.config["attributes"]]
        assert "pids" not in stored and isinstance(stored["persons"], int)  # no values per person
    html = client.get("/surveys/1/results").data.decode()
    block = html.split('id="ww-2"', 1)[1].split("</details>", 1)[0]
    assert " open>" in block[:80] and "bestätigt" in block and "Suche neu starten" in block
    assert block.count("data-ww-row") == 3 and 'data-dialog-open="ww-dlg-2-' in block
    assert "Ins HB-Modell übernehmen" in html and "Wie oft gezeigt" in html
    # a stored search with random divisions (as before 2026-11-24) still shows as before, now with the reading aids,
    # the scale of the combination table and the suggested wording with its copy button
    assert "2 von 3 besser" in block or "3 von 3 besser" in block or "1 von 3 besser" in block
    assert "Formulierungsvorschlag" in html and "data-ww-copy=" in html and "Skala: HB-Teilnutzen ×" in html
    assert "zeigte sich eine Wechselwirkung (χ²(" in html and "Aufteilungen, Δ LL = +" in html
    from app.models import User

    with app.app_context():                                   # the "?" of the learning aids, switched on on "Konto"
        db.session.scalar(db.select(User).filter_by(orcid="0000-0000-0000-001X")).learning_aids = True
        db.session.commit()
    aided = client.get("/surveys/1/results").data.decode()
    for key in ("ww_cell", "ww_comb", "ww_shown", "ww_wald", "ww_ll", "ww_suggest", "ww_confirm"):
        assert f'#{key}"' in aided, key
    # the select: every task once is the default, the random divisions name their source
    select = block.split("data-ww-splits>", 1)[1].split("</select>", 1)[0]
    assert select.index('value="0"') < select.index('value="3"') < select.index('value="5"')
    assert "jede Aufgabe einmal (Standard; Stone, 1974)" in select and "3 zufällige Aufteilungen (schnell; Picard" in select
    # the methods text carries the sentence per flagged pair and its sources
    from app import methods

    with app.app_context():
        item = db.session.get(Item, 2)
        w = methods._interactions(item, None)
        assert w["say_de"] and any("zeigte sich eine Wechselwirkung" in s for s in w["say_de"])
        assert "zeigte sich eine Wechselwirkung" in methods._ww_de({"ww": w})
        assert "results: An interaction between" in methods._ww_en({"ww": w})
        # a search with "jede Aufgabe einmal" shows the paired test over persons instead of the divisions
        from app import maxdiff_hb

        tasks = dict(stored, confirm={"mode": "tasks", "splits": 10, "needed": None, "grouped": False})
        tasks["rows"] = [dict(r, confirm=(dict(r["confirm"], mode="tasks", grouped=False, needed=None,
                                               delta=[0.4] * 10, mean=0.052, hit_all_with=0.61, hit_all_without=0.58,
                                               persons={"n": 160, "mean": 0.052, "sd": 0.2, "t": 3.29, "df": 159,
                                                        "p": 0.0006})
                                          if r.get("confirm") else None)) for r in stored["rows"]]
        maxdiff_hb._write(ww._paths(item)[0], tasks)
        w = methods._interactions(item, None)
        assert "Kreuzvalidierung über alle 10 Aufgaben" in methods._ww_de({"ww": w})
    html = client.get("/surveys/1/results").data.decode()
    assert "Kreuzvalidierung über alle 10 Aufgaben (jede Aufgabe einmal zurückgehalten): Δ LL = 0,05 je Person" in html
    assert "t(159) = 3,29, p &lt; ,001" in html and "einseitiger t-Test über die Personen" in html
    with app.app_context():
        maxdiff_hb._write(ww._paths(db.session.get(Item, 2))[0], stored)
    # the search table as CSV
    rows = list(csv.reader(io.StringIO(client.get("/surveys/1/results/ww/2.csv").data.decode("utf-8-sig")), delimiter=";"))
    assert rows[0][0] == "Merkmalspaar" and len(rows) == 4 and rows[1][-1] == "bestätigt"
    # a pair that is not confirmed cannot be taken over
    other = next((r for r in stored["rows"] if r["status"] != "confirmed"), None)
    if other is not None:
        client.post("/surveys/1/results/ww/2/adopt", data={"a": ids[other["a"]], "b": ids[other["b"]], "take": "1"})
        with app.app_context():
            assert not db.session.get(Item, 2).config.get(ww.CONFIG_KEY)
    a_id, b_id = ids[top["a"]], ids[top["b"]]
    r = client.post("/surveys/1/results/ww/2/adopt", data={"a": a_id, "b": b_id, "take": "1", "source": "real"})
    assert r.status_code == 302 and r.location.endswith("#hb-2")
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.config[ww.CONFIG_KEY] == [[a_id, b_id]]                  # attribute ids, never labels
        hb_table = conjoint_hb.maxdiff_hb.pool_table(item.survey, {"sources": ["real"], "drop": True})
        assert conjoint_hb.view(item, hb_table, "de")["state"] == "stale"   # the HB estimate is no longer current
        assert ww.view(item, hb_table, None, "de")["state"] == "current"     # the search stays
    client.post("/surveys/1/results/hb/2", data={"source": "real"})
    with app.app_context():
        item = db.session.get(Item, 2)
        res = conjoint_hb.load(item)
        inter = res["interactions"][0]
        assert (inter["a"], inter["b"]) == (top["a"], top["b"]) and len(inter["cells"]) == 3
        cells = np.array(inter["cells"])
        assert cells[0][0] > 0.5 and cells[2][0] < -0.5                   # look 1 with privacy 1 up, look 3 down
        assert np.allclose(cells.sum(axis=0), 0, atol=1e-3) and np.allclose(cells.sum(axis=1), 0, atol=1e-3)
        assert inter["cols"] == 2 and len(inter["mean"]) == 2 and res["ww_draws"]
        # the simulator adds the interaction to the concept utilities
        params = conjoint_hb.draw_parameters(res)
        assert params["ww"][0]["kind"] == "levels"
        spans = res["spans"]
        U = choice_predict.draw_utilities(params, spans, [[0, 1, 0], [2, 1, 0]])
        V = choice_predict.draw_utilities({**params, "ww": []}, spans, [[0, 1, 0], [2, 1, 0]])
        diff = (U - V).mean(axis=(0, 1))
        assert diff[0] > 0.5 and diff[1] < -0.5
    html = client.get("/surveys/1/results").data.decode()
    assert 'class="res-table ww-cells hb-ww"' in html and "Die Wichtigkeiten oben beruhen nur auf den Haupteffekten" in html
    assert "im HB-Modell" in html and ">Entfernen<" in html
    # the export: one column per combination, the same for everyone, with its codebook entry
    data = client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig")
    head = next(csv.reader(io.StringIO(data), delimiter=";" if ";" in data.splitlines()[0] else ","))
    columns = [c for c in head if c.startswith("cbc_hb_ww_")]
    assert len(columns) == 6 and "cbc_hb_ww_look_privacy_1_1" in columns
    from pypdf import PdfReader

    body = client.get("/surveys/1/export/ergebnisbericht.pdf").data
    pdf = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(body)).pages)
    assert "Wechselwirkungen zwischen Merkmalen" in pdf and "Wechselwirkung" in pdf and "Holm" in pdf
    # removing the pair takes it out again
    client.post("/surveys/1/results/ww/2/adopt", data={"a": a_id, "b": b_id, "take": "0"})
    with app.app_context():
        assert ww.CONFIG_KEY not in db.session.get(Item, 2).config
