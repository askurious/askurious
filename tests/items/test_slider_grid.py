"""Decimals of a step and the slider grid from a fractional start (audit 2026-10-23, M8)."""
from app import units
from app.items.slider import snap


def test_decimals_of_small_steps():
    """"%g" writes 0.00001 as 1e-05, which gave 0 decimals."""
    assert [units.decimals(s) for s in (1, 100, 0.5, 0.25, 0.0001, 0.00001, 1e-6, 2.5e-7)] == [0, 0, 1, 2, 4, 5, 6, 8]
    assert units.decimals(0.1 + 0.2) == 1          # binary noise does not count
    assert units.decimals(float("nan")) == 0 and units.decimals(0) == 0 and units.decimals("x") == 0


def test_snap_keeps_small_steps():
    assert snap(0.00003, 0, 0.0001, 0.00001) == 0.00003


def test_snap_on_a_grid_from_a_fractional_start():
    """From 0.5 in steps of 1, 2.5 stays 2.5 (it became 3); from 0.25 in steps of 0.5, 0.25 stays 0.25 (became 0.3)."""
    assert snap(2.5, 0.5, 10.5, 1) == 2.5 and snap(1.6, 0.5, 10.5, 1) == 1.5
    assert snap(0.25, 0.25, 5, 0.5) == 0.25 and snap(0.8, 0.25, 5, 0.5) == 0.75
    assert snap(3, 1, 9, 2) == 3 and isinstance(snap(3, 1, 9, 2), int)


def test_histogram_edges_keep_small_steps():
    """results.histogram rounds its edges to the step's decimals + 1: small steps no longer collapse to 0."""
    from app import results

    [bars] = results.histogram([[0.000001 * i for i in range(30)]])
    edges = [b["lo"] for b in bars["bars"]]
    assert len(set(edges)) == len(edges) and edges[1] > 0
