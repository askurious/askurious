"""Editor: the tests of the earlier audit and review files about this area (sorted by area,
architecture concept 2026-10-12, G). Each block names the file it came from; its helpers are in
tests/helpers/audit_<file>.py."""
from tests.helpers.audit_audit13_rest import MultiDict, Survey, User, add, db, json  # noqa: F401
from tests.helpers.audit_audit_2026_10_06_a import Board, ConstructShare, D, Item, OTHER as OTHER__audit_2026_10_06_a, _editor, _insert, _linked_question as _linked_question__audit_2026_10_06_a, _member as _member__audit_2026_10_06_a, _own_survey, _person, choice, experiments_on, flow, linked  # noqa: F401
from tests.helpers.audit_audit_2026_10_06_b import _fresh_limits, time  # noqa: F401
from tests.helpers.audit_audit_2026_10_06_c import _fresh_count, _Answer, _real_fetch  # noqa: F401
from tests.helpers.audit_audit_2026_10_06_d import _no_failures_from_other_tests, OTHER as OTHER__audit_2026_10_06_d, _as, _user  # noqa: F401
from tests.helpers.audit_audit_2026_10_06_e import CONSENT_OK, ESCAPE, _dd, re  # noqa: F401
from tests.helpers.audit_audit_2026_10_06_g import piping  # noqa: F401
from tests.helpers.audit_audit_2026_10_06_reste import ast, inspect, pathlib, rows  # noqa: F401
from tests.helpers.audit_audit_2026_10_12_a import Block, E as E__audit_2026_10_12_a, Response, _answers, _scale, fill_table, loops, random, sem_model, study_plan  # noqa: F401
from tests.helpers.audit_audit_2026_10_12_h import _fit, _products, np  # noqa: F401
from tests.helpers.audit_audit_rest import E as E__audit_rest, export  # noqa: F401
from tests.helpers.audit_audit_small import ranking, runtime  # noqa: F401
from tests.helpers.audit_cr8_a import OPTS, _attention_run, _linked_in_two, _loops, _question, _vignette_survey, app, client, survey, vignettes  # noqa: F401
from tests.helpers.audit_cr8_b import retention  # noqa: F401
from tests.helpers.audit_cr8_c import PreviewComment  # noqa: F401
from tests.helpers.audit_cr8_g import _js  # noqa: F401
from tests.helpers.audit_cr8_i import TYPES, TrashedItem, _linked_question as _linked_question__cr8_i, fill_questions, os  # noqa: F401
from tests.helpers.audit_review_fixes import E as E__review_fixes, OTHER as OTHER__review_fixes, _consent_and_two_blocks, _errors as _errors__review_fixes, _member as _member__review_fixes, fill_consent, split_node  # noqa: F401
from tests.helpers.audit_review_medium import E as E__review_medium, _errors as _errors__review_medium, _file, _loop_around_block_2, _upload, _with_parameter, conditions, test_quotas  # noqa: F401
from tests.helpers.page_pos import page_pos


# --- from tests/audits/test_audit13_rest.py: Audit Daten und Berechnungen 2026-10-12, package I (exports, choice models, elements, rest): E-3, E-4, E-5, E-6, ---


def test_e6_duplicate_and_file_take_paper_not_admin_hidden(app, client, survey):
    from app import duplicate, transfer

    with app.test_request_context():
        s = db.session.get(Survey, 1)
        s.paper = {"review": "none", "keep": "answers", "ai_ok": {"by": 1}}
        s.admin_hidden = True
        db.session.commit()
        user = db.session.get(User, 1)
        new, _ = duplicate.create(s, user, "Kopie")
        assert new.paper == {"review": "none", "keep": "answers"} and not new.admin_hidden
        data = transfer.dump(s, with_files=False)
        assert data["survey"]["paper"] == {"review": "none", "keep": "answers"}
        read, _ = transfer.create(transfer.loads(json.dumps(data).encode()), user)
        assert read.paper == {"review": "none", "keep": "answers"}


def test_n12_iat_order_checked_against_the_drawn_one(app, client, survey):
    from app.items.iat import BLOCKS

    add(client, "iat")
    with app.app_context():
        s = db.session.get(Survey, 1)
        item = [i for b in s.blocks for i in b.items if i.type == "iat"][0]
        item.config = {**(item.config or {}), "order": "random"}
        kind = item.kind
        assert (item.config or {}).get("stimuli"), "the IAT starts with its example terms"
        pid = next(p for p in (f"p{n}" for n in range(50)) if kind.order_of(item, p) == 2)
        order, blocks = kind.plan(item, pid)
        trials = [[b["n"], st, 700, 0] for b in blocks for st, _ in b["trials"]]
        assert len(trials) == sum(BLOCKS)
        good = MultiDict({f"i{item.id}": json.dumps({"o": order, "t": trials})})
        bad = MultiDict({f"i{item.id}": json.dumps({"o": 3 - order, "t": trials})})
        assert kind.parse_answer(item, good, "de", meta={"pid": pid})["o"] == 2
        item.answer_mode = "optional"
        assert kind.parse_answer(item, bad, "de", meta={"pid": pid}) is None
        assert kind.parse_answer(item, bad, "de", meta={}) is not None  # no pid (dummy data): not checked


# --- from tests/audits/test_audit_2026_10_06_a.py: Security audit 2026-10-06, package A "Rollen und Objekte": S1, S2, S9, S10, S12, S22, H5 and the side finding on ---


def test_s1_shared_for_use_only_adapts_the_own_use(app, client, survey):
    key = _linked_question__audit_2026_10_06_a(app, client)                       # the maker: survey 1, question 2
    with app.app_context():
        db.session.add(ConstructShare(construct_id=linked.get(key).id, orcid=OTHER__audit_2026_10_06_a))
        db.session.commit()
    other = _person(app, OTHER__audit_2026_10_06_a, "Zed", may_create=True)
    assert other.get("/surveys/1/editor/").status_code == 404  # the maker's draft is not theirs
    sid, block = _own_survey(app, other, "Zeds Befragung")
    mine = _insert(app, other, sid, block, key)
    with app.app_context():
        code = db.session.get(Item, mine).code
    choice(other, sid, mine, code=code)                        # saved as it is: no adaptation
    with app.app_context():
        assert not linked.adapted(db.session.get(Item, mine))
    choice(other, sid, mine, question="Bitte geben Sie Ihre Kontonummer an.", code=code)
    with app.app_context():
        assert db.session.get(Item, 2).texts["de"]["question"] == "Frage"      # the maker's draft stays
        assert linked.get(key).spec["texts"]["de"]["question"] == "Frage"     # and so does the element
        item = db.session.get(Item, mine)
        assert item.texts["de"]["question"] == "Bitte geben Sie Ihre Kontonummer an."
        assert linked.is_linked(item) and linked.adapted(item)                  # their own use is adapted


