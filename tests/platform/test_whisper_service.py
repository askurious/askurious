"""Private durable queue: restart, cancellation, retention and admission bounds."""
import io
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "deploy/docker/whisper"))
from whisper_service.api import create_app
from whisper_service.pipeline import split_speakers
from whisper_service.queue import Queue, QueueFull
from whisper_service.settings import Settings, options
from whisper_service import worker
from whisper_service import estimates
from whisper_service.pipeline import ModelCache, with_prompt


@pytest.fixture
def queue(tmp_path, monkeypatch):
    monkeypatch.setattr(estimates, 'duration', lambda path: None)
    return Queue(Settings(tmp_path / "state", tmp_path / "models", "x" * 48, min_free_mb=0, max_bytes=100))


def submit(queue, audio=b"audio"):
    return queue.submit(io.BytesIO(audio), options({}))


def test_restart_recovers_only_unfinished_job_and_keeps_result(queue):
    first, second = submit(queue), submit(queue, b"second")
    assert queue.claim()["id"] == first
    queue.finish(first, result={"text": "complete"})
    queue.claim()
    restarted = Queue(queue.settings)
    restarted.recover()
    assert restarted.get(first)["state"] == "done"
    assert json.loads(restarted.get(first)["result"])["text"] == "complete"
    assert restarted.claim()["id"] == second
    assert restarted.audio(second).read_bytes() == b"second"
    assert not restarted.audio(first).exists()


def test_cancellation_cannot_be_undone_by_late_worker(queue):
    job = submit(queue)
    queue.claim()
    queue.cancel(job)
    assert not queue.finish(job, result={"text": "late"})
    assert queue.get(job)["state"] == "cancelled"
    assert queue.get(job)["result"] is None
    assert list(queue.files.iterdir()) == []


def test_identical_recordings_have_independent_erasure(queue):
    first, second = submit(queue), submit(queue)
    assert first != second
    queue.cancel(first)
    assert queue.get(second)["state"] == "queued"
    assert queue.audio(second).exists()


def test_admission_and_upload_bounds_leave_no_orphans(queue):
    queue.settings = replace(queue.settings, max_pending=1)
    submit(queue)
    with pytest.raises(QueueFull):
        submit(queue, b"other")
    with pytest.raises(ValueError, match="upload_too_large"):
        submit(queue, b"x" * 101)
    assert len(list(queue.files.iterdir())) == 1
    assert queue.counts()["free"] == 0  # the app reads it and waits instead of sending (Ralf, 2026-10-19)


def test_admission_keeps_room_for_one_more_recording_on_disk(queue, monkeypatch):
    from types import SimpleNamespace
    queue.settings = replace(queue.settings, min_free_mb=1, max_bytes=1024 * 1024)
    monkeypatch.setattr("whisper_service.queue.shutil.disk_usage", lambda _: SimpleNamespace(free=2 * 1024 * 1024 - 1))
    with pytest.raises(QueueFull):
        submit(queue)


def test_retention_removes_audio_and_result_but_keeps_recent_job(queue):
    expired, complete, recent = submit(queue), submit(queue, b"done"), submit(queue, b"recent")
    with queue.connect() as db:
        db.execute("UPDATE jobs SET created=? WHERE id=?", (time.time() - 86401, expired))
        db.execute("UPDATE jobs SET state='running' WHERE id=?", (complete,))
    queue.finish(complete, result={"text": "private"})
    with queue.connect() as db:
        db.execute("UPDATE jobs SET updated=? WHERE id=?", (time.time() - 3601, complete))
    queue.sweep()
    assert queue.get(expired) is None and queue.get(complete) is None
    assert queue.get(recent)["state"] == "queued"
    assert {p.name for p in queue.files.iterdir()} == {f"{recent}.audio"}


def test_api_auth_validation_status_and_cancellation(queue):
    client = create_app(queue).test_client()
    assert client.get("/healthz").status_code == 200
    assert client.post("/v1/jobs").status_code == 401
    headers = {"Authorization": f"Bearer {queue.settings.token}"}
    invalid = client.post("/v1/jobs", headers=headers,
                          data={"file": (io.BytesIO(b"audio"), "private.webm"), "model": "other"})
    assert invalid.status_code == 400
    response = client.post("/v1/jobs", headers=headers,
                           data={"file": (io.BytesIO(b"audio"), "private.webm"), "model": "large-v3-turbo"})
    job = response.json["id"]
    assert response.status_code == 202 and len(job) == 32
    assert client.get(f"/v1/jobs/{job}", headers=headers).json["state"] == "queued"
    assert client.delete(f"/v1/jobs/{job}", headers=headers).status_code == 200
    assert not queue.audio(job).exists()


