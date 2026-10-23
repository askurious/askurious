"""The selection shown before download must count exactly the exported participations."""
import csv
import io
from datetime import timedelta

import pytest

from app.extensions import db
from app.models import Response, utcnow


@pytest.mark.parametrize("query", ["source=real", "source=test&source=dummy", "source=real&all=1", "source=real&drop=1"])
def test_selection_count_matches_actual_csv_and_metadata(app, client, survey, query):
    with app.app_context():
        now = utcnow()
        for source, status, seconds in [("real", Response.COMPLETED, 5), ("real", Response.COMPLETED, 100),
                                        ("real", Response.COMPLETED, 100), ("test", Response.COMPLETED, 80),
                                        ("dummy", Response.COMPLETED, 80), ("real", Response.ACTIVE, 20),
                                        ("real", "review", 20)]:
            db.session.add(Response(survey_id=1, source=source, status=status, started_at=now-timedelta(seconds=seconds),
                                    completed_at=now if status == Response.COMPLETED else None, last_active_at=now))
        db.session.commit()
    count = client.get("/surveys/1/data-counts?"+query)
    assert count.status_code == 200 and count.headers["Cache-Control"] == "no-store"
    downloaded = client.get("/surveys/1/export/data.csv?"+query)
    rows = list(csv.reader(io.StringIO(downloaded.get_data(as_text=True).lstrip("\ufeff"))))
    assert count.json["included"] == len(rows)-1
    metadata = client.get("/surveys/1/data-selection.json?"+query)
    assert metadata.json["counts"] == count.json
    assert metadata.json["weighted"] is False
    assert metadata.json["exclude_marked"] == ("drop=1" in query)
    assert "attachment" in metadata.headers["Content-Disposition"]
    if query == "source=real":
        assert count.json == {"total": 3, "included": 3, "marked": 1}


def test_selection_endpoints_require_survey_access(app, client, survey):
    anonymous = app.test_client()
    for suffix in ("data-counts", "data-selection.json"):
        assert anonymous.get("/surveys/1/"+suffix).status_code == 302
        assert client.get("/surveys/999999/"+suffix).status_code == 404