def test_s1_maker_reaches_only_drafts_they_may_edit(app, client, survey):
    key = _linked_question__audit_2026_10_06_a(app, client)
    with app.app_context():
        db.session.add(ConstructShare(construct_id=linked.get(key).id, orcid=OTHER__audit_2026_10_06_a))
        db.session.commit()
    other = _person(app, OTHER__audit_2026_10_06_a, "Zed", may_create=True)
    sid, block = _own_survey(app, other, "Zeds Befragung")
    theirs = _insert(app, other, sid, block, key)
    own_sid, own_block = _own_survey(app, client, "Zweite des Herstellers")
    own = _insert(app, client, own_sid, own_block, key)
    choice(client, 1, 2, question="Kennen Sie Statistik?")
    with app.app_context():
        assert db.session.get(Item, own).texts["de"]["question"] == "Kennen Sie Statistik?"  # a draft the maker edits
        assert db.session.get(Item, theirs).texts["de"]["question"] == "Frage"               # not someone else's
        assert linked.get(key).spec["texts"]["de"]["question"] == "Kennen Sie Statistik?"


def test_text_input_keeps_its_link_when_saved(app, client, survey):
    add(client, "text_input")
    client.post("/surveys/1/editor/items/2", data={"code": "alter", "question": "Wie alt?", "validation": "integer"})
    client.post("/surveys/1/editor/items/2/link", data={"name": "Alter"})
    client.post("/surveys/1/editor/items/2", data={"code": "alter", "question": "Wie alt sind Sie?", "validation": "integer"})
    with app.app_context():
        item = db.session.get(Item, 2)
        assert linked.is_linked(item) and linked.get(linked.key_of(item)).spec["texts"]["de"]["question"] == "Wie alt sind Sie?"


def test_s9_redo_on_a_dashboard_with_a_link_needs_admin(app, client, survey):
    editor = _editor(app)
    editor.post(f"{D}/new", data={"template": "empty", "name": "Harmlos"})
    with app.app_context():
        bid = db.session.scalar(db.select(Board.id).filter_by(survey_id=1))
    editor.post(f"{D}/{bid}", data={"name": "Neuer Name"})
    editor.post("/surveys/1/editor/undo")
    with app.app_context():
        name = db.session.get(Board, bid).name
    client.post(f"{D}/{bid}/link", data={"valid": "30"})       # an admin makes a link
    r = editor.post("/surveys/1/editor/redo", follow_redirects=True)
    assert "Admin" in r.data.decode()
    with app.app_context():
        assert db.session.get(Board, bid).name == name
    r = editor.post("/surveys/1/editor/undo", follow_redirects=True)   # "Dashboard angelegt" would delete it
    with app.app_context():
        assert db.session.get(Board, bid) is not None


def test_s9_data_pool_by_undo_while_a_dashboard_has_a_link(app, client, survey):
    editor = _editor(app)
    client.post(f"{D}/new", data={"template": "empty", "name": "Bericht"})
    editor.post("/surveys/1/pool", data={"source": ["real", "test"], "all": "1"})
    with app.app_context():
        pool = db.session.get(Survey, 1).data_pool
    client.post(f"{D}/1/link", data={"valid": "30"})
    editor.post("/surveys/1/editor/undo")
    with app.app_context():
        assert db.session.get(Survey, 1).data_pool == pool
    client.post("/surveys/1/pool", data={"source": ["real"]})   # an admin may
    with app.app_context():
        assert db.session.get(Survey, 1).data_pool != pool


def test_s12_copy_to_needs_the_feature(app, client, survey, monkeypatch):
    # since 2026-10-16 no element type is experimental: the voice message stands in for one (feature "probe")
    from app import experimental
    from app.items.voice import Voice

    features = experimental.FEATURES + [("probe", "Probe", "Test")]
    monkeypatch.setattr(experimental, "FEATURES", features)
    monkeypatch.setattr(experimental, "KEYS", [k for k, _l, _t in features])
    monkeypatch.setattr(Voice, "experimental", "probe", raising=False)
    experiments_on(client, "probe")
    add(client, "voice")
    other = _person(app, OTHER__audit_2026_10_06_a, "Other", may_create=True)
    _member__audit_2026_10_06_a(app, 1, OTHER__audit_2026_10_06_a, "editor")
    sid, _block = _own_survey(app, other, "Eigene")
    with app.app_context():
        target = db.session.get(Survey, sid).code
    assert other.post("/surveys/1/editor/items/2/copy-to", data={"target": target}).status_code == 404
    with app.app_context():
        assert "voice" not in [i.type for b in db.session.get(Survey, sid).blocks for i in b.items]


def test_h5_computed_value_only_from_the_own_survey(app, client, survey):
    sid, block = _own_survey(app, client, "Andere")
    client.post(f"/surveys/{sid}/editor/blocks/{block}/items", data={"type": "text_input"})
    client.post(f"/surveys/{sid}/editor/blocks/{block}/items", data={"type": "calc"})
    with app.app_context():
        source, calc = db.session.scalars(db.select(Item.id).filter_by(block_id=block).order_by(Item.id)).all()
    client.post(f"/surveys/{sid}/editor/items/{source}", data={"code": "x", "question": "x?", "validation": "integer"})
    client.post(f"/surveys/{sid}/editor/items/{calc}", data={"code": "y", "question": "y", "calc_a": str(source),
                                                            "calc_op": "mul", "calc_b": "", "calc_fixed": "12",
                                                            "calc_places": "0"})
    cond = {"item": calc, "op": "gt", "value": "10"}
    with app.app_context():
        other, own = db.session.get(Survey, 1), db.session.get(Survey, sid)
        assert flow.holds(cond, {source: 5}, own)
        assert not flow.holds(cond, {source: 5}, other)
        assert flow.computed_value(calc, {source: 5}, other) is None


# --- from tests/audits/test_audit_2026_10_06_b.py: Package B of the security and performance audit of 2026-10-06: limits for the public pages. ---


def test_a_quiz_in_blocks_counts_from_the_start_of_the_block(app, client):
    from tests.live.test_live import add as live_add, steer
    from tests.live.test_live_audit_f import _blocks, _patch_live, _rows

    code, sid, block = _blocks(app, client, "Quiz")
    q = live_add(app, client, sid, block, "single_choice", question="2+2?", options=["3", "4"])
    client.post(f"/presenter/{code}/control", data={"action": "quiz_set", "item": q, "on": "1", "correct": "2", "seconds": "20"})
    steer(client, code, action="start")
    _patch_live(app, sid, block_at=time.time() - 12)                       # the block started 12 seconds ago
    late = app.test_client()                                               # a second device loads the page only now
    late.get(f"/live/{code}")
    assert 7 <= late.get(f"/live/{code}/state").json["left"] <= 8
    assert late.post(f"/live/{code}/answer", data={"ref": f"p-{block}-0", f"i{q}": "2"}).json["ok"]
    (answers, _, _, layout), = _rows(app, sid)
    assert layout["live_times"][str(q)] >= 12                               # not from loading the page


