"""Icons from one static file and one form per editor row (audit 2026-10-22, G-05).

The editor overview of a survey with 66 elements was 577 KB of uncompressed HTML: every icon was a whole inline SVG
(717 of them, 184 KB), and every row carried six forms with six CSRF tokens. The icons now come from
app/static/icons.svg (tools/icon_sprite.py), and the tools of a row are one form with an address per button."""
import importlib.util
import re
from pathlib import Path

from app.extensions import db
from app.models import Block, Item, Survey

ROOT = Path(__file__).resolve().parents[2]


def _tool():
    spec = importlib.util.spec_from_file_location("icon_sprite", ROOT / "tools/icon_sprite.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sprite_is_what_the_tool_writes():
    tool = _tool()
    written = (ROOT / "app/static/icons.svg").read_text(encoding="utf-8")
    assert written == tool.sprite(), "app/static/icons.svg is stale: run python tools/icon_sprite.py"
    names = tool.icons()
    assert len(names) > 100 and all(f'<symbol id="{n}" ' in written for n in names)


def test_icon_refers_to_the_sprite_and_draws_inline_only_on_request(app):
    with app.test_request_context("/"):
        module = app.jinja_env.get_template("_icons.html").make_module()
        used = str(module.icon("plus", 16))
        inline = str(module.icon("plus", inline=True))
    assert re.fullmatch(r'<svg class="icon" width="16" height="16" aria-hidden="true">'
                        r'<use href="/static/icons\.svg\?v=[0-9a-f]{12}#plus"/></svg>', used.strip()), used
    assert '<path d="M12 5v14M5 12h14"/>' in inline and "<use" not in inline


def test_sprite_is_served_as_svg_and_cached(client):
    page = client.get("/dashboard/").data.decode()
    href = re.search(r'<use href="(/static/icons\.svg\?v=[0-9a-f]{12})#', page).group(1)
    r = client.get(href)
    assert r.status_code == 200 and r.mimetype == "image/svg+xml" and r.cache_control.max_age > 86400


def _many_elements(app, n=66):
    with app.app_context():
        survey = db.session.get(Survey, 1)
        blocks = [Block(survey_id=survey.id, position=k + 1, titles={"de": f"Block {k}"}) for k in range(6)]
        db.session.add_all(blocks)
        db.session.flush()
        for k in range(n):
            db.session.add(Item(block_id=blocks[k % 6].id, type="text_input", code=f"t{k}", position=k,
                                config={"validation": "none", "multiline": False},
                                texts={"de": {"question": f"Frage {k}?"}}))
        db.session.commit()


def test_editor_overview_of_66_elements_stays_small(app, client):
    client.post("/surveys/new", data={"title": "Gross"})
    _many_elements(app)
    page = client.get("/surveys/1/editor/").data.decode()
    assert page.count('class="item-row') >= 66
    assert page.count('<svg class="icon"') > 300 and '<svg class="icon" width="18" height="18" viewBox' not in page  # none inline
    # one form and one CSRF token per row, the tools on formaction
    rows = re.findall(r'<li class="item-row[^"]*" id="item-\d+" data-item-id="\d+" data-drag>.*?</li>', page, re.S)
    assert len(rows) == 66
    assert all(r.count("<form") == 1 and r.count('name="csrf_token"') == 1 and r.count("formaction=") >= 4 for r in rows)
    # 532 KB before (66 plain text questions in six blocks), 386 KB after; the panel template of the audit went from
    # 577 KB to 424 KB
    assert len(page.encode()) < 410_000, len(page.encode())
