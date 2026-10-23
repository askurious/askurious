"""Labels of the diary figures that rest on another estimator or level than a reader may assume (audit
Datenspeicherung und Berechnungen 2026-10-21, F9 and E8)."""
from flask import render_template, render_template_string


def test_both_icc_values_name_their_method(app):
    """F9: the course of the diary survey (ANOVA after Shrout & Fleiss, may be negative) and the hypotheses (REML of
    the null model) estimate ICC(1) differently; both say which."""
    from app import diary

    with app.test_request_context():
        html = render_template("diary/_results_time.html", diary_time=[
            {"label": "Stimmung", "code": "mood", "module": "m1", "n": 20, "persons": 4, "icc": 0.21,
             "by_day": [(1, 3.0, 10)], "by_hour": [(9, 3.0, 10)]}], shrout=diary.SHROUT, bolger=diary.BOLGER)
    assert "(ANOVA)" in html and "Shrout und Fleiss" in html and "REML" in html
    import inspect

    from app import diary_model

    source = inspect.getsource(diary_model)
    assert "ICC(1) des Erklärten aus dem Nullmodell" in source and "from the null model (REML" in source


def test_scale_reliability_over_person_means_is_labelled_between(app):
    """E8: α and ω of a diary study over the statements averaged per person are the reliability of the person means."""
    with app.test_request_context():
        def pooled(scale):
            return render_template_string('{% from "surveys/_person_means_note.html" import person_means_note %}'
                                          '{{ person_means_note(s) }}', s=scale)

        assert "Reliabilität der Personenmittel (Between)" in pooled({"by_person": True, "pooled": False, "k": 3})
        assert '"surveys/_person_means_note.html" import person_means_note' in open(
            app.root_path + "/templates/surveys/results.html", encoding="utf-8").read()
        assert "Personenmittel" not in pooled({"by_person": False, "pooled": False, "k": 3})