# --- from tests/audits/test_audit_2026_10_06_c.py: Audit of 2026-10-06, package C (uploads and resources): voice messages written only when the page goes on, with a ---


def test_doi_redirect_loops_end(monkeypatch):
    loop = {"https://doi.org/10.1/l": _Answer(302, "https://api.crossref.org/a"),
            "https://api.crossref.org/a": _Answer(302, "https://api.crossref.org/b"),
            "https://api.crossref.org/b": _Answer(302, "https://api.crossref.org/a")}
    citations, asked = _real_fetch(monkeypatch, loop)
    assert citations.fetch("10.1/l") == {} and len(asked) == citations.MAX_HOPS + 1


# --- from tests/audits/test_audit_2026_10_06_d.py: Security audit 2026-10-06, package D "Konten": the right to create surveys of invited people (S11), blocking ends ---


def test_blocking_ends_every_session_also_after_unblocking(app, client):
    other = app.test_client()
    _as(other, OTHER__audit_2026_10_06_d, "Kollegin")
    assert other.get("/account/").status_code == 200
    uid = _user(app, OTHER__audit_2026_10_06_d).id
    client.post(f"/admin/accounts/{uid}/lock", data={"action": "block"})
    client.post(f"/admin/accounts/{uid}/lock", data={"action": "unblock"})
    assert other.get("/account/").status_code == 302                          # the old cookie stays dead


# --- from tests/audits/test_audit_2026_10_06_e.py: Security audit 2026-10-06, package E: researcher texts of the delay discounting stay inside their data block (S3), ---


def test_discounting_texts_cannot_end_the_data_block(app, client, survey):
    _dd(app, client)
    with app.app_context():
        item = db.session.get(Item, 2)
        de = dict((item.texts or {}).get("de") or {})
        de.update(now=ESCAPE, unit="{n} €</script><h1>x</h1>",
                  delays={d["id"]: "<input name=pw type=password>" for d in item.config["delays"]})
        item.texts = {**(item.texts or {}), "de": de}
        db.session.commit()
        task = item.kind.task(item, "de")
    assert isinstance(task, dict) and task["now"] == ESCAPE                     # the object; the template encodes it
    client.post("/surveys/1/start", data={})
    with app.app_context():
        url = f"/s/{db.session.get(Survey, 1).code}"
    p = app.test_client()
    p.get(url)
    p.post(url, data=CONSENT_OK)
    html = p.get(url).data.decode()
    block = re.search(r"<script type=\"application/json\" data-dd-task>(.*?)</script>", html, re.S).group(1)
    assert "<" not in block and json.loads(block)["now"] == ESCAPE              # as text for participant.js, not as HTML
    assert 'action="https://evil.example/x"' not in html and "<form action=https://evil.example" not in html
    assert "<input name=pw" not in html


# --- from tests/audits/test_audit_2026_10_06_g.py: Audit 2026-10-06, package G: performance of the database and of taking part. P3 (index for the counts of ---


def test_piping_works_a_value_out_only_when_asked(app, client, survey, monkeypatch):
    from tests.conftest import add

    add(client, "single_choice", block=2)
    with app.app_context():
        s = db.session.get(Survey, 1)
        item = next(i for b in s.blocks for i in b.items if i.type == "single_choice")
        calls = []
        kind = type(item.kind)
        original = kind.pipe_text
        monkeypatch.setattr(kind, "pipe_text", lambda self, *a, **k: calls.append(1) or original(self, *a, **k))
        values = piping.values(s, {item.id: "1"}, "de")
        assert calls == [] and item.code in values                                     # nothing worked out yet
        first = values[item.code]
        assert values[item.code] == first and calls == [1]                             # once, then kept
        assert dict(values) == {name: values[name] for name in piping.sources(s)}


# --- from tests/audits/test_audit_2026_10_06_reste.py: Security audit 2026-10-06, the rests S17 and H5: the setting "eigene Befragungen anlegen" on an account has three ---


def test_h5_the_survey_is_a_required_parameter():
    for function in (flow.computed_value, rows.hidden):
        assert inspect.signature(function).parameters["survey"].default is inspect.Parameter.empty


def test_h5_every_condition_in_the_app_is_checked_with_its_survey():
    """No call of flow.holds, flow.cond_holds or rows.hidden in the app leaves the survey out (it is the third,
    respectively fourth, argument)."""
    needed = {"holds": 3, "cond_holds": 4, "hidden": 3, "computed_value": 3}
    root = pathlib.Path(__file__).resolve().parents[2] / "app"
    missing = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) \
                    and func.value.id in ("flow", "rows", "row_logic") and func.attr in needed:
                name = func.attr
            elif isinstance(func, ast.Name) and func.id in ("holds", "cond_holds", "computed_value") and path.name == "flow.py":
                name = func.id
            else:
                continue
            if len(node.args) + len(node.keywords) < needed[name]:
                missing.append(f"{path.name}:{node.lineno} {name}")
    assert missing == []


# --- from tests/audits/test_audit_2026_10_12_a.py: Audit Daten und Berechnungen 2026-10-12, package A (crashes and wrong figures): S-1, S-3, W-1, M-1, SEM-1, SEM-2, ---


def test_loop_scale_stays_out_of_the_model(app, client, survey):
    for number, code, k in ((2, "tb", 4), (3, "einst", 3), (4, "absicht", 3)):
        _scale(client, number, code, k)
    client.post(E__audit_2026_10_12_a + "/blocks")                                   # block 3
    add(client, "likert", block=3)                               # item 5
    client.post(f"{E__audit_2026_10_12_a}/items/5", data=MultiDict(
        [("code", "loopsk"), ("question", "Skala in Schleife"), ("points", "7"), ("labeling", "full"), ("level", "metric")]
        + [x for n in range(1, 4) for x in (("stmt_id", f"s{n}"), ("stmt_code", ""), ("stmt_text", f"L Aussage {n}"))]))
    url = client.post(f"{E__audit_2026_10_12_a}/blocks/3/loop").headers["Location"].split("?")[0]
    fill_table(client, url, robot=["A", "B"])
    with app.app_context():
        rows = [r["id"] for r in loops.config(db.session.get(Block, 3))["rows"]]
        s = db.session.get(Survey, 1)
        rng = random.Random(4)
        for answers in _answers(rng, 300):
            for rid in rows:
                answers[f"5#{rid}"] = {f"s{i}": rng.randint(1, 7) for i in range(1, 4)}
            s.responses.append(Response(source="real", status=Response.COMPLETED, answers=answers))
        db.session.commit()
    with app.app_context():
        s = db.session.get(Survey, 1)
        model = sem_model.build(s)
        assert model["mode"] == "measurement" and "loopsk" not in model["spec"]["latent"]
        assert sem_model.variable(s, "loopsk")["error"] == "loop"
        assert "loopsk" not in [c for c, _ in study_plan.measures(s)]
        from app import plan_model

        assert all(v["code"] != "loopsk" for v in plan_model.variables(s))
    page = client.get("/surveys/1/results?go=1").data.decode()
    part = page[page.index('id="sem"'):]
    assert "Zu wenige vollständige Teilnahmen: 0 von mindestens" not in part
    assert "N = 300 vollständige Teilnahmen" in part


