"""Concepts and audit reports never go into the repository (Ralf, 2026-10-19; CLAUDE.md, "No concepts or audit
reports in the repository"): they live in the project's library. The code may name them in comments; the files
themselves stay out, so that the public code carries no internal plans or lists of weaknesses."""
import os
import re
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
# documents (not code) whose name says concept, audit, review or remediation, and the former folder concepts/
DOCUMENT = re.compile(r"\.(md|json|docx|pdf|txt|html|xlsx|pptx)$", re.I)
NAME = re.compile(r"konzept|concept|audit|pruefung|prüfung|review|remediation|rights-policy", re.I)


def offending(paths):
    return [p for p in paths
            if p.startswith("concepts/")
            or (DOCUMENT.search(p) and NAME.search(os.path.basename(p))
                and not p.startswith(("app/", "tests/", "migrations/")))]


def test_no_concepts_or_audit_reports_tracked():
    tracked = subprocess.run(["git", "-C", ROOT, "ls-files"], capture_output=True, text=True, check=True).stdout
    assert offending(tracked.splitlines()) == []


def test_the_rule_catches_what_it_should():
    assert offending(["konzept-merkle-zeitstempel-2026-10-16.md", "concepts/x.md", "docs/audit-sp13-c.md",
                      "docs/license-remediation-2026-10-17.json", "pruefung-urheberrecht.md", "docs/code-review.pdf"]) \
        == ["konzept-merkle-zeitstempel-2026-10-16.md", "concepts/x.md", "docs/audit-sp13-c.md",
            "docs/license-remediation-2026-10-17.json", "pruefung-urheberrecht.md", "docs/code-review.pdf"]
    assert offending(["app/privacy_concept.py", "app/templates/items/text_review.html", "docs/glossar.md",
                      "README.md", "tests/audits/test_audit_2026_10_12_c.py"]) == []
