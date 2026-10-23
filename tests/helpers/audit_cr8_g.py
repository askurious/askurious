"""Helpers of the tests from test_cr8_g.py; the tests themselves are in tests/accounts/test_accounts_audit_findings.py, tests/analysis/test_analysis_audit_findings.py, tests/editor/test_editor_audit_findings.py, tests/interview/test_interview_audit_findings.py, tests/items/test_items_audit_findings.py, tests/live/test_live_audit_findings.py, tests/participation/test_participation_audit_findings.py (sorted by area, architecture concept 2026-10-12, G).

Code review 2026-10-07, package G "Live, Editor-Skripte, Rechner": F1, F2, F7, F12, F16, F17, F20, F22, F23, F24,
G3, G4, G5 and E7. The scripts run in the browser; where the server takes part the test goes through it, else it
checks the script for the guard that fixes the finding (and runs it under Node where it can).
"""
from tests.static_sources import participant_js

import json


import re


import shutil


import subprocess


from pathlib import Path


import pytest


from app import study_plan


from app.extensions import db


from app.models import Survey


from tests.helpers.audit_audit_2026_10_06_a import _member, _person


from tests.live.test_live import add as live_add, make as live_make


STATIC = Path(__file__).resolve().parents[2] / "app" / "static"


NODE = shutil.which("node")


OTHER = "0000-0002-1825-0097"


def _js(name):
    if name == "participant.js":
        return participant_js()
    return (STATIC / name).read_text(encoding="utf-8")