# --- from tests/audits/test_audit_2026_10_12_h.py: Audit Daten und Berechnungen 2026-10-12, package H (SEM rest and R script): SEM-3 to SEM-7, SEM-9 to SEM-11, ---


def test_simple_slopes_and_conditional_effects_from_the_model_implied_sds():
    """SEM-4: the model-implied covariances of the latent variables give (a1 ∓ a3·sd_w)·sd_x/sd_m = 0.098268 / 0.631506
    and the conditional indirect effects 0.050468 / 0.324325; the old formula √(1 + r²) gave 0.0835 / 0.6462."""
    rng = np.random.default_rng(5)
    n = 500
    x = rng.normal(0, 1, n)
    w = 0.4 * x + rng.normal(0, 0.9, n)
    m = 0.4 * x + 0.2 * w + 0.25 * x * w + rng.normal(0, 0.8, n)
    y = 0.5 * m + 0.1 * x + rng.normal(0, 0.8, n)
    cols, data = [], []
    for name, f, loads in (("x", x, (1, .8, .9)), ("w", w, (1, .9, .7)), ("m", m, (1, .8, .9)), ("y", y, (1, .7, .8))):
        for k, load in enumerate(loads, 1):
            cols.append(f"{name}{k}")
            data.append(3 + load * f + rng.normal(0, 0.55, n))
    X = np.column_stack(data)
    XP = _products(X, {c: k for k, c in enumerate(cols)}, [(f"x{k}", f"w{k}") for k in (1, 2, 3)])
    spec = {"latent": {"X": ["x1", "x2", "x3"], "W": ["w1", "w2", "w3"], "M": ["m1", "m2", "m3"], "Y": ["y1", "y2", "y3"],
                       "X×W": ["x1×w1", "x2×w2", "x3×w3"]},
            "observed": [], "paths": [("X", "M"), ("W", "M"), ("X×W", "M"), ("M", "Y"), ("X", "Y")],
            "indirect": [("X", "M", "Y")],
            "products": {"X×W": {"x": "X", "w": "W", "kind": "ll", "pairs": [["x1", "w1"], ["x2", "w2"], ["x3", "w3"]]}},
            "modmed": [("a", "X", "M", "Y", "X×W")]}
    result = _fit(XP, cols + ["x1×w1", "x2×w2", "x3×w3"], spec)
    mod = {"x": "X", "w": "W", "y": "M", "xw": "X×W"}
    slopes = sem_model.simple_slopes(result, mod)
    assert [k for k, _, _ in slopes] == ["low", "high"]
    assert abs(slopes[0][2] - 0.098268) < 2e-5 and abs(slopes[1][2] - 0.631506) < 2e-5
    idx = sem_model.modmed_index(result, {"modmed": ("a", "X", "M", "Y", "X×W"), "moderation": mod})
    low, high = (v for _, _, v in idx["conditional"])
    assert abs(low - 0.050468) < 2e-5 and abs(high - 0.324325) < 2e-5
    assert abs(idx["value"] - 0.154) < 5e-4  # the index stays the standardised a3·b (reference 0.154)


# --- from tests/audits/test_audit_rest.py: Remaining fixes from the audit of 2026-10-01 (S5, S9, S12 to S15, S17, S18, D-2, D-6, D-7, D-9, D-12 and small ones). ---


def test_item_order_inside_a_loop_is_exported_per_pass(app, client, survey):
    from tests.editor.test_loops import setup

    setup(client)
    add(client, "text_input")                                    # 3, in the loop block
    client.post(E__audit_rest + "/items/3", data={"code": "why", "question": "Warum?"})
    with app.app_context():
        block = db.session.get(Block, 2)
        block.randomize_items = True
        rows = [r["id"] for r in loops.config(block)["rows"]]
        s = db.session.get(Survey, 1)
        names = [c for c, _ in export.columns(s)]
        order_cols = [c for c in names if re.fullmatch(r"page\d+_order_\d+", c)]
        assert len(order_cols) == len(rows) and not [c for c in names if re.fullmatch(r"page\d+_order", c)]
        s.responses.append(Response(source="real", status=Response.COMPLETED, pid="1-x", answers={}, layout={
            "pages": [{"block": 2, "row": rows[0], "items": [3, 2]}, {"block": 2, "row": rows[1], "items": [2, 3]}]}))
        db.session.commit()
        row = export.rows(s)[0]
        assert row[order_cols[0]] == "why_1|accept_1" and row[order_cols[1]] == "accept_2|why_2"
        assert order_cols[0] in {r[0] for r in export.codebook(s)}


# --- from tests/audits/test_audit_small.py: Smaller points of the data audit: codebook values, orders in loops, screen-out times, long JSON in .sav. ---


def test_randomized_elements_get_a_new_order_in_every_loop_pass(app, client, survey):
    from tests.editor.test_loops import setup

    setup(client)                                                   # item 2 in a loop with 4 rows
    ranking(client, item_id=3)                                      # 3, same block
    with app.app_context():
        s = db.session.get(Survey, 1)
        rows = [r["id"] for r in __import__("app.loops", fromlist=["config"]).config(db.session.get(Block, 2))["rows"]]
        orders = set()
        for seed in range(8):
            layout = runtime.build_layout(s, random.Random(seed))
            assert all(sorted(layout["stmt"][f"3#{rid}"]) == ["a", "b", "c"] for rid in rows)
            orders |= {tuple(layout["stmt"][f"3#{rid}"]) for rid in rows}
        assert len(orders) > 1
        layout = runtime.build_layout(s, random.Random(1))
        layout["stmt"][f"3#{rows[1]}"] = ["c", "a", "b"]
        r = Response(survey_id=1, status=Response.COMPLETED, layout=layout, answers={})
        db.session.add(r)
        db.session.commit()
        assert runtime.statement_orders(r, rows[1]) == {3: ["c", "a", "b"]} and runtime.statement_orders(r) == {}
        assert "prio_order_2" in [c for c, _ in export.columns(s)]
        assert export.rows(s)[0]["prio_order_2"] == "prio_3_2|prio_1_2|prio_2_2"
        assert "prio_order_2" in [row[0] for row in export.codebook(s)]


