"""Answer codes that only look like numbers stay text in SPSS, R and jamovi (audit storage and calculations
2026-10-28, E-1): "nan" and "inf" became missing, two digit strings beyond 2^53 the same number."""
import pytest
from werkzeug.datastructures import MultiDict

from app import export
from app.extensions import db
from app.models import Response, Survey
from tests.conftest import add


def _single(client, *codes):
    add(client, "single_choice")
    data = [("code", "sc"), ("question", "Q")]
    for n, code in enumerate(codes):
        data += [("option_id", ""), ("option_code", code), ("option_label", f"L{n}")]
    client.post("/surveys/1/editor/items/2", data=MultiDict(data))


@pytest.mark.parametrize("codes, kind", [
    (("nan", "inf"), "str"),
    (("9007199254740992", "9007199254740993"), "str"),
    (("1e5", "2"), "str"),
    (("1", "2"), "int"),
    (("9007199254740991", "2"), "int"),
])
def test_codes_keep_their_values(app, client, survey, codes, kind):
    _single(client, *codes)
    with app.app_context():
        s = db.session.get(Survey, 1)
        for n, code in enumerate(codes):
            s.responses.append(Response(status=Response.COMPLETED, source="real", answers={"2": code}, pid=f"p{n}"))
        db.session.commit()
        _cols, data, meta = export._spss_dictionary(s, False, ("real",), None, True, None)
        assert meta["sc"]["kind"] == kind
        if kind == "str":
            assert sorted(str(r["sc"]) for r in data) == sorted(codes)