def test_word_level_speaker_change_is_preserved():
    result = split_speakers([{"start": 0, "end": 3, "speaker": "SPEAKER_00", "words": [
        {"start": 0, "end": 1, "word": "Hallo", "speaker": "SPEAKER_00"},
        {"start": 1, "end": 2, "word": "Guten", "speaker": "SPEAKER_01"},
        {"start": 2, "end": 3, "word": "Tag", "speaker": "SPEAKER_01"}]}])
    assert [(row["text"], row["speaker"]) for row in result] == [
        ("Hallo", "SPEAKER_00"), ("Guten Tag", "SPEAKER_01")]


def test_status_counts_only_unfinished_jobs_and_requires_auth(queue):
    client = create_app(queue).test_client()
    headers = {"Authorization": f"Bearer {queue.settings.token}"}
    assert client.get("/v1/status").status_code == 401
    assert client.get("/v1/status", headers=headers).json == {"state": "ready", "queued": 0, "running": 0, "free": 8}
    first, second = submit(queue), submit(queue)
    queue.claim()
    assert client.get("/v1/status", headers=headers).json == {"state": "busy", "queued": 1, "running": 1, "free": 6}
    queue.finish(first, result={"text": "private"})
    queue.cancel(second)
    assert client.get("/v1/status", headers=headers).json == {"state": "ready", "queued": 0, "running": 0, "free": 8}
    unavailable = create_app(queue, ready=lambda: False).test_client().get("/v1/status", headers=headers)
    assert unavailable.status_code == 503
    assert unavailable.json == {"state": "unavailable", "queued": 0, "running": 0}


def test_multipart_recording_spills_to_disk_without_false_413(queue):
    queue.settings = replace(queue.settings, max_bytes=1024 * 1024)
    client = create_app(queue).test_client()
    response = client.post("/v1/jobs", headers={"Authorization": f"Bearer {queue.settings.token}"},
                           data={"model": "small", "language": "de", "align": "true", "diarize": "true",
                                 "file": (io.BytesIO(b"a" * (800 * 1024)), "audio.wav", "audio/wav")})
    assert response.status_code == 202
    assert queue.audio(response.json["id"]).stat().st_size == 800 * 1024


def test_host_pressure_stops_child_and_requeues_without_losing_audio(queue, monkeypatch):
    job_id = submit(queue)
    job = queue.claim()
    class Child:
        stopped = False
        def poll(self):
            return 0 if self.stopped else None
    child = Child()
    monkeypatch.setattr(worker.subprocess, "Popen", lambda *a, **kw: child)
    monkeypatch.setattr(worker, "stop", lambda process: setattr(process, "stopped", True))
    worker.execute(queue, job, allowed=lambda settings: False)
    assert child.stopped
    assert queue.get(job_id)["state"] == "queued"
    assert queue.audio(job_id).read_bytes() == b"audio"
    assert not (queue.files / f"{job_id}.input").exists()


def test_queue_position_tracks_cancellation_and_running_stage(queue):
    first, second = submit(queue), submit(queue)
    assert queue.progress(queue.get(second)) == {"stage": "queued", "position": 2}
    queue.cancel(first)
    assert queue.progress(queue.get(second))["position"] == 1
    queue.claim()
    path = queue.files / f"{second}.progress"
    path.write_text(json.dumps({"stage": "aligning", "text": "private"}))
    client = create_app(queue).test_client()
    response = client.get(f"/v1/jobs/{second}", headers={"Authorization": f"Bearer {queue.settings.token}"})
    assert response.json["stage"] == "aligning" and response.json["position"] == 0
    assert "text" not in response.json
    path.write_text('{"stage": "private contents"}')
    assert queue.progress(queue.get(second))["stage"] == "loading"
    queue.cancel(second)
    assert not path.exists()


def test_cache_reuses_one_model_and_replaces_changed_language_or_model():
    cache, loaded = ModelCache(), []
    def load():
        model = object()
        loaded.append(model)
        return model
    one, hit = cache.get(("small", "de"), load)
    assert not hit
    assert cache.get(("small", "de"), load) == (one, True)
    two, hit = cache.get(("small", "en"), load)
    assert not hit and two is not one and len(loaded) == 2
    cache.clear()
    assert cache.model is None and cache.key is None