# --- from tests/audits/test_cr8_a.py: Code review 2026-10-07, package A "Falsche oder verlorene Daten": regression tests for T1, E1, E2, E8, E9, E10, ---


def test_t1_compound_condition_on_attention_excludes(app, client, survey):
    cond = {"conds": [{"item": "att", "op": "in", "value": "1, 2"}, {"item": 2, "op": "answered", "value": ""}],
            "rule": "all"}
    assert _attention_run(app, client, cond) == "excluded"


def test_t1_uses_ref_reads_every_part(app, survey):
    from app import quality, tempo

    with app.app_context():
        s = db.session.get(Survey, 1)
        s.flow = {"t": "root", "children": [
            {"t": "branch", "id": "b1", "children": [], "rule": "any",
             "cond": {"conds": [{"item": "att", "op": "in", "value": "1"}, {"item": "tempo", "op": "in", "value": "2"}],
                      "rule": "any"}}]}
        assert flow.uses_ref(s, quality.ATTENTION_REF) and flow.uses_ref(s, tempo.REF)
        assert not flow.uses_ref(s, "q:1")


def test_e1_vignette_loop_through_file(app, client, survey):
    from tests.exports.test_transfer import _file, _upload

    _vignette_survey(app, client)
    before = _loops(app, 1)[0]
    assert before["plan"].get("decks_rows")
    assert "ist als Entwurf angelegt" in _upload(client, _file(client), title="Kopie")
    after = _loops(app, 2)[0]
    assert vignettes.is_vignettes(after)
    for key in ("attributes", "prohibitions", "texts"):
        assert after[key] == before[key]
    assert after["plan"]["decks_rows"] == before["plan"]["decks_rows"]
    assert [r["id"] for r in after["rows"]] == [r["id"] for r in before["rows"]]
    with app.app_context():
        s = db.session.get(Survey, 2)
        assert vignettes.plan_current(s, after)
        assert len(loops.choose_rows(s, after, random.Random(1))) == 4  # one deck, not all 12 vignettes


def test_e8_undo_of_deleting_keeps_newer_wording_elsewhere(app, client, survey):
    y = _linked_in_two(app, client)
    client.post("/surveys/1/editor/undo")
    assert _question(app, 2) == "Alte Fassung?" and _question(app, y) == "Neue Fassung?"


def test_e8_restore_from_bin_takes_the_element(app, client, survey):
    from app.models import TrashedItem

    y = _linked_in_two(app, client)
    with app.app_context():
        eid = db.session.scalars(db.select(TrashedItem)).first().id
    client.post(f"/surveys/1/editor/trash/{eid}/restore")
    assert _question(app, 2) == "Neue Fassung?"
    client.post("/surveys/1/editor/items/2", data={**OPTS, "question": "Neue Fassung?", "answer_mode": "required"})
    assert _question(app, y) == "Neue Fassung?"


# --- from tests/audits/test_cr8_b.py: Code review 2026-10-07, package B: SQLite write locks and housekeeping (K2/D1, K3/D2, K4, E4, K8, T8/D6, K9). ---


def test_settings_fingerprint_ignores_the_note(app, client, survey):
    from app import edit_guard

    with app.app_context():
        s = db.session.get(Survey, 1)
        before = edit_guard.settings_state(s, "meta")
        s.retention = {**(s.retention or {}), retention.DONE: "2026-01-01T00:00:00"}
        assert edit_guard.settings_state(s, "meta") == before
        db.session.rollback()


# --- from tests/audits/test_cr8_c.py: Code review 2026-10-07, package C: counters and changes at the same moment (D3, D4, T2, K11, T7, R7, F8, F13, G7, ---


def test_comment_on_an_element_in_a_block_with_a_long_title(app, client, survey):
    add(client, "text_input")
    client.post("/surveys/1/editor/blocks/2", data={"title": "T" * 400})
    with app.app_context():
        assert len(db.session.get(Block, 2).titles["de"]) == 300
    r = client.post("/surveys/1/editor/preview/comment", data={"text": "Tippfehler", "block_id": "2", "item_id": "2"})
    assert r.status_code == 200
    with app.app_context():
        target = db.session.scalar(db.select(PreviewComment.target))
        assert len(target) == 300 and target.startswith("TTT") and target.endswith(" · " + db.session.get(Item, 2).code)


# --- from tests/audits/test_cr8_e.py: Code review 2026-10-07, package E: computing time of results and exports. ---


def test_t4_a_decline_takes_its_counts_away_and_keeps_the_rest(app, client, survey):
    from app import balance

    with app.app_context():
        app.config["BALANCE_SECONDS"] = 15
        s = db.session.get(Survey, 1)
        store = balance._store()
        store[("counts", s.id)] = (10 ** 9, {"rand": {"r1": {"a": 2, "b": 0}}, "loop": {"l1": {"x": 1}}, "version": {"7": {0: 1}}})
        store[("marked", s.id, ("real",))] = (10 ** 9, ("basis", {5}))
        balance.unnote(s, {"conditions": {"r1": ["a", "b"]}, "loops": {"l1": ["x"]}, "cj": {"7": {"version": 0}}})
        table = store[("counts", s.id)][1]
        assert table == {"rand": {"r1": {"a": 1, "b": 0}}, "loop": {"l1": {"x": 0}}, "version": {"7": {0: 0}}}
        assert ("marked", s.id, ("real",)) in store


def test_i14_construct_page_reads_usage_once(app, client, survey, monkeypatch):
    from app import citations, constructs

    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:kusiv3"})
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:tb"})
    calls = []
    real = constructs.usage
    monkeypatch.setattr(constructs, "usage", lambda key: (calls.append(key), real(key))[1])
    assert client.get("/constructs/kusiv3").status_code == 200
    assert calls == ["kusiv3"]
    with app.app_context():
        c = constructs.get("kusiv3")
        assert [o.key for o, n in citations.together(c)] == ["tb"]
        assert [o.key for o, n in citations.together(c, uses=real("kusiv3"))] == ["tb"]


def test_f3_only_the_settings_draw_the_title_anew(app, client, survey):
    add(client, "single_choice")
    assert 'data-live="survey-title"' in client.get("/surveys/1").get_data(as_text=True)
    with app.app_context():
        item = db.session.scalars(db.select(Item).filter_by(type="single_choice")).first().id
    for url in (f"/surveys/1/editor/items/{item}", "/surveys/1/editor/", "/surveys/1/flow/"):
        assert 'data-live="survey-title"' not in client.get(url).get_data(as_text=True), url


