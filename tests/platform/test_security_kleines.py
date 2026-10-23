"""Small findings of the security audit 2026-10-03 (package 5): SPSS quoting (S11), placeholders in typed text of
carried options (S12), "Alle Bilder freigeben" without images (S13), formula protection in two CSV files (S14),
validators with a newline at the end (H5), placeholder marks in attributes (H6), rich() headings (H7) and the
Zenodo description (H8). The complaint limits (S6) are in tests/platform/test_complaints.py."""
import re

from app.extensions import db
from app.models import Survey
from tests.conftest import add


def test_sps_quote_cuts_before_doubling_and_drops_control_characters():
    from app.export import _sps_quote

    text = "a" * 249 + "'" + "b" * 10
    out = _sps_quote(text)
    assert out == "'" + "a" * 249 + "''" + "'"  # the quote at 250 stays doubled, the string is closed
    body = out[1:-1]
    assert not re.search(r"(?<!')'(?!')", body.replace("''", ""))  # no single quote left inside
    assert _sps_quote("eins\r\nzwei\tdrei\x0bvier" + chr(0x2028) + "fünf") == "'eins  zwei drei vier fünf'"
    assert _sps_quote("'" * 300) == "'" + "''" * 250 + "'"
    assert _sps_quote(None) == "''"


def test_typed_text_of_a_carried_option_keeps_no_placeholder():
    from app import carry
    from app.items.base import PLACEHOLDER

    row = {"src": {"item": "2", "code": "9", "text": True}}
    for typed in ("{{{pid}}}", "{{pid}}", "{{{{pid}}}}", "x{{pid|y}}"):
        out = carry.label(row, {"2": {"v": ["9"], "t": {"9": typed}}}, "Anderes")
        assert "{{" not in out and not PLACEHOLDER.search(out), (typed, out)
    assert carry.label(row, {"2": {"v": ["9"], "t": {"9": "E-Reader"}}}, "Anderes") == "E-Reader"


def test_approve_all_images_of_an_item_without_images_is_404(app, client, survey):
    add(client, "single_choice")
    assert client.post("/surveys/1/editor/items/2/images/approve-all").status_code == 404


def test_quota_report_and_construct_pool_have_formula_protection(app, client, survey, monkeypatch):
    from app import constructs, quotas

    monkeypatch.setattr(quotas, "report_table", lambda *a, **k: [["Quote", "Teilquote"], ["=HYPERLINK(\"x\")", "-3"]])
    with app.app_context():
        text = quotas.report_text(db.session.get(Survey, 1), sums=[("@start", 1)])
    assert "'=HYPERLINK" in text and "\t-3" in text and "'@start" in text
    monkeypatch.setattr(constructs, "pool", lambda key: (["a", "b"], [["=1+1", -2], [None, "+x"]]))
    body = client.get("/constructs/bfi10/pool.csv").data.decode("utf-8-sig").splitlines()
    assert body == ["a,b", "'=1+1,-2", ",'+x"]


def test_validators_refuse_a_newline_at_the_end():
    from app import external, flow, panel, quotas
    from app.items import base

    for pattern in (base.CODE_RE, flow.CODE_RE, quotas.CODE_RE, external.NAME_RE, panel.NAME_RE):
        assert pattern.match("Q1") and not pattern.match("Q1\n"), pattern.pattern
    assert not base.HEX_RE.match("#aabbcc\n") and not base.ID_RE.match("a\n")


def test_import_strips_codes(app, client, survey):
    from app import transfer
    from app.models import User

    add(client, "single_choice")
    with app.app_context():
        data = transfer.dump(db.session.get(Survey, 1), with_files=False)
    item = next(i for b in data["blocks"] for i in b["items"] if i["type"] == "single_choice")
    item["code"] = " frage1\n"
    with app.test_request_context():
        new, _ = transfer.create(data, db.session.get(User, 1))
        codes = [i.code for b in new.blocks for i in b.items]
    assert "frage1" in codes and not [c for c in codes if c != c.strip()]


def test_unknown_placeholders_leave_attributes_whole(app):
    from pathlib import Path

    with app.app_context():
        tpl = app.jinja_env.from_string('<i aria-label="{{ l|fill(v)|striptags }}">')
        assert tpl.render(l="sehr {{nope}} gut", v={}) == '<i aria-label="sehr {{nope}} gut">'
    folder = Path(app.root_path) / "templates" / "items"
    for name in ("likert", "semantic"):
        for line in (folder / name / "participant.html").read_text(encoding="utf-8").splitlines():
            for attr in re.findall(r'aria-label="([^"]*)"', line):
                assert "|fill" not in attr or attr.count("|fill") == attr.count("|striptags"), line


def test_rich_heading_is_built_from_its_inner_text():
    from app.items.base import rich

    assert str(rich("**Teil 1**\nText")) == "<h3>Teil 1</h3><p>Text</p>"
    assert str(rich("**a** und **b**")) == "<p><strong>a</strong> und <strong>b</strong></p>"
    assert str(rich("**<b>**")) == "<h3><b></b></h3>"


def test_zenodo_description_is_escaped(app, client, survey):
    from app import zenodo

    with app.app_context():
        s = db.session.get(Survey, 1)
        s.description = "Zeile <script>x</script>\nzwei & drei"
        meta = zenodo.metadata(s)
    assert meta["description"].startswith("<p>Zeile &lt;script&gt;x&lt;/script&gt;<br>zwei &amp; drei</p>")