def test_prompts_do_not_modify_cached_baseline_options():
    from collections import namedtuple
    Options = namedtuple("Options", "initial_prompt beam_size")
    baseline = Options(None, 5)
    assert with_prompt(baseline, "first private prompt").initial_prompt == "first private prompt"
    assert with_prompt(baseline, "other private prompt").initial_prompt == "other private prompt"
    assert baseline.initial_prompt is None


def test_large_weights_can_be_released_without_losing_reusable_context():
    from types import SimpleNamespace
    calls = []
    engine = SimpleNamespace(unload_model=lambda **kwargs: calls.append(("unload", kwargs)),
                             load_model=lambda: calls.append(("load", {})))
    cache = ModelCache()
    model = SimpleNamespace(model=SimpleNamespace(model=engine))
    cache.get("turbo", lambda: model)
    cache.unload_weights()
    assert cache.unloaded and cache.get("turbo", lambda: None) == (model, True)
    cache.load_weights()
    cache.load_weights()
    assert not cache.unloaded and calls == [("unload", {"to_cpu": False}), ("load", {})]


def test_resident_completion_reuses_process_and_erases_job_files(queue, monkeypatch):
    resident = worker.Resident(queue.settings)
    class Child:
        pid = 999999999
        stdin = None
        returncode = None
        def poll(self):
            return self.returncode
    child = Child()
    def send(path):
        payload = json.loads(path.read_text())
        assert not Path(payload["output"]).exists() and not Path(payload["complete"]).exists()
        Path(payload["output"]).write_text(json.dumps({"text": "synthetic"}))
        Path(payload["complete"]).write_text('{"code": 0}')
        resident.process = child
        return child
    monkeypatch.setattr(resident, "send", send)
    monkeypatch.setattr(worker, "stop", lambda process: setattr(process, "returncode", 0))
    for number in range(2):
        job = submit(queue)
        # Simulate a child completing just before the old parent crashed.
        (queue.files / f"{job}.complete").write_text('{"code": 0}')
        queue.result_file(job).write_text('{"text": "stale attempt"}')
        worker.execute(queue, queue.claim(), allowed=lambda settings: True, resident=resident)
        assert queue.get(job)["state"] == "done" and not queue.audio(job).exists()
        assert json.loads(queue.get(job)["result"])["text"] == "synthetic"
        assert resident.process is child and resident.used == number + 1
    resident.expire(pressured=True)
    assert resident.process is None and child.poll() == 0


def test_resident_idle_and_job_limits_release_models(queue, monkeypatch):
    from types import SimpleNamespace
    resident = worker.Resident(queue.settings)
    resident.process = SimpleNamespace(pid=999999999)
    closed = []
    monkeypatch.setattr(resident, "close", lambda: closed.append(True))
    resident.finished = time.monotonic() - queue.settings.cache_idle_seconds - 1
    resident.expire()
    assert closed
    closed.clear()
    resident.finished, resident.used = time.monotonic(), queue.settings.cache_jobs
    resident.expire()
    assert closed


def test_running_cancellation_closes_resident_process(queue, monkeypatch):
    job_id = submit(queue)
    job = queue.claim()
    resident = worker.Resident(queue.settings)
    class Child:
        stdin = None
        stopped = False
        def poll(self):
            return 0 if self.stopped else None
    child = Child()
    def send(path):
        resident.process = child
        queue.cancel(job_id)
        return child
    monkeypatch.setattr(resident, "send", send)
    monkeypatch.setattr(worker, "stop", lambda process: setattr(process, "stopped", True))
    worker.execute(queue, job, allowed=lambda settings: True, resident=resident)
    assert child.stopped and resident.process is None
    assert queue.get(job_id)["state"] == "cancelled" and list(queue.files.iterdir()) == []


def test_submit_between_empty_claim_and_wait_is_not_lost(queue, monkeypatch):
    import threading
    stopping = threading.Event()
    original_claim, original_wait = queue.claim, queue.wake.wait
    submitted, processed = [], []
    def claim():
        row = original_claim()
        if not submitted:
            assert row is None
            submitted.append(submit(queue))
        return row
    def wait(timeout):
        assert queue.wake.is_set(), 'Submission notification was cleared after checking SQLite'
        return original_wait(0)
    def execute(_queue, job, **kwargs):
        processed.append(job['id'])
        stopping.set()
    monkeypatch.setattr(queue, 'claim', claim)
    monkeypatch.setattr(queue.wake, 'wait', wait)
    monkeypatch.setattr(worker, 'room', lambda settings: True)
    monkeypatch.setattr(worker, 'execute', execute)
    worker.run(queue, stopping)
    assert submitted == processed