# --- from tests/audits/test_cr8_g.py: Code review 2026-10-07, package G "Live, Editor-Skripte, Rechner": F1, F2, F7, F12, F16, F17, F20, F22, F23, F24, ---


def test_f1_copy_chips_are_delegated_outside_the_loop_editor():
    src = _js("app.js")
    loop = src.index("// Loop table editor")
    chips = src.index('closest("[data-copy-text][role=button]")')
    assert chips < loop                                                    # not inside the IIFE that needs [data-loop-editor]
    assert 'document.addEventListener("click"' in src[chips - 2000:loop] and 'document.addEventListener("keydown"' in src[chips - 2000:loop]
    assert 'querySelectorAll("[data-copy-text]").forEach' not in src      # no binding once on page load


def test_f2_empty_required_field_blocks_the_beacon_and_asks_before_undo():
    src = _js("app.js")
    assert "function emptyRequired()" in src
    assert "stuck: function () { return (failed || conflict || !!emptyRequired()) && key(values()) !== saved; }" in src
    assert "if (conflict || failed || emptyRequired()) return;" in src    # last(): nothing goes by beacon


def test_e7_settings_forms_carry_their_guard_key_and_the_new_base_works(app, client, survey):
    html = client.get("/surveys/1").get_data(as_text=True)
    keys = re.findall(r'name="base" value="[0-9a-f]+" data-guard-key="settings:meta"', html)
    assert len(keys) == 3                                                  # Befragung, Teilnahme, Datenschutz
    base = re.findall(r'name="base" value="([0-9a-f]+)"', html)[0]
    d = client.post("/surveys/1/meta", data={"part": "befragung", "autosave": "1", "base": base, "title": "Neuer Titel",
                                             "languages": ["de", "en"], "default_language": "de"}).get_json()
    assert d["status"] == "saved" and d["base"]
    # the script puts d.base into every form with the same key: the other form saves without a conflict
    d2 = client.post("/surveys/1/meta", data={"part": "teilnahme", "autosave": "1", "base": d["base"],
                                              "allow_back": "1", "show_progress": "1"}).get_json()
    assert d2["status"] == "saved"
    with app.app_context():
        assert db.session.get(Survey, 1).allow_back
    src = _js("app.js")
    assert "data-guard-key" in src and 'other.getAttribute("data-guard-key") === guard' in src


def test_f20_emoji_row_needs_no_loop():
    """F20 kept the drifting emoji row from running 60 frames a second for nothing; since 2026-10-16 the emojis wrap
    into further rows (Ralf) and there is no drift loop at all."""
    src = _js("live.js")
    part = src[src.index('var react = document.querySelector("[data-live-react]")'):]
    part = part[:part.index("function sendBurst")]
    assert "requestAnimationFrame" not in part and "scrollLeft" not in part
    css = _js("live.css")  # read from app/static like the scripts
    assert ".live-react { display: flex; flex-wrap: wrap;" in css


# --- from tests/audits/test_cr8_i.py: Code review 2026-10-07, package I "Kleinere Fehler": R6, E3, E5, E6, E12, K5, K6, K7, T6, G11. ---


def test_e5_restore_from_trash_after_dissolve(app, client, survey):
    key = _linked_question__cr8_i(app, client)
    client.post("/surveys/1/editor/items/2/delete")
    client.post(f"/constructs/linked/{key}", data={"action": "dissolve"})
    with app.app_context():
        assert linked.get(key) is None
        eid = db.session.scalars(db.select(TrashedItem)).first().id
    client.post(f"/surveys/1/editor/trash/{eid}/restore")
    with app.app_context():
        item = db.session.get(Item, 2)
        assert linked.key_of(item) is None and "construct" not in item.config and linked.linkable(item) is None


def test_e5_undo_of_the_deletion_after_dissolve(app, client, survey):
    key = _linked_question__cr8_i(app, client)
    client.post("/surveys/1/editor/items/2/delete")
    client.post(f"/constructs/linked/{key}", data={"action": "dissolve"})
    client.post("/surveys/1/editor/undo")
    with app.app_context():
        item = db.session.get(Item, 2)
        assert linked.key_of(item) == key and linked.get(key) is not None   # the step holds the element: it comes back


def test_e5_undo_step_without_its_element_puts_the_question_back_unlinked(app):
    from app import undo

    with app.app_context():
        target = {"item:2": {"type": "single_choice", "code": "q", "answer_mode": "optional", "texts": {},
                             "config": {"construct": {"key": "v-gone", "version": 1}, "layout": "vertical"}},
                  "item:3": {"type": "single_choice", "code": "r", "answer_mode": "optional", "texts": {},
                             "config": {"construct": {"key": "v-back", "version": 1}}},
                  "linked:v-back": {"name": "B"}}
        assert undo._gone_elements(target)
        assert target["item:2"]["config"] == {"layout": "vertical"}
        assert target["item:3"]["config"]["construct"]["key"] == "v-back"   # the step makes it anew
        assert not undo._gone_elements(target)


def test_e5_unlink_without_element(app, client, survey):
    key = _linked_question__cr8_i(app, client)
    with app.app_context():
        c = linked.get(key)
        db.session.delete(c)  # gone some other way: the question still names it
        db.session.commit()
    client.post("/surveys/1/editor/items/2/unlink")
    with app.app_context():
        assert linked.key_of(db.session.get(Item, 2)) is None


def test_e6_undo_of_apply_design_with_deleted_logo_is_refused(app, client, survey):
    from app import theme
    from app.models import Design
    from tests.ui.test_theme_logo import png, upload

    upload(client, png())
    with app.app_context():
        s = db.session.get(Survey, 1)
        logo = theme.clean(s.theme)["logo"]["file"]
        folder = os.path.join(app.config["UPLOAD_FOLDER"], s.uid)
    client.post("/surveys/new", data={"title": "Zwei", "languages": ["de"], "default_language": "de"})
    client.post("/surveys/2/design/save", data={"name": "Schlicht"})
    with app.app_context():
        did = db.session.scalars(db.select(Design)).first().id
    client.post("/surveys/1/design/apply", data={"design": str(did)})
    assert not os.path.isfile(os.path.join(folder, logo))          # gone once the change was committed
    client.post("/surveys/1/editor/undo")
    with app.app_context():
        back = (theme.clean(db.session.get(Survey, 1).theme) or {}).get("logo") or {}
        assert back.get("file") != logo                              # no broken image on the participant pages


