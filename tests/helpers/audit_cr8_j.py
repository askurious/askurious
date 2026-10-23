"""Helpers of the tests from test_cr8_j.py; the tests themselves are in tests/analysis/test_analysis_audit_findings.py, tests/dashboards/test_dashboards_audit_findings.py, tests/items/test_items_audit_findings.py, tests/live/test_live_audit_findings.py, tests/platform/test_platform_audit_findings.py, tests/ui/test_ui_audit_findings.py (sorted by area, architecture concept 2026-10-12, G).

Code review 2026-10-07, package J (tidying up): dead code, dead templates and CSS, one counter for the limits.
"""
from tests.static_sources import participant_js

import inspect


import re


from pathlib import Path


from app import limits, panel, study_plan


from app.items import attributes


ROOT = Path(__file__).resolve().parents[2]


STATIC = ROOT / "app" / "static"


TEMPLATES = ROOT / "app" / "templates"


def read(name):
    if name == "participant.js":
        return participant_js()
    return (STATIC / name).read_text(encoding="utf-8")


def all_app_text(suffixes=(".html", ".py", ".js", ".json", ".svg")):
    return "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in (ROOT / "app").rglob("*")
                     if p.is_file() and p.suffix in suffixes and "vendor" not in p.parts)


DEAD_CLASSES = ("contact-safeguards", "account-new", "style-contexts", "loop-badge", "wizard-head", "consent-preview",
                "panel-links", "palette-section", "page-subtitle", "landing-owner", "split-empty", "split-add",
                "inline-label", "group-form", "link-rule-item", "link-rule-value", "live-first-icon", "results-link",
                "ainstein-box-foot", "ainstein-box-form", "ainstein-proposal", "ainstein-proposal-actions",
                "ainstein-proposal-text", "ainstein-step-actions", "ainstein-sync")


def _rules(css):
    """(media context, selector, declarations) of the rules of a style sheet, comments left out."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out, stack, i, start = [], [], 0, 0
    while i < len(css):
        c = css[i]
        if c == "{":
            head = " ".join(css[start:i].split())
            if head.startswith("@"):
                stack.append(head)
            else:
                j = css.index("}", i)
                decls = {}
                for d in css[i + 1:j].split(";"):
                    if ":" in d:
                        k, v = d.split(":", 1)
                        decls[k.strip()] = " ".join(v.split())
                out.append((tuple(stack), head, decls))
                i = j
            start = i + 1
        elif c == "}":
            if stack:
                stack.pop()
            start = i + 1
        i += 1
    return out
