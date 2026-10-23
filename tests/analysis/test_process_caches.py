"""The small caches each server process keeps (code review 2026-10-15, L5 and L6): gunicorn's threads share them
without a lock, so evicting and reading must stand another thread's work in between."""
import threading
from types import SimpleNamespace

from app import linked_pool, sem_model


class _Racy(dict):
    """A cache whose oldest key another thread evicts between next(iter()) and pop()."""

    def __iter__(self):
        keys = list(dict.__iter__(self))
        if keys:
            dict.pop(self, keys[0])
        return iter(keys)


def test_eviction_stands_a_key_another_thread_took(monkeypatch):
    racy = _Racy({("old", 1, True, False): ("f", {})})
    monkeypatch.setattr(linked_pool, "_cache", racy)
    monkeypatch.setattr(linked_pool, "CACHE_SIZE", 0)
    monkeypatch.setattr(linked_pool.linked, "uses", lambda c: [])
    monkeypatch.setattr(linked_pool, "_editable", lambda user, uses: set())
    monkeypatch.setattr(linked_pool, "_fingerprint", lambda c, uses: "f")
    monkeypatch.setattr(linked_pool, "_collect", lambda *a: {"rows": []})
    c, user = SimpleNamespace(key="k"), SimpleNamespace(id=1)
    assert linked_pool.collect(c, user) == {"rows": []}                   # before: KeyError on the evicted key
    assert len(racy) == 0


def test_sem_cache_returns_its_own_value_and_stays_bounded(monkeypatch):
    monkeypatch.setattr(sem_model, "_CACHE", {})
    calls = []
    assert sem_model._cached("a", lambda: calls.append(1) or 7) == 7
    assert sem_model._cached("a", lambda: calls.append(2) or 8) == 7 and calls == [1]   # kept

    def nested():  # computed outside the lock: a computation may ask the cache itself without a deadlock
        return sem_model._cached("c", lambda: 3) + 2

    assert sem_model._cached("b", nested) == 5 and sem_model._CACHE["c"] == 3
    for i in range(sem_model._CACHE_SIZE + 10):
        sem_model._cached(("n", i), lambda: i)
    assert len(sem_model._CACHE) <= sem_model._CACHE_SIZE


def test_sem_cache_under_threads(monkeypatch):
    monkeypatch.setattr(sem_model, "_CACHE", {})
    monkeypatch.setattr(sem_model, "_CACHE_SIZE", 4)
    wrong = []

    def work(t):
        for i in range(300):
            if sem_model._cached((t, i % 9), lambda: (t, i % 9)) != (t, i % 9):
                wrong.append((t, i))

    threads = [threading.Thread(target=work, args=(t,)) for t in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not wrong and len(sem_model._CACHE) <= 4
