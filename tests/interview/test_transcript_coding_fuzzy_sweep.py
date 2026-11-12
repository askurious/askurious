"""Interviews kodieren: the pairs of categories whose borders blur (app/transcript_coding_review.py, fuzzy) are
found by sorting the passages of an interview and sweeping over them, not by comparing every pair; the pairs and their
order stay exactly those of the comparison of every pair (performance audit 2026-11-11: 9 s per call for 1 500 codings
of an interview, in the overview, the diagram and the map)."""
import random

from app import transcript_coding_review as review


def _every_pair(items):
    return [(i, j) for i in range(len(items)) for j in range(i + 1, len(items))
            if min(items[i]["end"], items[j]["end"]) - max(items[i]["start"], items[j]["start"]) > 0]


def test_the_sweep_finds_the_overlapping_pairs_in_the_same_order():
    rng = random.Random(4)
    for _ in range(200):
        items = []
        for _k in range(rng.randint(0, 40)):
            start = rng.randint(0, 300)
            items.append({"start": start, "end": start + rng.choice((0, 1, 5, 40, 120))})
        assert review._overlapping(items) == _every_pair(items)


def test_passages_that_only_touch_are_no_pair():
    items = [{"start": 0, "end": 10}, {"start": 10, "end": 20}, {"start": 5, "end": 5}]
    assert review._overlapping(items) == []
