"""One list of what the survey file and a duplicate take over (app/transfer.py SETTINGS, HANDLED, NOT_TAKEN), and the
study templates in the current file format (concept "Vereinheitlichung der Befragungsexporte", E1 and E9)."""
import json
from pathlib import Path

from app import duplicate, study_templates, transfer
from app.models import Asset, Survey


def _columns(model):
    return {c.key for c in model.__mapper__.column_attrs}


def test_every_survey_column_is_in_exactly_one_list():
    lists = [set(transfer.SETTINGS), set(transfer.HANDLED), set(transfer.NOT_TAKEN)]
    named = [name for group in lists for name in group]
    assert len(named) == len(set(named)), "a column is named in two lists"
    columns = _columns(Survey)
    assert columns - set(named) == set(), "new Survey column: add it to transfer.SETTINGS, HANDLED or NOT_TAKEN"
    assert set(named) - columns == set(), "a list names a column Survey no longer has"


def test_duplicate_takes_the_file_settings_and_only_its_own_extras():
    assert set(duplicate.SETTINGS) == set(transfer.SETTINGS) | set(transfer.DUPLICATE_ONLY)
    assert set(transfer.DUPLICATE_ONLY) <= set(transfer.HANDLED) | set(transfer.NOT_TAKEN)


def test_every_asset_column_is_taken_over_or_new():
    own = {"id", "item_id", "filename", "created_at"}  # new in the copy and in a file read in
    assert _columns(Asset) - own == set(transfer.ASSET_FIELDS)
    assert set(duplicate.ASSET_FIELDS) == set(transfer.ASSET_FIELDS) - {"rights"}


def test_study_templates_have_the_current_file_version():
    files = sorted(Path(study_templates.FOLDER).glob("*.json"))
    assert files
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        assert (data["format"], data["version"]) == (transfer.FORMAT, transfer.VERSION), path.name