def test_host_pressure_waits_even_if_new_jobs_wake_queue(queue, monkeypatch):
    import threading
    stopping = threading.Event()
    submit(queue)
    def wait(seconds):
        assert seconds == 15
        stopping.set()
    monkeypatch.setattr(stopping, 'wait', wait)
    monkeypatch.setattr(queue.wake, 'wait', lambda _: pytest.fail('Busy loop under host pressure'))
    monkeypatch.setattr(worker, 'room', lambda settings: False)
    worker.run(queue, stopping)
    assert queue.counts() == {'queued': 1, 'running': 0, 'free': 7}


@pytest.mark.parametrize('oom,code,expected', [(True, -9, 'memory_limit'), (False, -9, 'processing_failed'), (False, 4, 'memory_limit')])
def test_memory_failure_uses_evidence_and_erases_audio(queue, monkeypatch, oom, code, expected):
    from types import SimpleNamespace
    job = submit(queue)
    child = SimpleNamespace(returncode=code, poll=lambda: code)
    calls = iter([10, 11 if oom else 10])
    monkeypatch.setattr(worker, 'oom_kills', lambda: next(calls))
    monkeypatch.setattr(worker.subprocess, 'Popen', lambda *a, **kw: child)
    worker.execute(queue, queue.claim())
    assert queue.get(job)['error'] == expected
    assert not queue.audio(job).exists()


def test_estimate_includes_running_and_earlier_queued_jobs_but_not_later_jobs(queue):
    first, target, later = submit(queue), submit(queue), submit(queue)
    queue.claim()
    now = time.time()
    with queue.connect() as db:
        db.execute('UPDATE jobs SET duration=60,started=?', (now - 20,))
        db.execute('UPDATE jobs SET duration=NULL WHERE id=?', (later,))
    estimate = queue.estimate(target)
    assert 480 <= estimate['seconds'] <= 500
    assert 290 <= estimate['low_seconds'] <= 292 and 914 <= estimate['high_seconds'] <= 916
    assert not estimate['sampled'] and not estimate['overdue']
    with queue.connect() as db:
        db.execute('UPDATE jobs SET duration=NULL WHERE id=?', (first,))
    assert queue.estimate(target) is None
    queue.cancel(first)
    assert 259 <= queue.estimate(target)['seconds'] <= 260


def test_timing_history_is_aggregated_without_content_and_expires(queue, monkeypatch):
    ids = []
    for _ in range(3):
        job = queue.submit(io.BytesIO(b'audio'), options({'prompt': 'private glossary'}))
        ids.append(job)
        queue.claim()
        with queue.connect() as db:
            db.execute('UPDATE jobs SET duration=60,started=? WHERE id=?', (time.time() - 140, job))
        queue.finish(job, result={'text': 'private transcript'})
        queue.cancel(job)
    with queue.connect() as db:
        samples = [dict(row) for row in db.execute('SELECT * FROM timings')]
    assert len(samples) == 1 and samples[0]['samples'] == 3 and 1.9 < samples[0]['rate'] < 2.1
    stored = json.dumps(samples)
    assert 'private' not in stored and not any(job in stored for job in ids)
    queue.sweep(now=time.time() + 7 * 86400 + 1)
    with queue.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM timings').fetchone()[0] == 0


def test_decoded_duration_enables_estimate_for_webm_without_metadata(queue):
    job = submit(queue)
    queue.claim()
    assert queue.estimate(job) is None
    (queue.files / f'{job}.progress').write_text(json.dumps({'stage': 'transcribing', 'duration': 600}))
    queue.observe_duration(job)
    assert queue.estimate(job)['seconds'] > 2300


def test_existing_spool_is_upgraded_without_losing_queued_audio(tmp_path):
    import sqlite3
    folder = tmp_path / 'old'
    (folder / 'files').mkdir(parents=True)
    job = 'a' * 32
    audio = folder / 'files' / f'{job}.audio'
    audio.write_bytes(b'old audio')
    with sqlite3.connect(folder / 'jobs.sqlite') as db:
        db.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, state TEXT NOT NULL, "
                   "created REAL NOT NULL, updated REAL NOT NULL, options TEXT NOT NULL, result TEXT, "
                   "error TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0)")
        db.execute("INSERT INTO jobs(id,fingerprint,state,created,updated,options) VALUES(?,?,'queued',?,?,?)",
                   (job, 'hash', time.time(), time.time(), json.dumps(options({}))))
    upgraded = Queue(Settings(folder, tmp_path / 'models', 'x' * 48))
    assert upgraded.claim()['id'] == job and audio.read_bytes() == b'old audio'
    assert upgraded.get(job)['duration'] is None and upgraded.get(job)['started'] > 0


