"""Helpers of the tests from test_audit_small.py; the tests themselves are in tests/accounts/test_accounts_audit_findings.py, tests/editor/test_editor_audit_findings.py, tests/exports/test_exports_audit_findings.py, tests/items/test_items_audit_findings.py, tests/participation/test_participation_audit_findings.py, tests/ui/test_ui_audit_findings.py (sorted by area, architecture concept 2026-10-12, G).

Smaller points of the data audit: codebook values, orders in loops, screen-out times, long JSON in .sav.
"""
import csv


import io


import json


import random


from datetime import datetime


from werkzeug.datastructures import MultiDict


from app import design, export, runtime


from app.extensions import db


from app.models import Block, Item, Response, Survey


from tests.conftest import add


E = "/surveys/1/editor"


def ranking(client, item_id=2, last_code="3"):
    add(client, "ranking")
    client.post(f"{E}/items/{item_id}", data=MultiDict([
        ("code", "prio"), ("question", "Was ist wichtig?"), ("randomize", "1"),
        ("option_id", "a"), ("option_code", "1"), ("option_label", "Sicherheit"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "Kosten"),
        ("option_id", "c"), ("option_code", last_code), ("option_label", "Komfort")]))
