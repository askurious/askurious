"""Every element type has its symbol in the list of the editor (Ralf, 2026-10-06: voice, IAT and delay discounting
had none)."""
import re
from pathlib import Path

from app.items import REGISTRY

# not offered in the list of elements: no symbol needed
NOT_IN_PALETTE = {"consent", "iat_feedback", "page_break"}


def test_every_element_type_has_a_symbol():
    icons = Path(__file__).resolve().parents[2].joinpath("app", "templates", "_icons.html").read_text(encoding="utf-8")
    known = set(re.findall(r'^\s*"([a-z0-9_-]+)":', icons, re.M))
    missing = sorted(set(REGISTRY) - known - NOT_IN_PALETTE)
    assert not missing, missing
