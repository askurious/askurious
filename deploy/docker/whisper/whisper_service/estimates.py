"""Approximate timings, aggregated by runtime settings without job or content identifiers."""
import json
import math
import subprocess

from .checkpoints import STAGES


def metadata(data):
    """Whitelist the tiny timing snapshot before storing it in the spool database."""
    completed = data.get('completed')
    if not isinstance(completed, dict) or data.get('stage') not in (*STAGES, 'loading', 'decoding'):
        return {}
    if any(stage not in STAGES or type(value) not in (int, float) or not 0 <= value < 21600
           for stage, value in completed.items()):
        return {}
    started = data.get('stage_started')
    if type(started) not in (int, float) or not math.isfinite(started) or started <= 0:
        return {}
    return {'stage': data['stage'], 'stage_started': started, 'completed': completed}


def duration(path):
    try:
        reply = subprocess.run(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe', '-show_entries', 'format=duration:stream=duration',
                                '-of', 'json', str(path)], capture_output=True, timeout=3, check=True)
        data = json.loads(reply.stdout)
        values = [data.get('format', {}).get('duration')] + [s.get('duration') for s in data.get('streams', [])]
        for item in values:
            try:
                value = float(item)
            except (TypeError, ValueError):
                continue
            if math.isfinite(value) and 0 < value <= 3600:
                return value
        return None
    except (OSError, ValueError, StopIteration, subprocess.SubprocessError):
        return None


def key(options, threads):
    return json.dumps([options.get(name) for name in ('model', 'language', 'align', 'diarize')] + [threads])


def seconds(row, timings, threads, now, phase_timings=None):
    if not row.get('duration'):
        return None
    options = json.loads(row['options'])
    phases = metadata(json.loads(row.get('phases') or '{}'))
    setting = key(options, threads)
    if phases or any((setting, stage) in (phase_timings or {}) for stage in STAGES):
        return phase_seconds(row, options, phases, phase_timings or {}, threads, now)
    timing = timings.get(key(options, threads))
    # Cold short-audio measurements are deliberately only a coarse starting point.
    rate = timing['rate'] if timing else (4.0 if options['model'] == 'small' else 5.3)
    expected = 20 + row['duration'] * rate
    elapsed = max(0, now - row['started']) if row['state'] == 'running' and row.get('started') else 0
    return {'seconds': max(60, expected - elapsed), 'sampled': bool(timing and timing['samples'] >= 3),
            'low_seconds': max(60, expected * .6 - elapsed), 'high_seconds': max(120, expected * 1.8 - elapsed),
            'overdue': elapsed > expected * 1.8}


def phase_seconds(row, options, progress, timings, threads, now):
    # Coarse split of the existing synthetic 10/30-minute CPU benchmarks, with headroom.
    defaults = (.5, .5, 3.0) if options['model'] == 'small' else (1.6, .4, 3.3)
    enabled = [stage for stage, flag in zip(STAGES, (True, options['align'], options['diarize'])) if flag]
    completed = progress.get('completed', {})
    stage = progress.get('stage', 'loading')
    elapsed = max(0, now - progress.get('stage_started', now)) if row['state'] == 'running' else 0
    # Loading/decoding remain a coarse allowance. Completed ML stages are never charged again.
    overhead = 20 if row['state'] == 'queued' or stage in ('loading', 'decoding') else 0
    total, lower, upper = max(0, overhead - elapsed), max(0, overhead * .6 - elapsed), max(0, overhead * 1.8 - elapsed)
    sampled, overdue = True, False
    for name in enabled:
        if name in completed:
            continue
        timing = timings.get((key(options, threads), name))
        expected = row['duration'] * (timing['rate'] if timing else defaults[STAGES.index(name)])
        spent = elapsed if stage == name else 0
        total += max(0, expected - spent)
        lower += max(0, expected * .6 - spent)
        upper += max(0, expected * 1.8 - spent)
        sampled = sampled and bool(timing and timing['samples'] >= 3)
        overdue = overdue or spent > expected * 1.8
    return {'seconds': max(60, total), 'low_seconds': max(60, lower), 'high_seconds': max(120, upper),
            'sampled': sampled, 'overdue': overdue}


def estimate(rows, target, timings, threads, now, phase_timings=None):
    total, lower, upper, sampled, overdue = 0, 0, 0, True, False
    for row in rows:
        value = seconds(row, timings, threads, now, phase_timings)
        if value is None:
            return None  # An unknown predecessor must never become zero waiting time.
        total += value['seconds']
        lower += value['low_seconds']
        upper += value['high_seconds']
        sampled = sampled and value['sampled']
        overdue = overdue or value['overdue']
        if row['id'] == target:
            return {'seconds': min(7 * 86400, math.ceil(total)),
                    'low_seconds': min(7 * 86400, math.ceil(lower)), 'high_seconds': min(7 * 86400, math.ceil(upper)),
                    'sampled': sampled, 'overdue': overdue}
    return None