def test_duration_probe_skips_unknown_metadata_and_has_a_deadline(monkeypatch, tmp_path):
    from types import SimpleNamespace
    def probe(arguments, **kwargs):
        assert kwargs['timeout'] == 3 and 'file,pipe' in arguments
        return SimpleNamespace(stdout=b'{"format":{"duration":"N/A"},"streams":[{"duration":"12.5"}]}')
    monkeypatch.setattr(estimates.subprocess, 'run', probe)
    assert estimates.duration(tmp_path / 'audio') == 12.5
    def timeout(*args, **kwargs):
        raise estimates.subprocess.TimeoutExpired('ffprobe', 3)
    monkeypatch.setattr(estimates.subprocess, 'run', timeout)
    assert estimates.duration(tmp_path / 'audio') is None


@pytest.mark.parametrize('error,expected', [(MemoryError(), True), (RuntimeError("can't allocate memory"), True),
                                           (RuntimeError('invalid input'), False)])
def test_allocator_failure_is_distinct_from_other_pipeline_errors(error, expected):
    from whisper_service.pipeline import memory_failure
    assert memory_failure(error) is expected


def test_elapsed_time_is_subtracted_from_the_range_not_used_to_shrink_uncertainty():
    row = {'id': 'synthetic', 'state': 'running', 'duration': 60, 'started': 100,
           'options': json.dumps(options({}))}
    value = estimates.estimate([row], 'synthetic', {}, 4, 300)
    assert value['seconds'] == 60 and value['low_seconds'] == 60 and value['high_seconds'] == 268
    assert not value['overdue']
    assert not estimates.estimate([row], 'synthetic', {}, 4, 400)['overdue']
    assert estimates.estimate([row], 'synthetic', {}, 4, 600)['overdue']


def test_decoding_reads_only_a_local_file():
    """Abgleich 2026-10-15, NB-9/NB-10: ffmpeg decoded the recording without a protocol allowlist; a file with a valid
    header could have led a demuxer to another protocol. Now only "file", as estimates.py already did."""
    import ast
    import inspect

    import whisper_service.pipeline as pipeline

    calls = [n for n in ast.walk(ast.parse(inspect.getsource(pipeline)))
             if isinstance(n, ast.List) and n.elts and isinstance(n.elts[0], ast.Constant) and n.elts[0].value == "ffmpeg"]
    assert calls
    for call in calls:
        args = [e.value if isinstance(e, ast.Constant) else None for e in call.elts]
        assert args.index("-protocol_whitelist") < args.index("-i") and args[args.index("-protocol_whitelist") + 1] == "file"


def test_threads_and_load_threshold_come_from_the_environment(monkeypatch):
    """2026-10-17: 8 threads on 24 vCPU, so the fixed load threshold of 12 would hold back the next job."""
    monkeypatch.setenv("WHISPER_THREADS", "8")
    monkeypatch.setenv("WHISPER_MAX_LOAD", "20")
    settings = Settings.environment()
    assert settings.threads == 8 and settings.max_host_load == 20.0
    monkeypatch.delenv("WHISPER_MAX_LOAD")
    assert Settings.environment().max_host_load == 12.0


def test_the_threads_compose_sets_pass_the_start_check():
    """2026-10-17: compose.yaml set 8 threads while __main__ still allowed 1..4, so the container ended at start."""
    import re
    root = Path(__file__).resolve().parents[2]
    compose = (root / "deploy/docker/compose.yaml").read_text(encoding="utf-8")
    threads = int(re.search(r'WHISPER_THREADS: "(\d+)"', compose).group(1))
    main = (root / "deploy/docker/whisper/whisper_service/__main__.py").read_text(encoding="utf-8")
    low, high = map(int, re.search(r"not (\d+) <= settings.threads <= (\d+)", main).groups())
    assert low <= threads <= high


def test_the_library_thread_pools_follow_whisper_threads():
    """2026-10-17: the image kept OMP/MKL/OpenBLAS/NumExpr at 4 after WHISPER_THREADS went to 8."""
    import re
    root = Path(__file__).resolve().parents[2]
    compose = (root / "deploy/docker/compose.yaml").read_text(encoding="utf-8")
    dockerfile = (root / "deploy/docker/whisper/Dockerfile").read_text(encoding="utf-8")
    threads = re.search(r'WHISPER_THREADS: "(\d+)"', compose).group(1)
    for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        assert f'{name}: "{threads}"' in compose, name
        assert f"{name}={threads}" in dockerfile, name
