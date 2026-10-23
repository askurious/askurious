"""Resume actual pipeline control flow without downloading ML models or loading the host."""
import io
import json
import sys
import time
from collections import namedtuple
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'deploy/docker/whisper'))
from whisper_service import estimates, pipeline, worker
from whisper_service.queue import Queue
from whisper_service.settings import Settings, options


@pytest.fixture
def queue(tmp_path, monkeypatch):
    monkeypatch.setattr(estimates, 'duration', lambda path: 60)
    return Queue(Settings(tmp_path / 'state', tmp_path / 'models', 'x' * 48, min_free_mb=0))


@pytest.fixture
def ml(tmp_path, monkeypatch):
    calls = {'asr': 0, 'align': 0, 'diarize': 0}
    failure = {'stage': None}
    def transcribe(*args, **kwargs):
        calls['asr'] += 1
        return {'segments': [{'start': 0, 'end': 1, 'text': 'Grüße'}]}
    def align(segments, *args, **kwargs):
        calls['align'] += 1
        if failure['stage'] == 'align':
            raise RuntimeError('synthetic alignment failure')
        return {'segments': [dict(segments[0], words=[{'word': 'Grüße', 'start': 0, 'end': 1}])]}
    def diarize(*args, **kwargs):
        calls['diarize'] += 1
        if failure['stage'] == 'diarize':
            raise RuntimeError('synthetic diarization failure')
        return SimpleNamespace(speaker_diarization=SimpleNamespace(itertracks=lambda **kw: [
            (SimpleNamespace(start=0, end=1), None, 'SPEAKER_00')]))
    def assign(frame, result, **kwargs):
        result['segments'][0]['words'][0]['speaker'] = 'SPEAKER_00'
        return result
    model = SimpleNamespace(options=namedtuple('Options', 'initial_prompt')(None), transcribe=transcribe,
                            vad_model=SimpleNamespace(vad_pipeline=None))
    whisper = SimpleNamespace(load_model=lambda *a, **kw: model,
                              load_align_model=lambda *a, **kw: (None, None), align=align)
    torch = SimpleNamespace(set_num_threads=lambda n: None, set_num_interop_threads=lambda n: None,
                            hub=SimpleNamespace(load=lambda *a, **kw: (None, [None])), device=lambda v: v,
                            from_numpy=lambda v: SimpleNamespace(unsqueeze=lambda n: None))
    pyannote = SimpleNamespace(Pipeline=SimpleNamespace(from_pretrained=lambda path: SimpleNamespace(
        to=lambda device: None, __call__=diarize)))
    class Diarizer:
        def to(self, device):
            return self
        __call__ = staticmethod(diarize)
    pyannote.Pipeline.from_pretrained = lambda path: Diarizer()
    class Vad:
        def __init__(self, *args):
            pass
    for name, module in {
        'torch': torch, 'whisperx': whisper, 'pyannote.audio': pyannote,
        'whisperx.diarize': SimpleNamespace(assign_word_speakers=assign),
        'whisperx.vads.silero': SimpleNamespace(Silero=Vad),
        'whisperx.vads.vad': SimpleNamespace(Vad=Vad),
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(pipeline.importlib.metadata, 'version', lambda name: 'test-version')
    monkeypatch.setattr(pipeline.subprocess, 'run', lambda *a, **kw: SimpleNamespace(stdout=b'\0\0' * 16000 * 60))
    monkeypatch.setattr(pipeline, 'trim_memory', lambda: None)
    monkeypatch.setattr(pipeline.logging, 'disable', lambda level: None)
    monkeypatch.setattr(pipeline, 'CACHE', pipeline.ModelCache())
    monkeypatch.setattr(pipeline, 'THREADS_READY', False)
    root = tmp_path / 'models'
    root.mkdir()
    (root / 'manifest.json').write_text(json.dumps({'models': {'small': 'small'}, 'alignment': {'de': 'alignment/de'}, 'vad': 'vad'}))
    job = {'audio': 'unused', 'models': str(root), 'options': options({}), 'threads': 4, 'max_seconds': 3600,
           'fingerprint': 'one-recording-and-options', 'checkpoint': str(tmp_path / 'job.checkpoint'),
           'progress': str(tmp_path / 'job.progress')}
    return job, calls, failure


@pytest.mark.parametrize('failed,expected', [('align', {'asr': 1, 'align': 2, 'diarize': 1}),
                                           ('diarize', {'asr': 1, 'align': 1, 'diarize': 2})])
def test_failed_phase_resumes_without_repeating_completed_ml(ml, failed, expected):
    job, calls, failure = ml
    failure['stage'] = failed
    with pytest.raises(RuntimeError):
        pipeline.process(job)
    assert Path(job['checkpoint']).exists()
    failure['stage'] = None
    result = pipeline.process(dict(job))
    assert calls == expected and result['text'] == 'Grüße'
    assert result['segments'][0]['speaker'] == 'SPEAKER_00'
    assert set(json.loads(Path(job['progress']).read_text())['completed']) == set(estimates.STAGES)
    # Recovered after the final atomic checkpoint but before publishing the result.
    assert pipeline.process(dict(job))['text'] == 'Grüße'
    assert calls == expected


@pytest.mark.parametrize('change', ['fingerprint', 'options', 'manifest', 'corrupt', 'other-job'])
def test_incompatible_or_corrupt_checkpoint_is_recomputed(ml, change):
    job, calls, failure = ml
    pipeline.process(job)
    if change == 'fingerprint':
        job['fingerprint'] = 'another-recording'
    elif change == 'options':
        job['options']['prompt'] = 'another prompt'
    elif change == 'manifest':
        path = Path(job['models']) / 'manifest.json'
        path.write_text(path.read_text().replace('"vad"}', '"other-vad"}'))
    elif change == 'other-job':
        copied = Path(job['checkpoint']).with_name('different-job.checkpoint')
        copied.write_bytes(Path(job['checkpoint']).read_bytes())
        job['checkpoint'] = str(copied)
    else:
        Path(job['checkpoint']).write_text('{partial')
    pipeline.process(job)
    assert calls == {'asr': 2, 'align': 2, 'diarize': 2}


def test_german_alignment_never_falls_back_to_the_nc_default(ml):
    """Licence audit 2026-10-23, O6: models prepared before 2026-10-16 have no German alignment entry; WhisperX's
    default for German (VOXPOPULI_ASR_BASE_10K_DE) is CC BY-NC 4.0, so the job stops instead of loading it."""
    job, calls, failure = ml
    path = Path(job['models']) / 'manifest.json'
    path.write_text(json.dumps({'models': {'small': 'small'}, 'vad': 'vad'}))
    with pytest.raises(RuntimeError, match='prepare.py'):
        pipeline.process(job)
    assert calls['align'] == 0


def submit(queue):
    return queue.submit(io.BytesIO(b'synthetic'), options({}))


def progress(queue, job, stage='diarizing', completed=None):
    data = {'stage': stage, 'stage_started': time.time() - 10, 'duration': 60,
            'completed': completed if completed is not None else {'transcribing': 180, 'aligning': 18}}
    (queue.files / f'{job}.progress').write_text(json.dumps(data))
    queue.observe_duration(job)
    return data


def test_worker_retries_downstream_failure_once_and_purges_terminal_failure(queue, monkeypatch):
    job = submit(queue)
    checkpoint = queue.files / f'{job}.checkpoint'
    child = SimpleNamespace(returncode=1, poll=lambda: 1)
    def launch(*a, **kw):
        checkpoint.write_text('{"synthetic":"checkpoint"}')
        return child
    monkeypatch.setattr(worker.subprocess, 'Popen', launch)
    monkeypatch.setattr(worker, 'oom_kills', lambda: 0)
    worker.execute(queue, queue.claim())
    assert queue.get(job)['state'] == 'queued' and queue.get(job)['failures'] == 1
    assert checkpoint.exists() and queue.audio(job).exists()
    worker.execute(queue, queue.claim())
    assert queue.get(job)['state'] == 'failed' and list(queue.files.iterdir()) == []


def test_worker_retry_finishes_actual_pipeline_without_repeating_completed_phases(queue, ml, monkeypatch):
    _, calls, failure = ml
    job = submit(queue)
    failure['stage'] = 'diarize'
    def launch(arguments, **kwargs):
        payload = json.loads(Path(arguments[-1]).read_text())
        try:
            pipeline.write_result(payload)
            code = 0
        except RuntimeError:
            code = 1
        return SimpleNamespace(returncode=code, poll=lambda: code)
    monkeypatch.setattr(worker.subprocess, 'Popen', launch)
    monkeypatch.setattr(worker, 'oom_kills', lambda: 0)
    worker.execute(queue, queue.claim())
    assert queue.get(job)['state'] == 'queued'
    failure['stage'] = None
    worker.execute(queue, queue.claim())
    assert queue.get(job)['state'] == 'done'
    assert calls == {'asr': 1, 'align': 1, 'diarize': 2}
    assert list(queue.files.iterdir()) == []


@pytest.mark.parametrize('end', ['cancel', 'finish', 'expire'])
def test_checkpoints_erased_on_every_terminal_path(queue, end):
    job = submit(queue)
    queue.claim()
    for suffix in ('checkpoint', 'checkpoint.tmp'):
        (queue.files / f'{job}.{suffix}').write_text('private transcript')
    if end == 'cancel':
        queue.cancel(job)
    elif end == 'finish':
        queue.finish(job, error='memory_limit')
    else:
        queue.sweep(time.time() + 86401)
    assert list(queue.files.iterdir()) == []


def test_restart_and_pressure_keep_checkpoint_but_do_not_spend_retry(queue):
    job = submit(queue)
    queue.claim()
    path = queue.files / f'{job}.checkpoint'
    path.write_text('private checkpoint')
    progress(queue, job)
    queue.postpone(job)
    assert path.exists() and queue.get(job)['failures'] == 0
    queue.claim()
    restarted = Queue(queue.settings)
    restarted.recover()
    assert restarted.get(job)['state'] == 'queued' and path.exists()


def test_phase_eta_uses_only_remaining_phase_and_current_phase_elapsed(queue):
    job = submit(queue)
    queue.claim()
    progress(queue, job)
    value = queue.estimate(job)
    # 60*3 seconds diarisation minus 10 elapsed.
    assert 169 <= value['seconds'] <= 170 and 313 <= value['high_seconds'] <= 314
    assert not value['sampled']
    # Elapsed ASR time must never be subtracted from diarisation.
    with queue.connect() as db:
        db.execute('UPDATE jobs SET started=? WHERE id=?', (time.time() - 5000, job))
    assert not queue.estimate(job)['overdue']


def test_phase_learning_survives_restart_without_learning_queue_or_interruption_time(queue):
    for _ in range(3):
        job = submit(queue)
        queue.claim()
        progress(queue, job, completed={'transcribing': 180, 'aligning': 18, 'diarizing': 42})
        with queue.connect() as db:
            db.execute('UPDATE jobs SET attempts=2,started=? WHERE id=?', (time.time() - 9000, job))
        queue.finish(job, result={'text': 'private'})
    restarted = Queue(queue.settings)
    with restarted.connect() as db:
        learned = [dict(row) for row in db.execute('SELECT * FROM phase_timings')]
        assert db.execute('SELECT COUNT(*) FROM timings').fetchone()[0] == 0
    assert {row['stage']: row['rate'] for row in learned} == pytest.approx({'transcribing': 3, 'aligning': .3, 'diarizing': .7})
    assert all(row['samples'] == 3 for row in learned)
    assert 'private' not in json.dumps(learned)
    job = submit(restarted)
    value = restarted.estimate(job)
    assert value['sampled'] and 259 <= value['seconds'] <= 260
    restarted.sweep(time.time() + 7 * 86400 + 1)
    with restarted.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM phase_timings').fetchone()[0] == 0


def test_phase_eta_still_includes_predecessors_and_unknown_duration(queue):
    first, second = submit(queue), submit(queue)
    queue.claim()
    progress(queue, first)
    value = queue.estimate(second)
    assert 429 <= value['seconds'] <= 430
    with queue.connect() as db:
        db.execute('UPDATE jobs SET duration=NULL WHERE id=?', (first,))
    assert queue.estimate(second) is None


def test_phase_eta_subtracts_current_phase_only_and_reports_overdue():
    config = options({})
    row = {'id': 'one', 'state': 'running', 'started': 1, 'duration': 600,
           'options': json.dumps(config), 'phases': json.dumps({
               'stage': 'aligning', 'stage_started': 900, 'completed': {'transcribing': 1800}})}
    learned = {(estimates.key(config, 4), stage): {'rate': rate, 'samples': 3}
               for stage, rate in [('transcribing', 3), ('aligning', .5), ('diarizing', 1)]}
    value = estimates.estimate([row], 'one', {}, 4, 1000, learned)
    assert value == {'seconds': 800, 'low_seconds': 440, 'high_seconds': 1520,
                     'sampled': True, 'overdue': False}
    # Even when alignment is overdue, the entire upcoming diarisation remains in the estimate.
    value = estimates.estimate([row], 'one', {}, 4, 1500, learned)
    assert value['overdue'] and value['seconds'] == 600 and value['high_seconds'] == 1080


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -1, 'private transcript'])
def test_invalid_phase_metadata_is_not_stored_or_trained(bad):
    assert estimates.metadata({'stage': 'aligning', 'stage_started': time.time(),
                               'completed': {'transcribing': bad}}) == {}


@pytest.mark.parametrize('stored,expected', [({'de': 'alignment/de'}, 'alignment/de')])
def test_german_alignment_uses_the_model_prepare_stored(ml, stored, expected):
    """German aligns with the free model prepare.py stored in the manifest, not WhisperX's CC BY-NC default
    (copyright audit 2026-10-16, U2); a manifest without it stops (test_german_alignment_never_falls_back_...)."""
    job, calls, failure = ml
    root = Path(job['models'])
    manifest = {'models': {'small': 'small'}, 'vad': 'vad'}
    if stored:
        manifest['alignment'] = stored
    (root / 'manifest.json').write_text(json.dumps(manifest))
    job['options'] = dict(job['options'], language='de')
    seen = []
    sys.modules['whisperx'].load_align_model = lambda language, device, model_name=None, **kw: (
        seen.append((language, model_name)) or (None, None))
    pipeline.process(job)
    assert seen == [('de', str(root / expected) if expected else None)]
