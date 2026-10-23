"""The RWTH design files (tests/data/rwth-*.askurious-design.json), the same values as the built-in RWTH templates had
until 2026-10-19 (konzept-org-designs-2026-10-19.md): the RWTH look is an organisation design now."""
import json
import os

from app import theme

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def file(mode="light"):
    """The design file as a dict."""
    name = "rwth-hell" if mode == "light" else "rwth-dunkel"
    with open(os.path.join(DATA, f"{name}.askurious-design.json"), encoding="utf-8") as f:
        return json.load(f)


def look(mode="light"):
    """The organisation design of the file, as a theme (preset "org")."""
    return theme.read_design_file(file(mode))


def pair():
    """Light with dark for "automatisch", as a survey gets it from an organisation with both."""
    return theme.org_pair(look("light"), look("dark"))


COLOURS = file("light")["colors"]
DARK_COLOURS = file("dark")["colors"]
