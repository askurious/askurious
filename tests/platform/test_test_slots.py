"""Places for parallel test runs on one machine (tests/helpers/slots.py, concept testsuite-verteilen-konzept.md F1)."""

from tests.helpers import slots


def test_a_place_is_taken_until_released(tmp_path):
    first = slots.try_take(1, folder=tmp_path)
    assert first is not None
    assert slots.try_take(1, folder=tmp_path) is None  # the only place is held
    slots.release(first)
    again = slots.try_take(1, folder=tmp_path)
    assert again is not None
    slots.release(again)


def test_two_places_hold_two_runs(tmp_path):
    held = [slots.try_take(2, folder=tmp_path), slots.try_take(2, folder=tmp_path)]
    assert all(held)
    assert slots.try_take(2, folder=tmp_path) is None
    for handle in held:
        slots.release(handle)


def test_take_waits_for_a_free_place(tmp_path):
    import threading

    held = slots.try_take(1, folder=tmp_path)
    said = []
    threading.Timer(0.3, slots.release, args=(held,)).start()
    got = slots.take(1, folder=tmp_path, say=said.append, pause=0.1)
    assert got is not None
    assert said and said[0].startswith("Waiting for a free test place")
    slots.release(got)
