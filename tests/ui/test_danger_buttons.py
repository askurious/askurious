"""What cannot be undone looks dangerous before it is clicked, not only in the question afterwards: a form whose
question is red (data-confirm-danger) has a red button (button-danger, icon-btn danger). Closing a survey can be
undone ("Datenerhebung fortsetzen") and is therefore asked, but not red. UI/UX audit 2026-10-16, finding 4."""
import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parents[2] / "app" / "templates"
DANGER_FORM = re.compile(r"<form\b[^>]*data-confirm-danger[^>]*>.*?</form>", re.S)


def test_forms_asking_in_red_have_a_red_button():
    grey = []
    for path in TEMPLATES.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        for m in DANGER_FORM.finditer(text):
            form = m.group(0)
            if "set_role" in form or "set_member_role" in form:  # changing one's own role: asked in red only for oneself, the button stays a role picker
                continue
            classes = re.findall(r'<button\b[^>]*class="([^"]*)"', form)
            if classes and not any("danger" in c for c in classes):
                grey.append(f"{path.relative_to(TEMPLATES)}:{text[:m.start()].count(chr(10)) + 1}")
    assert not grey, "red question, grey button: " + ", ".join(grey)


def test_closing_a_survey_is_not_red():
    status = (TEMPLATES / "surveys" / "_status.html").read_text(encoding="utf-8")
    for m in re.finditer(r"<form\b[^>]*surveys\.close[^>]*>.*?</form>", status, re.S):
        assert "button-danger" not in m.group(0) and "data-confirm-danger" not in m.group(0)