def test_g11_pages_with_empty_configs(app, client, survey):
    from app import experimental

    from tests.conftest import experiments_on

    experiments_on(client, *[f[0] for f in experimental.FEATURES])
    for t in TYPES:
        add(client, t)
    fill_questions(app)
    with app.app_context():
        ids = [i.id for b in db.session.get(Survey, 1).blocks for i in b.items if i.type in TYPES]
        for iid in ids:
            db.session.get(Item, iid).config = {}
        db.session.commit()
    assert len(ids) == len(TYPES)
    for iid in ids:
        for url in (f"/surveys/1/editor/items/{iid}", f"/surveys/1/editor/items/{iid}/frame"):
            assert client.get(url).status_code < 500, url
    for url in ("/surveys/1/editor", "/surveys/1/results", "/surveys/1/data", "/surveys/1/methods",
                "/surveys/1/editor/preview"):
        assert client.get(url).status_code < 500, url


# --- from tests/audits/test_review_fixes.py: What the code review of 2026-10-02 found and what holds since (code-review-2026-10-02.md, S1 to S7). ---


def test_flow_page_keeps_the_consent_out_of_groups(app, client, survey):
    _consent_and_two_blocks(client)
    for kind in ("rand", "branch", "loop"):
        client.post(E__review_fixes + "/flow/add", data={"kind": kind})
    with app.app_context():
        s = db.session.get(Survey, 1)
        groups = [flow.walk_nodes(s, kind)[0]["id"] for kind in ("rand", "branch", "loop")]
    client.post(E__review_fixes + f"/flow/{groups[0]}/place", data={"target": "k2", "mode": "before"})
    client.post(E__review_fixes + "/flow/k2/place", data={"target": groups[0], "mode": "into"})
    for gid in groups:
        client.post(E__review_fixes + "/flow/k1/place", data={"target": gid, "mode": "into"})  # into a group
    client.post(E__review_fixes + "/flow/k1/place", data={"target": "k2", "mode": "after"})    # next to a block inside a group
    client.post(E__review_fixes + f"/flow/{groups[0]}/place", data={"target": "k1", "mode": "before"})
    client.post(E__review_fixes + "/flow/k1/in")                                                # into the group before it
    client.post(E__review_fixes + "/flow/add", data={"kind": "rand", "target": "k1", "mode": "wrap"})  # a new group around it
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert flow.ancestors(flow.tree(s), "k1") == []
        assert flow.ancestors(flow.tree(s), "k2") != []  # other blocks still go into groups
        assert len(flow.walk_nodes(s, "rand")) == 1
    assert not [m for m in _errors__review_fixes(app) if "Einwilligung steht" in m]


