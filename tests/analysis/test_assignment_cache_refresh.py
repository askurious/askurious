import json
from app.extensions import db
from app.models import Survey, Response, utcnow

def test_cached_results_refresh_assignment_after_incomplete_starts(app, client, survey, monkeypatch):
    from app import groupvars, results_store, compare, export
    from tests.editor.test_flow import setup_design
    from datetime import timedelta
    setup_design(app, client)
    pool = {"sources": ["real"], "all": False, "drop": True, "speeder": 50, "speed_real": True}
    with app.app_context():
        s = db.session.get(Survey, survey)
        s.data_pool = {"sources": ["real"], "all": False}
        s.quality_rules = {"flags": [], "min": 1}
        now = utcnow()
        def add_row(condition, status):
            db.session.add(Response(survey_id=survey, source="real", is_preview=False,
                status=status, answers={"2": "30"}, started_at=now - timedelta(seconds=60),
                completed_at=now if status == Response.COMPLETED else None,
                layout={"conditions": {"r1": [condition]}, "pages": [{"block": int(condition[1:]), "items": [], "cond": [condition]}]}))
        for condition in ("k3", "k4"):
            for _ in range(3):
                add_row(condition, Response.COMPLETED)
        db.session.commit()
        by = groupvars._randomizers(s)["rand:framing"]
        key_before = results_store.fingerprint(s, pool)
        before = groupvars.assignment_check(by, s, ["real"])
        result = compare.compute(s, by, {"items": []}, export.rows(s, sources=("real",), with_hb=False))
        assert results_store.save(s, key_before + ":" + by.stamp, result, pool, name="compare")
        first = client.get("/surveys/1/results?go=1&gruppen=rand:framing")
        assert first.status_code == 200
        for _ in range(20):
            add_row("k3", Response.ACTIVE)
        db.session.commit()
        key_after = results_store.fingerprint(s, pool)
        after = groupvars.assignment_check(by, s, ["real"])
        cached = results_store.load(s, key_after + ":" + by.stamp, pool, name="compare")[0]["assignment"]
        print("CACHE_REPRO", json.dumps({"same_fingerprint": key_before == key_after, "before": before, "fresh_after": after, "loaded_cache": cached}, ensure_ascii=False))
        assert key_before == key_after
        assert before != after
        assert cached == before

    from flask import template_rendered
    seen = []
    def capture(sender, template, context, **extra):
        if template.name == "surveys/results.html":
            seen.append(context["cmp"]["assignment"])
    def no_recompute(*args, **kwargs):
        raise AssertionError("Comparison should come from cache")
    monkeypatch.setattr(compare, "compute", no_recompute)
    with template_rendered.connected_to(capture, app):
        assert client.get("/surveys/1/results?go=1&gruppen=rand:framing").status_code == 200
    assert seen == [after]
    assert sum(r["started"] for r in seen[0]["rates"]) == 26
