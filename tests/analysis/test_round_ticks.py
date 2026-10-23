"""Axis ticks over every magnitude (audit 2026-10-23, M9): an absolute tolerance of 1e-9 gave thousands of ticks
for a span of 1e-12 and an endless loop for 1e-100."""
import time

import pytest

from app import results


@pytest.mark.parametrize("hi", [1e-3, 1e-6, 1e-9, 1e-12, 1e-100, 1e-300, 1.0, 1e6, 1e100])
def test_few_ticks_at_every_magnitude(hi):
    start = time.process_time()
    ticks = results.round_ticks(0, hi, most=4)
    assert time.process_time() - start < 0.5
    assert 2 <= len(ticks) <= 6
    assert ticks == sorted(set(ticks)) and ticks[0] >= 0 and ticks[-1] <= hi * (1 + 1e-9)


def test_constant_and_ordinary_ranges():
    assert results.round_ticks(3, 3) == [3]
    assert results.round_ticks(47, 60) == [50, 55, 60]
    assert results.round_ticks(0, 1) == [0, 0.25, 0.5, 0.75, 1]
    assert results.round_ticks(-5, 5) == [-5, -2.5, 0, 2.5, 5]
    assert results.round_ticks(0, float("inf")) == [0]
