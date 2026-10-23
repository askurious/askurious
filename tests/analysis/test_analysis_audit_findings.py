"""Analysis: the tests of the earlier audit and review files about this area (sorted by area,
architecture concept 2026-10-12, G). Each block names the file it came from; its helpers are in
tests/helpers/audit_<file>.py."""
from tests.helpers.audit_audit_2026_10_06_d import _no_failures_from_other_tests  # noqa: F401
from tests.helpers.audit_audit13_rest import Survey, add, db, np  # noqa: F401
from tests.helpers.audit_audit_2026_10_06_f import FETCH, Item, WAIT, _as_the_script_does, _body, _likert, _pool, _quiet, _scale_rows, draws, export, maxdiff_hb, number, os, psm_analysis, results, results_store, simulator  # noqa: F401
from tests.helpers.audit_audit_2026_10_06_g import subprocess, sys, time  # noqa: F401
from tests.helpers.audit_audit_2026_10_12_a import MultiDict, Response, _answers, _scale, _subscales, random, sem_model, vp  # noqa: F401
from tests.helpers.audit_audit_2026_10_12_c import SimpleNamespace, _new, diary  # noqa: F401
from tests.helpers.audit_audit_2026_10_12_h import _groups_survey, math, sem, study_plan  # noqa: F401
from tests.helpers.audit_audit_rest import E as E__audit_rest, kind_of, panel, psm, pytest, text  # noqa: F401
from tests.helpers.audit_cr8_a import _loops, _vignette_survey, app, client, flow, survey, vignettes  # noqa: F401
from tests.helpers.audit_cr8_d import _dead_pid, _job_file, _stale_marker, _ticks_before, compute, datetime, estimate, jobs, json, paths, threading, timezone  # noqa: F401
from tests.helpers.audit_cr8_e import _count_rows, _hypothesis  # noqa: F401
from tests.helpers.audit_cr8_g import _js  # noqa: F401
from tests.helpers.audit_cr8_j import attributes, inspect  # noqa: F401
from tests.helpers.audit_review_medium import E as E__review_medium, boards  # noqa: F401


# --- from tests/audits/test_audit13_rest.py: Audit Daten und Berechnungen 2026-10-12, package I (exports, choice models, elements, rest): E-3, E-4, E-5, E-6, ---


def test_w2_maxdiff_separated_per_segment():
    from app import maxdiff_analysis as ma
    from app import segments

    versions = [[[0, 1, 2, 3], [0, 1, 2, 3], [0, 1, 2, 3]]]
    est, col, k, thr = ma.parameters(4, versions, [(0, {0: (0, 2)})], False)
    tasks = {0: (0, 2), 1: (0, 2), 2: (0, 2)}  # 0 always best, 2 always worst, 1 and 3 never chosen
    person_sets = [ma.task_sets(versions, 0, tasks, col, k)]
    inp = {"col": {str(i): c for i, c in col.items()}, "est": est, "thr": thr}
    assert segments._separated(person_sets, np.array([0]), 1, None, inp)[0] == [0, 2]
    # the reference statement (no column) always least important: separated too
    tasks = {0: (0, 3), 1: (0, 3), 2: (0, 3)}
    person_sets = [ma.task_sets(versions, 0, tasks, col, k)]
    assert segments._separated(person_sets, np.array([0]), 1, None, inp)[0] == [0, 3]


def test_w4_cbc_constant_note_names_the_price_levels():
    from app import conjoint_hb

    src = open(conjoint_hb.__file__, encoding="utf-8").read()
    assert "zum mittleren Preis der Preisstufen" in src


def test_m10_psm_consistency_weak_and_labels_in_survey_language(app, client, survey):
    from app import export

    add(client, "psm")
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        item = [i for b in s.blocks for i in b.items if i.type == "psm"][0]
        rows = {r[0]: r for r in export.codebook(s)}
        assert any("TC ≤ C ≤ E ≤ TE" in (r[4] or "") for r in rows.values())
        s.default_language = "en"
        _, _, labels = item.kind.result_column(item, f"{item.code}_nms_c")
        assert labels == {str(n): lab for n, lab in enumerate(item.kind.nms_labels(item, "en"), start=1)}


# --- from tests/audits/test_audit_2026_10_06_d.py: Security audit 2026-10-06, package D "Konten": the right to create surveys of invited people (S11), blocking ends ---


def test_the_back_of_the_study_plan_stays_on_this_site(app, client, survey):
    add(client, "text_input")
    client.post("/surveys/1/editor/items/2", data={"code": "minuten", "question": "Wie viele Minuten?"})
    for back in ("/\\evil.example", "//evil.example", "https://evil.example/"):
        r = client.post("/surveys/1/researchdesign/assign", data={"code": "minuten", "back": back})
        assert r.status_code == 302 and "evil" not in r.headers["Location"], back
    r = client.post("/surveys/1/researchdesign/assign", data={"code": "minuten", "back": "/surveys/1/editor/"})
    assert r.headers["Location"].endswith("/surveys/1/editor/")


# --- from tests/audits/test_audit_2026_10_06_f.py: Audit 2026-10-06 (security and performance), package F: performance of the evaluation pages. ---


def test_p1_results_page_draws_no_omega_and_the_part_brings_the_same_intervals(app, client, survey, draws):
    _likert(app, client, _scale_rows())
    _quiet(client)
    first = client.get("/surveys/1/results").data.decode()
    assert not draws and WAIT in first and "omega.js" in first
    assert 'data-omega-slot="s:2:"' in first and 'data-omega-slot="o:TA:"' in first
    # the source of the interval is named from the start (it is there once the part came)
    assert "Perzentil-Bootstrap mit 200 Ziehungen" in first
    part = client.get("/surveys/1/results?omega_part=1", headers=FETCH)
    assert part.status_code == 200 and part.headers["Cache-Control"] == "no-store"
    assert len(draws) == 1                                                 # one scale, drawn once
    filled = _as_the_script_does(first, part.data.decode())
    again = client.get("/surveys/1/results").data.decode()
    assert WAIT not in again and len(draws) == 1                           # kept: not drawn again
    # what the script puts in place is exactly what the page shows once the intervals are kept
    assert _body(filled) == _body(again)
    # the numbers of the bootstrap as compute() drew them before (same cases, same seed)
    with app.app_context():
        inline = results.compute(db.session.get(Survey, 1), ("real",), False, True, omega_ci=True)
        scale = next(e for e in inline["items"] if e["item"].code == "TA")["scale"]
        kept = results_store.load(db.session.get(Survey, 1), results_store.fingerprint(
            db.session.get(Survey, 1), {**_pool(app), "drop": True, "speed_real": True, "weighted": False}),
            {"weighted": False}, name="omega")
    assert scale["omega_ci"] is not None and kept[0]["2:"]["ci"] == scale["omega_ci"]
    lo, hi = scale["omega_ci"]
    assert f"[{number(lo, 2)}; {number(hi, 2)}]" in again
    # a second part request takes the kept intervals
    client.get("/surveys/1/results?omega_part=1", headers=FETCH)
    assert len(draws) == 2                                                 # the inline computation above, not the part


