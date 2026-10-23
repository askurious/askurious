"""Performance audit 2026-10-09, package P7: the HB check kept per data state (N-6), the data page without it,
the scale profile's sample worked out once per kept answers (N-5), no survey lock without targets (N-10)."""
from app import balance, export, lifecycle, maxdiff_hb
from app.extensions import db
from app.models import Item, Survey
from tests.analysis.test_maxdiff_hb import with_dummies


def test_derived_is_built_once_for_the_same_lists(app):
    app.config["BALANCE_SECONDS"] = 60
    with app.app_context():
        calls = []
        a, b = [(1, 2)], [(2, 3)]
        assert balance.derived(("x", 1), [a, b], lambda: calls.append(1) or "v") == "v"
        assert balance.derived(("x", 1), [a, b], lambda: calls.append(1) or "w") == "v"   # the same lists: kept
        assert balance.derived(("x", 1), [list(a), b], lambda: calls.append(1) or "w") == "w"  # read anew: built
        app.config["BALANCE_SECONDS"] = 0
        assert balance.derived(("x", 1), [list(a), b], lambda: "z") == "z"                # no keeping at all
        assert len(calls) == 2


def test_hb_check_is_kept_while_the_data_stay(app, client, survey, monkeypatch):
    with_dummies(app, client, n=20)
    client.post("/surveys/1/results/hb/2", data={"source": "dummy", "speeder": "50"})
    maxdiff_hb._FITS.clear()
    built = []
    original = maxdiff_hb.pool_table
    monkeypatch.setattr(maxdiff_hb, "pool_table", lambda *a: built.append(1) or original(*a))
    with app.app_context():
        from app import dummy

        assert maxdiff_hb.current(db.session.get(Item, 2)) is not None
        assert maxdiff_hb.current(db.session.get(Item, 2)) is not None
        # the estimate keeps the fingerprint of its data (audit 2026-10-09b, V1): no table is built for the check
        assert len(built) == 0
        dummy.generate(db.session.get(Survey, 1), 3, seed=6)
        db.session.commit()
        assert maxdiff_hb.current(db.session.get(Item, 2)) is None              # new data: another fingerprint, stale
        assert len(built) == 0
    # the data page names the scale columns without checking the estimate
    monkeypatch.setattr(maxdiff_hb, "current", lambda item: (_ for _ in ()).throw(AssertionError("checked")))
    with app.app_context():
        export.scale_defs(db.session.get(Survey, 1))


def test_no_survey_lock_without_targets(app, client, survey, monkeypatch):
    refreshed = []
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.status = Survey.RUNNING
        s.max_responses = None
        db.session.commit()
        monkeypatch.setattr(db.session, "refresh", lambda *a, **k: refreshed.append(k))
        lifecycle.close_if_sample_complete(s)
        assert refreshed == []
        s.max_responses = 100
        lifecycle.close_if_sample_complete(s)
        assert refreshed == [{"with_for_update": {"key_share": True}}]
