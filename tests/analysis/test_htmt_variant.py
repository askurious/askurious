"""HTMT stays the original of Henseler et al. (2015), named so, with a warning for mixed signs (audit 2026-10-23,
R-M1)."""
import numpy as np

from app import methods_text, sem, sem_model


def _quality(corr):
    spec = {"latent": {"A": ["a1", "a2"], "B": ["b1", "b2"]}}
    loadings = {lv: [{"std": 0.8}] * 2 for lv in spec["latent"]}
    return sem.quality(spec, loadings, np.array(corr), ["a1", "a2", "b1", "b2"])


def test_original_htmt_and_mixed_signs():
    same = _quality([[1, .6, .3, .3], [.6, 1, .3, .3], [.3, .3, 1, .5], [.3, .3, .5, 1]])
    assert abs(same["htmt"][("A", "B")] - 0.3 / np.sqrt(0.6 * 0.5)) < 1e-12
    assert same["htmt_mixed"] == []
    mixed = _quality([[1, .6, .5, -.4], [.6, 1, .5, -.4], [.5, .5, 1, .5], [-.4, -.4, .5, 1]])
    assert mixed["htmt_mixed"] == [["A", "B"]]
    result = {"fit": {"quality": mixed}, "nodes": {"A": ("A", "Alpha"), "B": ("B", "Beta")}, "spec": {}}
    assert sem_model.htmt_mixed(result) == [("Alpha", "Beta")]
    assert sem_model.htmt_mixed({"fit": {"quality": {"htmt": {}}}}) == []


def test_methods_text_names_the_variant():
    text = str(methods_text.__dict__)
    assert "HTMT in der ursprünglichen Form" in text and "HTMT in its original form, not HTMT+" in text