def test_p1_draws_without_an_omega_keep_their_note(app, client, survey):
    _likert(app, client, _scale_rows(n=80, k=3, seed=11, weak=True))
    _quiet(client)
    first = client.get("/surveys/1/results").data.decode()
    part = client.get("/surveys/1/results?omega_part=1", headers=FETCH).data.decode()
    again = client.get("/surveys/1/results").data.decode()
    assert _body(_as_the_script_does(first, part)) == _body(again)
    with app.app_context():
        scale = next(e for e in results.compute(db.session.get(Survey, 1), ("real",), False, True, omega_ci=True)["items"]
                     if e["item"].code == "TA")["scale"]
    # the share of failed draws next to the scale, the open lower end and the sentence under the overview
    assert scale["omega_failed"] and scale["omega_ci"][0] is None
    assert "der Bootstrap-Ziehungen ohne schätzbares ω" in again and "Untergrenze nach unten offen" in again
    assert "zählen als kleinster Wert" in again and "zählen als kleinster Wert" not in first


def test_p1_word_table_of_the_scales_draws_what_is_not_kept(app, client, survey, draws):
    _likert(app, client, _scale_rows())
    client.get("/surveys/1/results")
    assert not draws
    r = client.get("/surveys/1/results/scales.docx")
    assert r.status_code == 200 and len(draws) == 1
    import io

    from docx import Document

    text = "\n".join(c.text for t in Document(io.BytesIO(r.data)).tables for row in t.rows for c in row.cells)
    with app.app_context():
        scale = next(e for e in results.compute(db.session.get(Survey, 1), ("real",), False, True, omega_ci=True)["items"]
                     if e["item"].code == "TA")["scale"]
    hi = number(scale["omega_ci"][1], 2)
    assert hi in text or hi[1:] in text or hi.replace(",", ".")[1:] in text  # APA: without the leading zero
    # kept for the page: the results page has them at once
    assert WAIT not in client.get("/surveys/1/results").data.decode() and len(draws) == 2


def test_p1_kept_files_are_removed_with_the_results(app, client, survey):
    _likert(app, client, _scale_rows())
    client.get("/surveys/1/results")
    client.get("/surveys/1/results?omega_part=1", headers=FETCH)
    with app.app_context():
        s = db.session.get(Survey, 1)
        folder = os.path.join(app.config["UPLOAD_FOLDER"], "hb", s.uid)
        assert {"results.bin", "omega.bin"} <= set(os.listdir(folder))
        assert {"omega.bin", "omega-weighted.bin", "sim.bin", "hypotheses.bin"} <= set(results_store.KEPT)
        results_store.forget(s)
        assert not [n for n in os.listdir(folder) if n.endswith(".bin")]


def test_p6_stored_hb_result_and_pool_table_read_once(app, client, survey, monkeypatch):
    _likert(app, client, _scale_rows(n=12))
    reads, tables = [], []
    read = maxdiff_hb._read
    monkeypatch.setattr(maxdiff_hb, "_read", lambda path: reads.append(path) or read(path))
    rows = export.rows
    monkeypatch.setattr(export, "rows", lambda *a, **k: tables.append(1) or rows(*a, **k))
    with app.test_request_context():
        item = db.session.get(Item, 2)
        path = maxdiff_hb._paths(item)[0]
        os.makedirs(os.path.dirname(path), exist_ok=True)
        maxdiff_hb._write(path, {"key": "a"})
        assert maxdiff_hb.load(item) == {"key": "a"} and maxdiff_hb.load(item) == {"key": "a"} and len(reads) == 1
        maxdiff_hb._write(path, {"key": "bb"})                               # written anew (HB_SYNC): read anew
        assert maxdiff_hb.load(item) == {"key": "bb"} and len(reads) == 2
        pool = {"sources": ["real"], "all": False, "speeder": 50, "drop": True}
        first = maxdiff_hb.pool_table(item.survey, pool)
        assert maxdiff_hb.pool_table(item.survey, dict(pool)) is first and len(tables) == 1
        maxdiff_hb.pool_table(item.survey, {**pool, "drop": False})
        assert len(tables) == 2
    with app.app_context():                                                 # outside a request: no memo
        item = db.session.get(Item, 2)
        maxdiff_hb.load(item), maxdiff_hb.load(item)
        assert len(reads) == 4


def test_p6_simulator_opening_scenario_kept_with_the_results(app, client, survey, monkeypatch):
    from tests.analysis.test_simulator import _hb, _shares

    _hb(app, client)
    _quiet(client)
    first = client.get("/surveys/1/results").data.decode()
    computed = []
    view = simulator.view
    monkeypatch.setattr(simulator, "view", lambda *a, **k: computed.append(1) or view(*a, **k))
    again = client.get("/surveys/1/results").data.decode()
    assert not computed and _shares(again) == _shares(first) and _body(again) == _body(first)
    # a scenario of its own in the address is computed
    client.get("/surveys/1/results?sim=2&sim_n=2&sim_0_0=0&sim_0_2=0&sim_0_price=75&sim_1_0=1&sim_1_2=0&sim_1_price=50")
    assert len(computed) == 1
    # a saved scenario changes what the page opens with: computed anew, then kept again
    client.post("/surveys/1/results/sim/2/save", data={"sim": "2", "sim_name": "Zwei", "sim_n": "2", "sim_0_0": "0",
                                                       "sim_0_2": "0", "sim_0_price": "60", "sim_1_0": "1", "sim_1_2": "0",
                                                       "sim_1_price": "50"})
    saved = client.get("/surveys/1/results").data.decode()
    assert len(computed) == 2
    client.get("/surveys/1/results")
    assert len(computed) == 2 and "Zwei" in saved


def test_p9_psm_curves_show_at_most_300_prices(app, client, survey):
    from tests.analysis.test_psm import setup

    setup(client, nms="1")
    rng = np.random.default_rng(4)
    table = []
    for _ in range(500):
        tc = round(float(rng.uniform(5, 40)), 2)
        c = round(tc + float(rng.uniform(0, 30)), 2)
        e = round(c + float(rng.uniform(0, 30)), 2)
        te = round(e + float(rng.uniform(0, 30)), 2)
        table.append({"wtp_tc": tc, "wtp_c": c, "wtp_e": e, "wtp_te": te, "wtp_nms_c": int(rng.integers(1, 6)),
                      "wtp_nms_e": int(rng.integers(1, 6))})
    with app.app_context():
        item = db.session.get(Item, 2)
        a = results.psm_curves(table, item)
        full = psm_analysis.analyse(table, item)
    assert len(full["curves"]["prices"]) > 1000
    assert a["points"] == full["points"] and a["nms"]["max_revenue"] == full["nms"]["max_revenue"]
    assert len(a["table_rows"]) == psm_analysis.MAX_POINTS and a["table_rows"][0] == 0
    assert a["table_rows"][-1] == len(full["curves"]["prices"]) - 1
    for chart in (a["chart"], a["trial_chart"], a["revenue_chart"]):
        assert len(chart["data"]) <= psm_analysis.MAX_POINTS
        assert all(len(line["points"].split()) <= psm_analysis.MAX_POINTS for line in chart["lines"])
    assert psm_analysis.thin(range(10)) == list(range(10))
    assert psm_analysis.thin(range(1000), 4) == [0, 333, 666, 999]


