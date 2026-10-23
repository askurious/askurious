"""One atomic, private checkpoint per job; never shared between recordings."""
import hashlib
import json
import os
from pathlib import Path

STAGES = ('transcribing', 'aligning', 'diarizing')
MAX_BYTES = 16 * 1024 * 1024
VERSION = 1


class Checkpoint:
    def __init__(self, job, manifest, versions):
        self.path = Path(job['checkpoint']) if job.get('checkpoint') else None
        self.completed, self.result = {}, None
        self.identity = hashlib.sha256(json.dumps(
            [VERSION, self.path.name if self.path else None, job.get('fingerprint'),
             job['options'], job['threads'], manifest, versions],
            sort_keys=True).encode()).hexdigest()
        if self.path and job.get('fingerprint'):
            try:
                if self.path.stat().st_size > MAX_BYTES:
                    return
                saved = json.loads(self.path.read_text(encoding='utf-8'))
                completed = saved['completed']
                enabled = [stage for stage in STAGES if stage == 'transcribing' or
                           job['options']['align' if stage == 'aligning' else 'diarize']]
                if (saved['identity'] == self.identity and isinstance(saved['result'], dict)
                        and isinstance(saved['result'].get('segments'), list)
                        and list(completed) == enabled[:len(completed)] and completed
                        and all(type(v) in (int, float) and 0 <= v < 21600 for v in completed.values())):
                    self.completed, self.result = completed, saved['result']
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                pass  # A torn, corrupt or incompatible checkpoint is recomputed.

    def save(self, stage, result, seconds):
        self.completed[stage], self.result = seconds, result
        if not self.path:
            return
        value = json.dumps({'identity': self.identity, 'completed': self.completed, 'result': result},
                           ensure_ascii=False, allow_nan=False)
        if len(value.encode()) > MAX_BYTES:
            raise ValueError('checkpoint_too_large')
        temporary = self.path.with_suffix('.checkpoint.tmp')
        with temporary.open('w', encoding='utf-8') as target:
            os.chmod(temporary, 0o600)
            target.write(value)
            target.flush()
            os.fsync(target.fileno())
        temporary.replace(self.path)