def test_design_check_blocks_the_start_with_the_consent_in_a_group(app, client, survey):
    _consent_and_two_blocks(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        split = split_node(s)
        s.flow = {"t": "root", "children": [
            {"t": "rand", "id": "r1", "code": "cond1", "name": "R", "k": 1, "balanced": True, "shuffle": False,
             "children": [{"t": "block", "block": 1}, {"t": "block", "block": 2}]},
            {"t": "block", "block": 3}, split]}
        db.session.commit()
    assert [m for m in _errors__review_fixes(app) if "Einwilligung steht in einer Randomisierung" in m]
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        assert db.session.get(Survey, 1).status == Survey.DRAFT


def test_only_admins_set_the_completion_link_of_an_end(app, client, survey):
    """M2: where participants are sent is decided by the admins, in the flow as in the settings."""
    fill_consent(client)
    add(client, "text_input")
    client.post(E__review_fixes + "/flow/add", data={"kind": "end"})
    with app.app_context():
        nid = flow.walk_nodes(db.session.get(Survey, 1), "end")[0]["id"]

    def end():
        with app.app_context():
            return dict(flow.find(flow.tree(db.session.get(Survey, 1)), nid)[0])

    client.post(E__review_fixes + f"/flow/{nid}/update", data={"kind": "screenout", "link": "https://panel.example/off", "link_mode": "button"})
    assert (end()["link"], end()["link_mode"]) == ("https://panel.example/off", "button")
    editor = _member__review_fixes(app, "editor")
    page = editor.get("/surveys/1/flow/").data.decode()             # the end shows its trigger as a card
    assert 'name="case0_link" value="https://panel.example/off" placeholder="https://…" maxlength="1000" disabled' in page
    assert 'name="case0_link_mode" disabled' in page and "Abschlusslinks legt fest, wer in dieser Befragung die Rolle „Admin“" in page
    # the page of an editor sends no link fields (they are disabled): everything else is saved, the link stays
    editor.post(E__review_fixes + f"/flow/{nid}/update", data={"kind": "excluded", "text_de": "Schade."})
    assert (end()["kind"], end()["text"]["de"], end()["link"], end()["link_mode"]) == \
        ("excluded", "Schade.", "https://panel.example/off", "button")
    # a request that sends them anyway changes nothing
    editor.post(E__review_fixes + f"/flow/{nid}/update", data={"kind": "screenout", "link": "https://evil.example/collect", "link_mode": "now"})
    assert (end()["kind"], end()["link"], end()["link_mode"]) == ("excluded", "https://panel.example/off", "button")
    # the same with the triggers of an end, as the page sends them: an editor changes the trigger and its text, adds
    # one without a link; the link of the trigger that was there stays, and a link sent anyway saves nothing
    editor.post(E__review_fixes + f"/flow/{nid}/update", data=MultiDict([
        ("case", "0"), ("case0_id", "c0"), ("case0_when", "screenout"), ("case0_text_de", "Leider nein."),
        ("case", "1"), ("case1_when", "excluded")]))
    found = end()["cases"]
    assert [(c["when"], c["link"], c["link_mode"]) for c in found] == [("screenout", "https://panel.example/off", "button"),
                                                                       ("excluded", "", "now")]
    assert found[0]["text"]["de"] == "Leider nein." and "link" not in end()
    r = editor.post(E__review_fixes + f"/flow/{nid}/update", follow_redirects=True, data=MultiDict([
        ("case", "0"), ("case0_id", found[0]["id"]), ("case0_when", "excluded"), ("case0_link", "https://evil.example/collect")]))
    assert "Abschlusslinks legt fest, wer in dieser Befragung die Rolle „Admin“ hat" in r.data.decode()
    assert [(c["when"], c["link"]) for c in end()["cases"]] == [("screenout", "https://panel.example/off"), ("excluded", "")]
    client.post(E__review_fixes + f"/flow/{nid}/update", data=MultiDict([                      # the admin sets it
        ("case", "0"), ("case0_id", found[0]["id"]), ("case0_when", "screenout"), ("case0_link", "https://panel.example/new"),
        ("case0_link_mode", "auto")]))
    assert [(c["link"], c["link_mode"]) for c in end()["cases"]] == [("https://panel.example/new", "auto")]


def test_condition_never_reads_a_question_of_another_survey(app, client, survey):
    """M3: question ids count on across surveys; a condition must not name the question of someone else."""
    from app import conditions
    from app.models import SurveyMember

    add(client, "single_choice")                         # item 2 in survey 1
    client.post(E__review_fixes + "/items/2", data=MultiDict([
        ("code", "geheim"), ("question", "Geheime Frage"), ("option_id", "a"), ("option_code", "1"),
        ("option_label", "vertrauliche Antwort A"), ("option_id", "b"), ("option_code", "2"), ("option_label", "B")]))
    client.post("/surveys/new", data={"title": "Zwei", "languages": ["de"], "default_language": "de"})   # survey 2
    with app.app_context():
        db.session.add(SurveyMember(survey_id=2, orcid=OTHER__review_fixes, name="Fremde", role="editor", position=1))
        db.session.commit()
    other = app.test_client()
    other.get(f"/auth/dev-login?orcid={OTHER__review_fixes}&name=Fremde")
    other.post("/terms/", data={"accept": "1"})
    assert other.get("/surveys/1/editor/").status_code == 404
    other.post("/surveys/2/editor/flow/add", data={"kind": "branch"})
    with app.app_context():
        bid = flow.walk_nodes(db.session.get(Survey, 2), "branch")[0]["id"]
    for data in ({"item": "2", "op": "eq", "value": "9"},                                    # the stored form
                 {"item": "2", "raw": '{"item": 2, "op": "eq", "value": "9"}'}):            # the field of the page
        other.post(f"/surveys/2/editor/flow/{bid}/update", data=data)
        for page in ("/surveys/2/flow/", "/surveys/2/editor/"):
            html = other.get(page).data.decode()
            assert "geheim" not in html and "vertrauliche Antwort" not in html
    with app.app_context():
        assert conditions.text(db.session.get(Survey, 2), {"item": 2, "op": "eq", "value": "1"}) == "Bedingung unvollständig"
        assert conditions.text(db.session.get(Survey, 1), {"item": 2, "op": "eq", "value": "1"}) == "geheim: vertrauliche Antwort A"


# --- from tests/audits/test_review_medium.py: The medium findings of the code review of 2026-10-02 (code-review-2026-10-02.md, M5 to M16). ---


def test_conditions_and_cells_on_a_question_in_a_loop_are_errors(app, client, survey):
    test_quotas.questionnaire(client)
    client.post(test_quotas.Q + "/add")
    client.post(test_quotas.Q + "/1", data=test_quotas.cells_form([("Maenner", "m", 2, [(2, "eq", "1")])]))
    with app.app_context():
        assert "2" in conditions.describe(db.session.get(Survey, 1), everything=True)
    client.post(E__review_medium + "/flow/add", data={"kind": "branch"})
    with app.app_context():
        bid = flow.walk_nodes(db.session.get(Survey, 1), "branch")[0]["id"]
    client.post(E__review_medium + f"/flow/{bid}/update", data={"item": "2", "op": "eq", "value": "1"})
    _loop_around_block_2(app)
    with app.app_context():
        assert "2" not in conditions.describe(db.session.get(Survey, 1), everything=True)  # no longer offered
    errors = _errors__review_medium(app)
    assert [m for m in errors if "Teilquote „Maenner“" in m and "in einer Schleife" in m]
    assert [m for m in errors if m.startswith("Bedingung") and "Frage in einer Schleife" in m]


def test_screen_out_before_its_question_is_an_error(app, client, survey):
    fill_consent(client)
    add(client, "single_choice")                         # 2
    client.post(E__review_medium + "/items/2", data=MultiDict([
        ("code", "age"), ("question", "Alt genug?"), ("answer_mode", "required"),
        ("option_id", "a"), ("option_code", "1"), ("option_label", "ja"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "nein")]))
    client.post(E__review_medium + "/flow/add", data={"kind": "branch"})
    with app.app_context():
        bid = flow.walk_nodes(db.session.get(Survey, 1), "branch")[0]["id"]
    client.post(E__review_medium + f"/flow/{bid}/update", data={"item": "2", "op": "eq", "value": "2"})
    client.post(E__review_medium + "/flow/add", data={"kind": "end"})
    with app.app_context():
        eid = flow.walk_nodes(db.session.get(Survey, 1), "end")[0]["id"]
    client.post(E__review_medium + f"/flow/{eid}/place", data={"target": bid, "mode": "into"})
    assert not [m for m in _errors__review_medium(app) if "erst danach kommt" in m]          # behind its question: fine
    client.post(E__review_medium + f"/flow/{bid}/place", data={"target": "k2", "mode": "before"})
    assert [m for m in _errors__review_medium(app) if "erst danach kommt" in m]              # a condition that holds only an end


def test_an_end_before_the_first_page_ends_without_a_redirect_loop(app, client, survey):
    url = _with_parameter(app, client, [
        {"t": "branch", "id": "b1", "cond": {"item": "p:grp", "op": "eq", "value": "x"},
         "children": [{"t": "end", "id": "e1", "text": {"de": "**Nicht für Sie.**"}, "kind": "screenout"}]},
        {"t": "block", "block": 1}, {"t": "block", "block": 2}, {"t": "block", "block": 3}])
    page = app.test_client().get(url + "?grp=x")
    assert page.status_code == 200 and "Nicht für Sie" in page.data.decode()
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id)).filter_by(is_preview=False)) == 0
    assert "i1_c_voluntary" in app.test_client().get(url).data.decode()  # everybody else starts with the consent


def test_a_first_block_for_some_only(app, client, survey):
    url = _with_parameter(app, client, [
        {"t": "branch", "id": "b1", "cond": {"item": "p:grp", "op": "eq", "value": "x"},
         "children": [{"t": "block", "block": 3}]},
        {"t": "block", "block": 1}, {"t": "block", "block": 2}])
    p = app.test_client()
    page = p.get(url)                                         # without the parameter: block 3 is skipped
    assert page.status_code == 200 and "i1_c_voluntary" in page.data.decode()
    p.post(url, data={**CONSENT_OK, "pos": page_pos(p, 1)})            # the first send is the consent, not lost
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(is_preview=False))
        assert r.answers.get("1") == "1" and r.position == 2
    assert "Hinweis für das Panel" in app.test_client().get(url + "?grp=x").data.decode()


def test_import_takes_the_texts_the_editor_writes(app, client, survey):
    add(client, "single_choice")
    client.post(E__review_medium + "/items/2", data=MultiDict([("code", "wahl"), ("question", "Was?"), ("option_id", "a"),
                                                ("option_code", "1"), ("option_label", "ja")]))
    _upload(client, _file(client))
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Survey.id))) == 2