# --- from tests/audits/test_audit_2026_10_06_g.py: Audit 2026-10-06, package G: performance of the database and of taking part. P3 (index for the counts of ---


def test_the_places_of_estimations_count_over_processes(app, tmp_path):
    app.config["HB_PARALLEL"] = 1
    slot = maxdiff_hb._take_slot(app)
    assert slot is not None and maxdiff_hb._take_slot(app) is None                     # also a second thread
    maxdiff_hb._free_slot(slot)
    lock = os.path.join(maxdiff_hb._slot_folder(app), "0.lock")
    holder = subprocess.Popen([sys.executable, "-c", (
        "import sys, time; sys.path.insert(0, sys.argv[2]); from app import maxdiff_hb; "
        "f = open(sys.argv[1], 'a+b'); assert maxdiff_hb._lock_file(f); print('held', flush=True); time.sleep(30)"),
        lock, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        assert maxdiff_hb._take_slot(app) is None                                       # another server process has it
    finally:
        holder.kill()
        holder.wait(10)
    for _ in range(50):  # Windows lets go of the dead process's lock a moment later
        slot = maxdiff_hb._take_slot(app)
        if slot is not None:
            break
        time.sleep(0.1)
    assert slot is not None                                                             # a dead process gives it back
    maxdiff_hb._free_slot(slot)


# --- from tests/audits/test_audit_2026_10_12_a.py: Audit Daten und Berechnungen 2026-10-12, package A (crashes and wrong figures): S-1, S-3, W-1, M-1, SEM-1, SEM-2, ---


def test_vignette_columns_only_of_the_loop_items(monkeypatch):
    node = {"code": "L", "name": "L", "attributes": [{"code": "f"}]}
    plan = (node, 2, {1: [0], 2: [1], 3: [0]}, ["bew"], {1: 1, 2: 1, 3: 2}, ["bew_ges", "bew_2x", "alter"])
    monkeypatch.setattr(vp, "_plans", lambda survey: [plan])
    cols = [("L_order", "Reihenfolge"), ("bew_1", "Bewertung [1]"), ("bew_2", "Bewertung [2]"), ("bew_3", "Bewertung [3]"),
            ("bew_ges_1", "Gesamt Zeile 1"), ("bew_ges_2", "Gesamt Zeile 2"), ("bew_ges_3", "Gesamt Zeile 3")]
    out = [c for c, _ in vp.columns(None, cols)]
    assert all(c in out for c in ("bew_ges_1", "bew_ges_2", "bew_ges_3"))
    assert "bew_ges_p1" not in out and "bew_p1" in out and "bew_1" not in out
    row = {"L_order": "2|1", "bew_1": 4, "bew_2": 5, "bew_3": None, "bew_ges_1": 1, "bew_ges_2": 2, "bew_ges_3": 3}
    vp.rows(None, [row])
    assert (row["bew_p1"], row["bew_p2"]) == (5, 4)
    assert "bew_ges_p1" not in row and row["bew_ges_3"] == 3
    # an item "bew_2" outside the loop keeps its column too
    plan2 = (node, 2, {1: [0], 2: [1], 3: [0]}, ["bew"], {1: 1, 2: 1, 3: 2}, ["bew_2"])
    monkeypatch.setattr(vp, "_plans", lambda survey: [plan2])
    assert not vp._owned("bew_2", "bew", ["bew"], ["bew_2"]) and vp._owned("bew_3", "bew", ["bew"], ["bew_2"])


def test_one_statement_subscale(app, client, survey):
    for number, code, k in ((2, "tb", 4), (3, "einst", 3), (4, "absicht", 3)):
        _scale(client, number, code, k)
    _subscales(app, 2, ["A", "A", "A", "B"])
    with app.app_context():
        s = db.session.get(Survey, 1)
        rng = random.Random(4)
        for answers in _answers(rng, 300):
            s.responses.append(Response(source="real", status=Response.COMPLETED, answers=answers))
        db.session.commit()
        assert sem_model.variable(s, "tb")["error"] == "single"
        model = sem_model.build(s)  # the measurement model keeps the other scales and subscale A
        assert model["mode"] == "measurement" and "tb:A" in model["spec"]["latent"]
        assert "tb:B" not in model["spec"]["latent"] and model["skipped"] == ["tb · B"]
    page = client.get("/surveys/1/results?go=1").data.decode()
    assert "Ohne die Subskalen mit nur einer Aussage" in page
    client.post("/surveys/1/researchdesign", data=MultiDict({
        "research_question": "x", "goal": "correlation", "design": "cross",
        "h_id": ["", ""], "h_text": ["H1", "H2"], "h_kind": ["effect", "effect"], "h_dir": ["", ""],
        "h_iv": ["tb", "einst"], "h_dv": ["einst", "absicht"], "h_mv": ["", ""], "h_wv": ["", ""],
        "h_analysis": ["sem", "sem"], "sem_method": "cb"}))
    with app.app_context():
        model = sem_model.build(db.session.get(Survey, 1))
        assert model["hyps"][1]["errors"] == [("tb", "single")]  # only the hypothesis that names it
        assert not model["hyps"][2]["errors"] and model["spec"]["paths"] == [("einst", "absicht")]


# --- from tests/audits/test_audit_2026_10_12_c.py: Audit data and calculations 2026-10-12, package C (diary study): E-1, E-2, D-1 to D-7, D-10, D-12 to D-15 and ---


def test_time_results_keep_negative_values_of_a_signed_scale(app, client):
    _new(app, client)
    with app.app_context():
        survey = db.session.get(Survey, 1)
        item = next(i for b in survey.blocks if diary.part_of(b) == "m1" for i in b.items)
        item.config = {**item.config, "style": "slider", "min": -10, "max": 10, "step": 1}
        db.session.commit()
        from app import export

        col = export._plain_columns(item)[0][0]
        rows = []
        for p, xs in {"p1": [-8, -6, -7], "p2": [5, 6, 7]}.items():
            for n, x in enumerate(xs, start=1):
                rows.append({"person": p, "teil": "m1", "tag": n, "ortszeit": "10:00", col: x})
        out = diary.time_results(survey, rows=rows)[0]
        assert out["n"] == 6 and out["persons"] == 2
        assert out["by_day"][0] == (1, -1.5, 2)
        assert out["icc"] is not None and out["icc"] > 0.9


def test_weights_per_question():
    numbered = {i: SimpleNamespace(enrolment_id=1, occasion={"part": "m1"}) for i in range(1, 5)}
    numbered[5] = SimpleNamespace(enrolment_id=2, occasion={"part": "m1"})
    rows = [{"resp_id": i, "x": (10 if i == 1 else None)} for i in range(1, 5)] + [{"resp_id": 5, "x": 0}]
    w = diary.question_weights(rows, numbered, ["x"])
    answered = [(w[r["resp_id"]], r["x"]) for r in rows if r["x"] is not None]
    assert sum(a for a, _ in answered) == 2
    assert sum(a * x for a, x in answered) / 2 == 5.0
    # a code that counts as missing is no valid value
    rows[1]["x"] = -98
    w = diary.question_weights(rows, numbered, ["x"], {"x": {"-98"}})
    assert w[1] == 1.0 and w[2] == 0.25


# --- from tests/audits/test_audit_2026_10_12_h.py: Audit Daten und Berechnungen 2026-10-12, package H (SEM rest and R script): SEM-3 to SEM-7, SEM-9 to SEM-11, ---


def test_pls_without_paths_takes_the_factor_scheme():
    """SEM-7: a construct without a path takes the other constructs as its inner proxy (factor scheme), not its own
    score; loadings and correlations as an independent implementation of Wold's algorithm (mode A, factor scheme)."""
    rng = np.random.default_rng(11)
    n = 300
    f = rng.multivariate_normal([0, 0, 0], [[1, .5, .3], [.5, 1, .4], [.3, .4, 1]], n)
    cols, data = [], []
    for j, name in enumerate("abc"):
        for k, load in enumerate((.9, .7, .5), 1):
            cols.append(f"{name}{k}")
            data.append(load * f[:, j] + rng.normal(0, math.sqrt(1 - load * load), n))
    X = np.column_stack(data)
    spec = {"latent": {"A": cols[0:3], "B": cols[3:6], "C": cols[6:9]}, "observed": [], "paths": [], "indirect": []}
    r = sem.fit_pls(X, cols, spec)
    assert [round(x["std"], 5) for x in r["loadings"]["C"]] == [0.92399, 0.87729, 0.58087]
    assert [round(x["std"], 5) for x in r["loadings"]["A"]] == [0.88373, 0.83393, 0.72923]
    assert abs(r["latent_corr"]["matrix"][0][1] - 0.451598) < 1e-5
    assert r["latent_sd"] == {"A": 1.0, "B": 1.0, "C": 1.0}


def test_moderated_mediation_by_two_groups_in_plan_page_and_r_script(app, client, survey):
    """SEM-5, SEM-6, SEM-9, SEM-10 a, SEM-3 and decision 8 on a survey: two groups moderating a leg of a mediation is
    an interaction term (planned and named so), the effect field says "je Pfad" for the mediation, the R script centres
    the products over the complete cases of the model, writes the index and the conditional effects per group."""
    _groups_survey(app, client)
    client.post("/surveys/1/researchdesign", data=MultiDict({
        "research_question": "x", "goal": "correlation", "design": "cross", "h_id": ["", ""],
        "h_text": ["H1", "H2"], "h_kind": ["mediation", "moderation"], "h_dir": ["pos", "pos"],
        "h_iv": ["tb", "tb"], "h_dv": ["absicht", "einst"], "h_mv": ["einst", ""], "h_wv": ["", "grp"],
        "h_analysis": ["", "sem"], "h_effect": ["0,3", "0,2"], "sem_method": "cb"}))
    with app.app_context():
        s = db.session.get(Survey, 1)
        hs = study_plan.plan_of(s)["hypotheses"]
        assert not sem_model.moderation_by_groups(s, hs[1]) and sem_model.moderation_groups(s, hs[1]) == 0
        text = sem_model.r_script(s, "d.rds")
        assert 'daten_m <- daten[complete.cases(daten[, c("tb_1"' in text and '"grp_01")]), ]' in text
        assert "daten_m <- indProd(daten_m" in text and "data = daten_m" in text
        assert "imm1 := p" in text and "stellen <- c(0, 1) - mean(daten_m$grp_01)" in text
        assert "bedingte indirekte Effekte" in text
    page = client.get("/surveys/1/researchdesign").data.decode()
    assert "je Pfad (x → m und m → y)" in page
    page = client.get("/surveys/1/results?go=1").data.decode()
    hyp = page[page.index('id="hypothesen"'):page.index('id="sem"')]
    assert "bedingte indirekte Effekte" in hyp and "(G1)" in hyp and "(G2)" in hyp and "(−1 SD)" not in hyp
    part = page[page.index('id="sem"'):]
    assert "grp = „G1“" in part


def test_r_names_unique_and_pls_without_paths():
    """SEM-10 b and c: names that differ only in umlauts get a number; PLS without paths writes no empty seminr
    model."""
    found = sem_model._r_unique(["tb:Größe", "tb:Grüße", "tb:x"], lambda v: "F_" + sem_model._r_name(v))
    assert found == {"tb:Größe": "F_tb_Gr__e", "tb:Grüße": "F_tb_Gr__e_2", "tb:x": "F_tb_x"}
    assert sem_model._r_unique(["a:b"], sem_model._r_name, used={"a_b"}) == {"a:b": "a_b_2"}


def test_moderation_direction_names_the_sign():
    """SEM-11: the direction of a moderation names the sign of the interaction, not "verstärkt"."""
    d = study_plan.directions("moderation")
    assert d["pos"].startswith("positive Wechselwirkung") and "größer" in d["pos"]
    assert d["neg"].startswith("negative Wechselwirkung") and "kleiner" in d["neg"]


# --- from tests/audits/test_audit_rest.py: Remaining fixes from the audit of 2026-10-01 (S5, S9, S12 to S15, S17, S18, D-2, D-6, D-7, D-9, D-12 and small ones). ---


def test_reliability_per_subscale_and_gini_only_with_a_true_zero(app, client, survey):
    add(client, "likert"), add(client, "text_input")
    client.post(E__audit_rest + "/items/2", data=MultiDict(
        [("code", "bfi"), ("question", "Ich ..."), ("points", "5"), ("labeling", "full"), ("level", "metric")]
        + [x for n in range(1, 5) for x in (("stmt_id", f"s{n}"), ("stmt_code", ""), ("stmt_text", f"Aussage {n}"))]))
    client.post(E__audit_rest + "/items/3", data={"code": "betrag", "question": "Betrag", "validation": "decimal"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        item = db.session.get(Item, 2)
        item.config = {**item.config, "level": "metric", "statements": [
            {**st, "subscale": "E__audit_rest" if n < 2 else "N"} for n, st in enumerate(item.config["statements"])]}
        # two independent traits: the pairs agree within, not across
        for e, n_, amount in ((1, 5, 10.0), (2, 4, 20.0), (4, 2, 30.0), (5, 1, 40.0), (3, 3, 50.0), (5, 5, 60.0), (1, 1, 5.0)):
            s.responses.append(Response(source="real", status=Response.COMPLETED,
                                        answers={"2": {"s1": e, "s2": e, "s3": n_, "s4": n_}, "3": amount}))
        db.session.commit()
        out = results.compute(s)
        entry = next(e for e in out["items"] if e["item"].id == 2)
        assert entry["scale"] is None and [(x["name"], x["k"], x["n"]) for x in entry["subscales"]] == [("E__audit_rest", 2, 7), ("N", 2, 7)]
        assert all(x["alpha"] > 0.99 for x in entry["subscales"])
        assert all(c["gini"] is None and not c["ratio"] for c in entry["columns"])      # agreement has no true zero
        amount = next(e for e in out["items"] if e["item"].id == 3)["columns"][0]
        assert amount["ratio"] and 0.2 < amount["gini"] < 0.5
    page = client.get("/surveys/1/results").data.decode()
    assert "Subskala „E__audit_rest“" in page and "Subskala „N“" in page and "Skala aus 4 Aussagen" not in page


def test_small_fixes():
    # cumulative shares from the counts
    stats = results.ordinal_stats([1] * 67 + [2] * 133, {"1": "a", "2": "b"}, set())
    assert round(stats["rows"][0]["cum"] * 200) == 67 and stats["rows"][-1]["cum"] == 1
    # every level of a short scale is listed
    rating = kind_of("rating")
    stars = SimpleNamespace(config={"style": "stars", "points": 5})
    assert rating.scale_values(stars, "x") == [1, 2, 3, 4, 5]
    assert rating.scale_values(SimpleNamespace(config={"style": "slider", "min": 0, "max": 100, "step": 1}), "x") is None
    # the same word counts once, however it was typed
    assoc = kind_of("association")
    assert assoc.normalize("Für  Alle!") == assoc.normalize("Für alle") == "für alle"
    # several separators are thousands separators only in groups of three
    for bad in ("1.2.3", "0.5.5", "1.000.00"):
        with pytest.raises(ValueError):
            psm.parse_price(bad)
        with pytest.raises(ValueError):
            text._num(bad)
    assert psm.parse_price("1.000.000") == 1000000 and text._num("1.000.000") == 1000000
    # the links per group (the field page's form, update_from_form) keep the setting made under "Ablauf"
    s = SimpleNamespace(panel={"params": [], "redirects": {}, "multi": True}, quotas=[])
    form = MultiDict({"param_name": "gruppe", "param_mode": "store", "param_required": "0", "param_allowed": "a, b"})
    assert panel.update_from_form(s, form) is None and s.panel["multi"] is True
    assert s.panel["params"] == [{"name": "gruppe", "mode": "store", "required": False, "allowed": ["a", "b"]}]


# --- from tests/audits/test_audit_small2.py: Small fixes from the audit of 2026-10-01 (S4, S6, S7, S8, S10, S11, D-1, D-3, D-4). ---


def test_numbers_have_a_limit_and_a_plain_notation():
    for bad in ("1e308", "1e5", "1_000", "nan", "inf", "1" + "0" * 16):
        with pytest.raises(ValueError):
            text._num(bad)
    assert text._num("1.000.000") == 1000000 and text._num("-1,5") == -1.5 and text._num("1000000000000000") == 1e15
    for bad in ("1" + "0" * 400, "1" + "0" * 16):
        with pytest.raises(ValueError):
            psm.parse_price(bad)
    assert psm.parse_price("1.299,00") == 1299.0
    # values stored before the limit do not break the statistics
    assert results._num("1e308") is None and results._num(1e300) is None and psm_analysis._num(float("inf")) is None
    assert results.numeric_stats([results._num(v) for v in ("1e308", "1e308", 5)])["mean"] == 5


# --- from tests/audits/test_cr8_a.py: Code review 2026-10-07, package A "Falsche oder verlorene Daten": regression tests for T1, E1, E2, E8, E9, E10, ---


def test_e1_vignette_plan_that_no_longer_fits_is_left_out_with_a_note(app, client, survey):
    from tests.exports.test_transfer import _file, _upload

    _vignette_survey(app, client)
    data = _file(client)
    for n, _, _ in flow.walk(data["flow"]):
        if n.get("t") == "loop":
            n["plan"]["sig"] = "forged"
    page = _upload(client, data, title="Kopie")
    assert "Versuchsplan erzeugen" in page
    after = _loops(app, 2)[0]
    assert vignettes.is_vignettes(after) and not after["plan"].get("decks_rows") and after["rows"] == []
    assert len(after["attributes"]) == 3


def test_e1_plan_subset_through_file(app, client, survey):
    from app import loop_plan
    from tests.exports.test_transfer import _file, _upload

    page = _vignette_survey(app, client)
    client.post(page, data={"action": "source_table"})
    before = _loops(app, 1)[0]
    assert before["mode"] == "plan" and before["subset"]["decks_rows"]
    _upload(client, _file(client), title="Kopie")
    after = _loops(app, 2)[0]
    assert after["mode"] == "plan" and after["subset"]["decks_rows"] == before["subset"]["decks_rows"]
    with app.app_context():
        assert loop_plan.current(db.session.get(Survey, 2), after)


def test_r1_forget_files_removes_every_kept_result(app, survey):
    import os

    from app import results_store, retention

    with app.test_request_context():
        s = db.session.get(Survey, 1)
        f = {"sources": ["real"], "all": False, "drop": True, "speeder": 40, "speed_real": True, "weighted": False}
        assert results_store.save(s, "k:plan", {"fit": {}, "_rows": [[1, 2, 3]]}, f, name="sem")
        assert results_store.save(s, "k", {"items": []}, f)
        folder = os.path.join(app.config["UPLOAD_FOLDER"], "hb", s.uid)
        with open(os.path.join(folder, "board-1.json"), "w") as out:
            out.write("{}")
        results_store.forget_files(s.uid)
        assert sorted(os.listdir(folder)) == ["board-1.json"]  # not a kept result: removed elsewhere
        os.remove(os.path.join(folder, "board-1.json"))
        assert not retention._files_left(s.uid)
        results_store.forget_files("no-such-survey")  # no folder: nothing to do


# --- from tests/audits/test_cr8_d.py: Code review 2026-10-07, package D "Schätzungen und Aufträge" (C1, C3-C8): start, state and rescue of the background ---


def test_silent_run_of_a_living_process_is_not_rescued(app, paths, monkeypatch):
    started = []
    monkeypatch.setattr(maxdiff_hb, "_estimate_and_store", lambda *a, **k: started.append(a))
    _stale_marker(paths)
    _job_file(app, paths, os.getpid())  # the process that took the job lives (this one)
    with app.app_context():
        marker = jobs.marker(paths)
        assert marker["state"] == "running" and not started
        assert maxdiff_hb.claim(paths, {"key": "k2"}, {}) is False          # no second run next to the living one
        # silent for longer than DEAD_AFTER: the number may belong to another process by now
        _stale_marker(paths, jobs.DEAD_AFTER + 5)
        assert jobs.stale(paths, maxdiff_hb._read(paths[1]))


def test_silent_run_of_a_dead_process_is_rescued(app, paths, monkeypatch):
    started = []
    monkeypatch.setattr(maxdiff_hb, "_estimate_and_store", lambda *a, **k: started.append(a))
    _stale_marker(paths)
    _job_file(app, paths, _dead_pid())
    with app.app_context():
        assert jobs.marker(paths)["state"] == "running"
    for t in threading.enumerate():
        if t.name == "hb-rescue":
            t.join(10)
    assert len(started) == 1


def test_run_beats_between_its_progress_steps(app, paths, monkeypatch):
    monkeypatch.setattr(maxdiff_hb, "BEAT_EVERY", 0.05)
    maxdiff_hb._write(paths[1], {"state": "running"})
    seen = []

    def quiet(inp, progress):  # a long step without a tick
        first = maxdiff_hb._read(paths[1])["beat"]
        # waits for the beat instead of a fixed 0.4 s: on busy cores the beat thread came later (test suite concept
        # 2026-10-18, C); a run that never beats still fails after 10 s
        until = time.monotonic() + 10
        while maxdiff_hb._read(paths[1])["beat"] == first and time.monotonic() < until:
            time.sleep(0.05)
        seen.append((first, maxdiff_hb._read(paths[1])["beat"]))
        return {"key": inp["key"]}

    maxdiff_hb._estimate_and_store(app, paths, {"key": "k"}, {}, quiet)
    assert seen[0][1] > seen[0][0]
    assert maxdiff_hb._read(paths[0])["key"] == "k" and not os.path.exists(paths[1])


def test_estimate_main_beats_and_finishes(app, paths, monkeypatch):
    """The process's code path (estimate.main) runs the same store with its own token."""
    monkeypatch.setattr(compute, "runner", lambda kind: lambda inp, progress: {"key": inp["key"]})
    maxdiff_hb._write(paths[1], {"state": "running"})
    job = estimate.job_path(paths)
    maxdiff_hb._write(job, {"kind": "maxdiff_hb", "paths": list(paths), "inp": {"key": "k"}, "pool": {},
                            "upload": app.config["UPLOAD_FOLDER"]})
    monkeypatch.setattr(estimate, "lower_priority", lambda: False)
    estimate.main([job])
    assert maxdiff_hb._read(paths[0])["key"] == "k" and not os.path.exists(paths[1])
    assert not [n for n in os.listdir(os.path.dirname(job)) if ".job.json" in n]


def test_result_not_written_after_folder_was_removed(app, paths):
    import shutil

    maxdiff_hb._write(paths[1], {"state": "running"})

    def runner(inp, progress):
        progress(0.5)
        shutil.rmtree(os.path.dirname(paths[0]))  # survey deleted, retention, erasure meanwhile
        return {"key": inp["key"], "pids": inp["pids"]}

    maxdiff_hb._estimate_and_store(app, paths, {"key": "k", "pids": ["p-deleted"]}, {}, runner)
    assert not os.path.exists(os.path.dirname(paths[0]))                    # not made anew


def test_run_stops_when_its_marker_is_removed(app, paths):
    maxdiff_hb._write(paths[1], {"state": "running"})
    steps = []

    def runner(inp, progress):
        progress(0.2)
        os.remove(paths[1])  # jobs.forget of a cleanup
        for n in range(5):
            progress(0.3 + n / 10)
            steps.append(n)
        return {"key": inp["key"]}

    maxdiff_hb._estimate_and_store(app, paths, {"key": "k"}, {}, runner)
    assert steps == [] and not os.path.exists(paths[0]) and not os.path.exists(paths[1])  # stopped, no error marker


def test_superseded_run_writes_nothing(app, paths):
    """Another run took the marker (a new claim after it went silent): the old one stops."""
    maxdiff_hb._write(paths[1], {"state": "running"})

    def runner(inp, progress):
        progress(0.2)
        maxdiff_hb._write(paths[1], {"state": "running", "run": "another", "key": "k2"})
        return {"key": inp["key"]}

    maxdiff_hb._estimate_and_store(app, paths, {"key": "k"}, {}, runner)
    assert not os.path.exists(paths[0]) and maxdiff_hb._read(paths[1])["run"] == "another"


def test_marker_gone_after_the_result_is_no_error(app, paths, monkeypatch):
    """C4: the result is stored; the marker removed meanwhile does not turn it into an error."""
    maxdiff_hb._write(paths[1], {"state": "running"})
    write = maxdiff_hb._write

    def and_gone(path, data, make=True):
        write(path, data, make)
        if path == paths[0]:
            os.remove(paths[1])

    monkeypatch.setattr(maxdiff_hb, "_write", and_gone)
    maxdiff_hb._estimate_and_store(app, paths, {"key": "k"}, {}, lambda inp, progress: {"key": "k"})
    assert maxdiff_hb._read(paths[0])["key"] == "k" and not os.path.exists(paths[1])


def test_forget_removes_job_files_and_remote_index(app, paths):
    folder = os.path.dirname(paths[0])
    for name in ("item_1.run.json", "item_1.run.json.job.json", "item_1.run.json.job.json.123.running",
                 "item_1.lc.part.json", "item_1.json", "results.bin"):
        with open(os.path.join(folder, name), "w") as f:
            f.write("{}")
    remote = os.path.join(app.config["UPLOAD_FOLDER"], "hb", "_remote")
    maxdiff_hb._write(os.path.join(remote, "A" * 24 + ".json"), {"paths": list(paths)})
    maxdiff_hb._write(os.path.join(remote, "B" * 24 + ".json"), {"paths": ["/elsewhere/item_1.json", "x"]})
    jobs.forget(folder)
    assert sorted(os.listdir(folder)) == ["item_1.json", "results.bin"]     # the results are the cleanups' own part
    assert os.listdir(remote) == ["B" * 24 + ".json"]


def test_rechenknecht_result_after_a_cleanup_is_dropped(app, paths):
    job = "J" * 24
    with app.app_context():
        maxdiff_hb._write(compute._index(app, job), {"kind": "maxdiff_hb", "paths": list(paths), "pool": {},
                                                     "real": ["p1"], "inp": {"key": "k"}})
        maxdiff_hb._write(paths[1], {"state": "running", "remote": job, "key": "k"})
        os.remove(paths[1])  # jobs.forget removed the marker; the index was left (another server process, say)
        assert compute.deliver(app, job, result={"key": "k", "pids": [1]}) is True
    assert not os.path.exists(paths[0])


def test_sem_bootstrap_state_follows_the_rechenknecht(app, client, survey, monkeypatch):
    from app import sem_model

    followed = []
    monkeypatch.setattr(compute, "follow", lambda paths, marker: followed.append(paths) or None)
    with app.app_context():
        s = db.session.get(Survey, 1)
        paths = sem_model._paths(s)
        now = datetime.now(timezone.utc).isoformat()
        maxdiff_hb._write(paths[1], {"state": "running", "started": now, "beat": now, "key": "b", "remote": "x" * 24})
        assert sem_model.boot_state(s, {"boot_key": "b"}) == {"state": "none"}
    assert followed


def test_jobs_start_one_run_at_a_time(app, paths, monkeypatch):
    monkeypatch.setattr(compute, "runner", lambda kind: lambda inp, progress: {"key": inp["key"]})
    with app.app_context():
        assert jobs.start(app, "maxdiff_hb", paths, {"key": "k"}, {}, background=False) is True
        assert maxdiff_hb._read(paths[0])["key"] == "k"
        now = datetime.now(timezone.utc).isoformat()
        maxdiff_hb._write(paths[1], {"state": "running", "started": now, "beat": now, "key": "k"})
        assert jobs.start(app, "maxdiff_hb", paths, {"key": "k2"}, {}, background=False) is False


def test_expected_effect_counts_first_and_is_kept(app, client, survey, monkeypatch):
    from app import compare, dummy, power_pilot, study_plan
    from app.models import Response
    from tests.analysis.test_power import _power_plan, _questions

    _questions(client, app)
    _power_plan(client)
    client.post("/surveys/1/duplicate", data={"title": "Vorstudie", "with_people": "1"})
    built = []
    table_of = compare.table_of
    monkeypatch.setattr(compare, "table_of", lambda *a, **k: built.append(a[0].id) or table_of(*a, **k))
    with app.app_context():
        s = db.session.get(Survey, 1)
        h = study_plan.plan_of(s)["hypotheses"][0]
        assert power_pilot.estimate(s, h, "t") is None and built == []     # no real data: counted, no table
        earlier = db.session.scalars(db.select(Survey).filter(Survey.title == "Vorstudie")).one()
        dummy.generate(earlier, 60, seed=5)
        for r in db.session.scalars(db.select(Response).filter_by(survey_id=earlier.id)):
            r.source = "real"
        db.session.commit()
        first = power_pilot.estimate(earlier, h, "t")
        again = power_pilot.estimate(earlier, h, "t")
        assert first and again == first and built == [earlier.id]           # the second look: kept, no table
        assert "pilot.bin" in os.listdir(os.path.join(app.config["UPLOAD_FOLDER"], "hb", earlier.uid))


def test_nice_ticks_end_and_stay_the_same_for_usual_axes():
    from app import psm_analysis as pa

    t0 = time.monotonic()
    huge = pa.nice_ticks(1e12 - 0.0001, 1e12)
    tiny = pa.nice_ticks(100.0, 100.00000000001)
    assert time.monotonic() - t0 < 1 and 0 < len(huge) <= pa.MAX_TICKS and 0 < len(tiny) <= pa.MAX_TICKS
    assert len(pa.nice_ticks(0, 1e-8, 4)) <= 10
    for lo, hi, count in [(0, 1.4, 4), (0.1, 0.25, 6), (3, 17, 6), (0, 1, 4), (9.99, 129.5, 6), (-5, 5, 6),
                          (0, 2500, 4), (0.5, 0.75, 6), (12.5, 99.9, 6)]:
        assert pa.nice_ticks(lo, hi, count) == _ticks_before(lo, hi, count)


def test_dumped_parts_give_the_same_key():
    from app.conjoint_hb import _dumped

    data = {"format": 3, "type": "conjoint", "config": {"z": 1, "a": [1.5, None, "ä"], "m": {"y": 2, "b": True}},
            "labels": None, "persons": [["p1", {"t1": 2, "t0": 1}], ["p2", datetime(2026, 10, 7)]]}
    cfg = {"draws": 400, "burn": 100, "lc": True}
    parts = {k: json.dumps(v, sort_keys=True, default=str) for k, v in data.items()}
    assert _dumped(parts, cfg) == json.dumps({**data, "settings": cfg}, sort_keys=True, default=str)
    assert _dumped(parts) == json.dumps(data, sort_keys=True, default=str)


def test_conjoint_input_built_once_per_table(app, client, survey, monkeypatch):
    from app import conjoint_hb, export
    from app.models import Item
    from tests.analysis.test_cbc_results import setup
    from tests.analysis.test_conjoint_hb import simulate_segments

    setup(client, tasks=6, versions=2)
    simulate_segments(app, 20)
    with app.app_context():
        item = db.session.get(Item, 2)
        fresh = conjoint_hb.model_input(item, list(export.rows(item.survey, with_hb=False)))  # a plain list: no memo
        table = export.rows(item.survey, with_hb=False)
        calls = []
        collect = conjoint_hb._collect
        monkeypatch.setattr(conjoint_hb, "_collect", lambda *a: calls.append(1) or collect(*a))
        a = conjoint_hb.model_input(item, table)
        b = conjoint_hb.model_input(item, table, {"lc": True})
        c = conjoint_hb.model_input(item, table)
    assert len(calls) == 1
    assert a["key"] == c["key"] == fresh["key"] and b["key"] != a["key"] and a["data_key"] == b["data_key"]
    assert a["persons"] == fresh["persons"] and b["settings"] == {"lc": True}


# --- from tests/audits/test_cr8_e.py: Code review 2026-10-07, package E: computing time of results and exports. ---


def test_r3_kept_results_and_a_new_plan_read_the_pool_once(app, client, survey, monkeypatch):
    from tests.accounts.test_groupvars import randomized

    randomized(app, client, [((0,), "1", 1, 1)] * 8 + [((1,), "2", 5, 5)] * 8)
    assert client.get("/surveys/1/results").status_code == 200  # results kept, no plan yet
    _hypothesis(app)
    calls = _count_rows(monkeypatch)
    assert client.get("/surveys/1/results").status_code == 200
    assert len(calls) == 1  # only for the hypotheses


def test_r4_the_plan_builds_once_per_element_and_only_inside_an_export(app, client, survey):
    from app.items import base

    built = []
    item = object()
    assert base.prepared(item, "x", lambda: built.append(1) or 1) == 1
    assert base.prepared(item, "x", lambda: built.append(1) or 1) == 1
    assert len(built) == 2  # no plan open: anew every time
    with base.export_plan():
        for _ in range(5):
            assert base.prepared(item, "x", lambda: built.append(1) or 2) == 2
        assert base.prepared(object(), "x", lambda: 3) == 3  # another element
    assert len(built) == 3


def test_r4_maxdiff_person_scores_as_the_counts():
    import numpy as np

    from app.maxdiff_analysis import counts, person_scores, scores

    rng = random.Random(5)
    for _ in range(300):
        n = rng.randint(3, 9)
        sets = [rng.sample(range(n), rng.randint(2, min(5, n))) for _ in range(rng.randint(1, 6))]
        tasks = {}
        for t in range(len(sets) + 1):
            if rng.random() < 0.8:
                pick = sets[t] if t < len(sets) else list(range(n))
                tasks[t] = (rng.choice(pick + [None]), rng.choice(pick))
        shown, best, worst, _ = counts(n, [sets], [(0, tasks)])
        bw, _, _ = scores(shown, best, worst)
        assert person_scores(n, sets, tasks) == [None if np.isnan(x) else round(float(x), 4) for x in bw]


def test_r5_marked_view_and_comparisons_keep_their_own_files(app, client, survey, monkeypatch):
    from app import results, results_store
    from tests.accounts.test_groupvars import randomized

    randomized(app, client, [((0,), "1", 1, 2)] * 6 + [((1,), "2", 4, 5)] * 6)
    calls = []
    real = results.compute
    monkeypatch.setattr(results, "compute", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    seen = []
    for url in ("/surveys/1/results", "/surveys/1/results?markierte=1", "/surveys/1/results",
                "/surveys/1/results?markierte=1"):
        before = len(calls)
        assert client.get(url).status_code == 200
        seen.append(len(calls) > before)
    assert seen == [True, True, False, False]
    with app.app_context():
        s = db.session.get(Survey, 1)
        folder = os.path.join(app.config["UPLOAD_FOLDER"], "hb", s.uid)
        assert {"results.bin", "results-markiert.bin"} <= set(os.listdir(folder))
        assert {"results-markiert.bin", "compare-markiert.bin", "compare-weighted.bin"} <= set(results_store.KEPT)
        results_store.forget_files(s.uid)
        assert not [n for n in os.listdir(folder) if n.endswith(".bin")]


# --- from tests/audits/test_cr8_g.py: Code review 2026-10-07, package G "Live, Editor-Skripte, Rechner": F1, F2, F7, F12, F16, F17, F20, F22, F23, F24, ---


def test_f23_simulator_hb_request_uses_seq():
    src = _js("simulator.js")
    hb = src[src.index('"askurious:hb-updated"'):]
    assert "var n = seq[id] = (seq[id] || 0) + 1;" in hb and "n !== seq[id]" in hb
    assert "now.replaceWith(fresh)" in hb


def test_f24_percent_reads_decimals():
    assert study_plan._percent("12.5") == 13 and study_plan._percent("12,5") == 13
    assert study_plan._percent("12.4") == 12 and study_plan._percent("20") == 20
    assert study_plan._percent("") is None and study_plan._percent("95") is None and study_plan._percent("nan") is None
    assert "Math.round(excl)" in _js("samplesize.js")


def test_f24_take_target_with_decimal_exclusions(app, client, survey):
    client.post("/surveys/1/researchdesign/goal", data={"n": "125", "excl": "12.5"})
    with app.app_context():
        sample = study_plan.plan_of(db.session.get(Survey, 1))["sample"]
        assert (sample["target"], sample["exclusions"]) == (125, 13)


# --- from tests/audits/test_cr8_i.py: Code review 2026-10-07, package I "Kleinere Fehler": R6, E3, E5, E6, E12, K5, K6, K7, T6, G11. ---


def test_e3_study_plan_follows_undo_and_redo_of_a_renaming(app, client, survey):
    add(client, "single_choice")   # 2
    add(client, "single_choice")   # 3
    client.post("/surveys/1/editor/items/2", data={"code": "geschlecht", "question": "G?", "answer_mode": "optional"})
    client.post("/surveys/1/editor/items/3", data={"code": "zufr", "question": "Z?", "answer_mode": "optional"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.study_plan = {"hypotheses": [{"iv": "geschlecht", "dv": "zufr", "text": "H1"}]}
        db.session.commit()
    client.post("/surveys/1/editor/items/2", data={"code": "gender", "question": "G?", "answer_mode": "optional"})

    def plan():
        with app.app_context():
            return db.session.get(Item, 2).code, db.session.get(Survey, 1).study_plan["hypotheses"][0]["iv"]

    assert plan() == ("gender", "gender")
    client.post("/surveys/1/editor/undo")
    assert plan() == ("geschlecht", "geschlecht")
    client.post("/surveys/1/editor/redo")
    assert plan() == ("gender", "gender")


def test_e3_rename_codes_swaps_at_once():
    from types import SimpleNamespace

    from app import study_plan

    s = SimpleNamespace(study_plan={"hypotheses": [{"iv": "a", "dv": "b"}]})
    study_plan.rename_codes(s, {"a": "b", "b": "a"})
    assert s.study_plan["hypotheses"][0] == {"iv": "b", "dv": "a"}


# --- from tests/audits/test_cr8_j.py: Code review 2026-10-07, package J (tidying up): dead code, dead templates and CSS, one counter for the limits. ---


def test_d7_functions_without_a_caller_are_gone():
    assert "def attribute_index" not in inspect.getsource(attributes)
    assert not hasattr(study_plan, "goal_choices") and hasattr(study_plan, "design_choices")
    assert not hasattr(panel, "set_groups")


# --- from tests/audits/test_review_medium.py: The medium findings of the code review of 2026-10-02 (code-review-2026-10-02.md, M5 to M16). ---


def test_merged_scale_levels_give_no_exact_mean():
    counts = [1, 6, 0, 0, 5]                         # one person answered "1": the levels 1 and 2 are put together
    data = {"shape": "scale", "n": 12, "first": 1, "levels": list("12345"),
            "rows": [{"label": "A", "n": 12, "reverse": False, "_counts": counts, "_mean": 38 / 12, "_sd": 1.64}]}
    row = boards._reduce(data)["rows"][0]
    assert [(s["a"], s["b"], s["count"]) for s in row["segs"]][0] == (0, 1, 7)
    assert row["mean"] == pytest.approx((7 * 1.5 + 5 * 5) / 12) and row["mean"] != pytest.approx(38 / 12)
    plain = {"shape": "scale", "n": 12, "first": 1, "levels": list("12345"),
             "rows": [{"label": "A", "n": 12, "reverse": False, "_counts": [0, 6, 0, 0, 6], "_mean": 3.5, "_sd": 1.5}]}
    assert boards._reduce(plain)["rows"][0]["mean"] == 3.5  # nothing put together: the mean as it is


def test_live_comparison_leaves_attention_checks_out_of_the_scale(app, client, survey):
    from app.items.sample_compare import SampleCompare

    add(client, "likert")                                       # 2
    client.post(E__review_medium + "/items/2", data=MultiDict([
        ("code", "m"), ("points", "5"), ("labeling", "full"), ("answer_mode", "optional"), ("level", "metric"),
        ("stmt_id", "s1"), ("stmt_code", ""), ("stmt_text", "A"),
        ("stmt_id", "s2"), ("stmt_code", ""), ("stmt_text", "B"),
        ("stmt_id", "s3"), ("stmt_code", ""), ("stmt_text", ""), ("stmt_check_s3", "1")]))
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.checks(item) == {"s3": 1}
        assert SampleCompare().value(item, {"s1": 5, "s2": 5, "s3": 1}) == 5.0
        # a statement shown and left out: no scale mean, as on the results page (app/scales.py)
        assert SampleCompare().value(item, {"s1": 5, "s3": 1}) is None
