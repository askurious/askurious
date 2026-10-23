"""Every field of the researcher pages has a name of its own (WCAG 1.3.1, 4.1.2). A <label> without `for` names
only the first control inside it, so a second field in the same label (the date after the tick "Feldende am", the
number after "nach") needs aria-label or aria-labelledby. UI/UX audit 2026-10-16, finding 24."""
import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parents[2] / "app" / "templates"
CONTROL = re.compile(r"<(input|select|textarea)\b([^>]*)>", re.S)
LABEL = re.compile(r"<label\b(?![^>]*\bfor=)[^>]*>(.*?)</label>", re.S)
# the branches after {% else %} / {% elif %} are not rendered together with the first one
OTHER_BRANCH = re.compile(r"{%-?\s*(?:else|elif\b[^%]*)\s*-?%}.*?{%-?\s*endif\s*-?%}", re.S)


def controls(label_body):
    out = []
    for m in CONTROL.finditer(OTHER_BRANCH.sub("", label_body)):
        attrs = m.group(2)
        if re.search(r'type="hidden"', attrs):
            continue
        out.append(attrs)
    return out


def test_second_field_in_a_label_has_its_own_name():
    unnamed = []
    for path in TEMPLATES.rglob("*.html"):
        if "participant" in path.parts:
            continue  # the participants' pages have their own rules (tests/ui/test_accessibility_rules.py)
        text = path.read_text(encoding="utf-8")
        for m in LABEL.finditer(text):
            for attrs in controls(m.group(1))[1:]:
                if "aria-label" not in attrs:
                    line = text[:m.start()].count("\n") + 1
                    unnamed.append(f"{path.relative_to(TEMPLATES)}:{line}")
    assert not unnamed, "second field in a <label> without aria-label: " + ", ".join(sorted(set(unnamed)))
