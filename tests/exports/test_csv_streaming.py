"""CSV output avoids a full StringIO/encoded-body copy without changing values/BOM/dialect (SP13 PF-2)."""
import pytest

from app import export
from app.extensions import db
from app.models import Response, Survey


@pytest.mark.parametrize("separator", ["comma", "semicolon"])
def test_streamed_file_equals_string_export_and_rolls_large_output(app, client, survey, separator):
    from tests.conftest import add

    add(client, "text_input")
    with app.test_request_context():
        study = db.session.get(Survey, 1)
        for n in range(60):
            db.session.add(Response(survey_id=1, source="real", status=Response.COMPLETED,
                                    answers={"2": 'Grüße; "x",\n' + "a" * 40000}))
        db.session.commit()
        expected = export.to_csv(study, separator=separator).encode("utf-8")
        with export.csv_file(study, separator=separator) as file:
            assert file._rolled and file.read() == expected
        assert file.closed and expected.startswith(b"\xef\xbb\xbf")


def test_erasure_removes_the_cached_table_from_a_real_download(app, client, survey):
    from pathlib import Path
    from app import erasure, results_store

    with app.app_context():
        study = db.session.get(Survey, 1)
        response = Response(survey_id=1, source="real", status=Response.COMPLETED)
        db.session.add(response)
        db.session.commit()
        rid = response.id
        path = Path(results_store._path(study, name="export-table"))
    downloaded = client.get("/surveys/1/export/data.csv")
    assert downloaded.status_code == 200 and path.is_file()
    downloaded.close()
    with app.app_context():
        study = db.session.get(Survey, 1)
        erasure.erase(study, [db.session.get(Response, rid)], "request")
        assert not path.exists()
