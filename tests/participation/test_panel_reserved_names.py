"""URL parameters may not take the names of the keys live votes store in Response.params (audit data storage
2026-10-21, B2): a stored parameter "live_extra" marked every participation with a value as a further word of a live
vote, and the counts of an ordinary survey left them out."""
from app.extensions import db
from app.models import Response, Survey


class _Form(dict):
    def getlist(self, key):
        return self.get(key, [])


def _form(name):
    return _Form({"param_name": [name], "param_mode": ["store"], "param_required": ["0"], "param_allowed": [""]})


def test_names_of_live_keys_are_refused(app, survey):
    from app import panel

    with app.app_context():
        s = db.session.get(Survey, 1)
        for name in ("live_extra", "live_main", "Live_Run"):
            error = panel.update_from_form(s, _form(name))
            assert error and "live_" in str(error)
        assert panel.update_from_form(s, _form("liveid")) is None
        assert panel.add_values(s, "live_device", count=1)[1]


def test_a_url_value_named_live_extra_counts_as_a_participation(app, survey):
    with app.app_context():
        db.session.add_all([Response(survey_id=1, source="real", status=Response.COMPLETED, answers={},
                                     params={"live_extra": "nein"}),
                            Response(survey_id=1, source="real", status=Response.COMPLETED, answers={}, params={})])
        db.session.commit()
        assert db.session.scalars(db.select(Response.live_extra).filter_by(survey_id=1)).all() == [False, False]
        # rows an earlier program marked wrongly still count in an ordinary survey
        db.session.execute(db.update(Response.__table__).values(live_extra=True))
        db.session.commit()
        db.session.expire_all()
        assert db.session.get(Survey, 1).tally() == {("real", False, Response.COMPLETED): 2}
