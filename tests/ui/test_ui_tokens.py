"""Colours, radii and sizes of the researchers' pages (UI/UX audit of 2026-10-04, package H, R78 to R86)."""
import glob
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "app"
CSS = (ROOT / "static" / "app.css").read_text(encoding="utf-8")


def rule(selector):
    return re.search(re.escape(selector) + r" \{([^}]*)\}", CSS).group(1)


def test_radius_sizes_and_no_pills():
    """R81 to R83: radius 8 for controls, 12 for panels, 6 for badges and chips; no pills (the switch track of the
    safety latch is gone, Ralf 2026-10-05);
    small buttons 30 px, fields 42 px."""
    assert "--radius: 8px;" in CSS and "--radius-panel: 12px;" in CSS and "--radius-tag: 6px;" in CSS
    pills = [line for line in CSS.splitlines() if re.search(r"border-radius: 99+px", line)]
    assert pills == []
    assert "height: 30px" in rule(".button-small") and "--field-h: 42px;" in CSS
    participant = (ROOT / "static" / "live.css").read_text(encoding="utf-8")
    for sel in (".live-release", ".live-qa-vote"):
        assert "border-radius: 8px" in re.search(re.escape(sel) + r" \{([^}]*)\}", participant).group(1)


def test_red_only_for_errors_and_deleting():
    """R78: an end of the flow is a regular step; flagged speeders and negative values are "check this"
    (--caution), not errors; no colour falls back to a fixed value beside its token."""
    assert "--danger" not in rule(".fl-node.fl-end")
    assert "var(--caution)" in rule(".speed-bar.is-speeder") and "#d9822b" not in CSS
    assert not re.search(r"var\(--[a-z0-9-]+, *#", CSS)


def test_one_class_for_deleting():
    """R80: the button that asks is neutral with a bin; the final one is .button-danger, in the dialog too."""
    assert "danger-button" not in CSS and "is-danger" not in CSS
    for path in glob.glob(str(ROOT / "templates" / "**" / "*.html"), recursive=True):
        assert "danger-button" not in Path(path).read_text(encoding="utf-8"), path
    js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert 'yes.classList.toggle("button-danger"' in js


def test_sections_fold_with_the_chevron_on_the_left():
    """R86: the results fold like the settings, the chevron before the heading."""
    html = (ROOT / "templates" / "surveys" / "results.html").read_text(encoding="utf-8")
    assert "area-caret" not in html and '<summary><span class="settings-caret"' in html


def test_font_sizes_in_six_steps_and_one_row_kit():
    """R84: rem font sizes only in the six steps (larger ones only for display numbers); R92: every row list can be
    dragged and moved with ↑ ↓ (the rows of the element editors, the elements and Themenblöcke of a guide); R86: the
    loop folds its settings like the other elements of the flow."""
    sizes = {float(v) for v in re.findall(r"font-size: ([0-9.]+)rem", CSS)}
    assert {s for s in sizes if s < 2} <= {0.75, 0.85, 0.95, 1.05, 1.35, 1.55}
    js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert 'handle.className = "drag-handle row-drag"' in js and ".guide-block-drag" in js
    guide = (ROOT / "templates" / "interview" / "guide.html").read_text(encoding="utf-8")
    assert "editor.move_item" in guide and "guide-block-drag" in guide
    flow = (ROOT / "templates" / "flow" / "index.html").read_text(encoding="utf-8")
    assert "Tabelle &amp; Einstellungen" not in flow and "Tabelle und Auswahl öffnen" in flow
