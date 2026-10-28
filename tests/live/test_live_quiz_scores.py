"""Quiz points of a live vote (app/live/scores.py), checked without a server: a right answer, the countdown bonus,
shared places. Check 2026-10-28: a slider answer exactly at the edge of the tolerance with decimals (2.4 at 2.3 ± 0.1)
counted as wrong, because 2.4 - 2.3 is a hair more than 0.1 in floating point."""
from types import SimpleNamespace

from app.live.scores import is_right, points, shared_places


def _slider():
    return SimpleNamespace(type="slider")


def test_a_slider_answer_at_the_edge_of_the_tolerance_is_right():
    quiz = {"value": 2.3, "tol": 0.1, "seconds": None}
    assert is_right(_slider(), quiz, {"r1": 2.4}) and is_right(_slider(), quiz, {"r1": 2.2})
    assert is_right(_slider(), {"value": 7.5, "tol": 0.2}, {"r1": 7.7})
    assert not is_right(_slider(), quiz, {"r1": 2.5})
    assert is_right(_slider(), {"value": 3, "tol": 0}, {"r1": 3}) and not is_right(_slider(), {"value": 3, "tol": 0}, {"r1": 4})


def test_points_with_and_without_countdown():
    quiz = {"correct": ["1"], "seconds": 20}
    item = SimpleNamespace(type="single_choice")
    assert points(item, quiz, "1", 0) == 1000 and points(item, quiz, "1", 10) == 750 and points(item, quiz, "1", 20) == 500
    assert points(item, quiz, "1", 21) == 500                                 # within the second's grace: the least
    assert points(item, quiz, "2", 0) == 0 and points(item, quiz, None, 0) == 0
    assert points(item, {"correct": ["1"], "seconds": None}, "1", 13.2) == 1000


def test_multiple_choice_counts_only_all_right_options():
    item, quiz = SimpleNamespace(type="multiple_choice"), {"correct": ["1", "3"]}
    assert is_right(item, quiz, ["3", "1"])
    assert not is_right(item, quiz, ["1"]) and not is_right(item, quiz, ["1", "2", "3"])


def test_same_points_share_a_place():
    assert shared_places([3000, 2000, 2000, 1000, 0, 0]) == [1, 2, 2, 4, 5, 5]
