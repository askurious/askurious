import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def oom_kills():
    try:
        return int(dict(line.split() for line in Path('/sys/fs/cgroup/memory.events').read_text().splitlines())['oom_kill'])
    except (OSError, ValueError, KeyError):
        return 0


def room(settings):
    """Host indicators only. Hard Docker limits remain effective throughout a job."""
    try:
        with open("/proc/meminfo") as source:
            available = next(int(line.split()[1]) // 1024 for line in source if line.startswith("MemAvailable:"))
        if available < settings.min_host_memory_mb or os.getloadavg()[0] > settings.max_host_load:
            return False
        if settings.app_health:
            with urllib.request.urlopen(settings.app_health, timeout=2) as response:
                if response.status != 200:
                    return False
    except (OSError, StopIteration, urllib.error.URLError):
        return False
    return True


def stop(process):
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            process.wait()
            return
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


class Resident:
    def __init__(self, settings):
        self.settings, self.process = settings, None
        self.used, self.finished = 0, time.monotonic()

    def close(self):
        if self.process is not None:
            stop(self.process)
            if self.process.stdin:
                self.process.stdin.close()
        self.process, self.used = None, 0

    def send(self, path):
        if self.process is None or self.process.poll() is not None:
            self.close()
            self.process = subprocess.Popen([sys.executable, "-m", "whisper_service.pipeline", "--resident"],
                                            stdin=subprocess.PIPE, text=True, stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL, start_new_session=True)
        self.process.stdin.write(str(path) + "\n")
        self.process.stdin.flush()
        return self.process

    def expire(self, pressured=False):
        if self.process is None:
            return
        try:
            with open(f"/proc/{self.process.pid}/status") as source:
                rss = next(int(line.split()[1]) // 1024 for line in source if line.startswith("VmRSS:"))
        except (OSError, StopIteration):
            rss = 0
        if pressured or self.used >= self.settings.cache_jobs or rss > self.settings.cache_memory_mb or time.monotonic() - self.finished >= self.settings.cache_idle_seconds:
            self.close()


def execute(queue, job, allowed=room, resident=None):
    job_id = job["id"]
    # A crash can leave an old completion marker. Never accept it as this attempt's reply.
    for suffix in ("input", "result", "result.tmp", "progress", "progress.tmp", "complete", "complete.tmp"):
        (queue.files / f"{job_id}.{suffix}").unlink(missing_ok=True)
    input_file = queue.files / f"{job_id}.input"
    input_file.write_text(json.dumps({"audio": str(queue.audio(job_id)), "models": str(queue.settings.models),
                                    "threads": queue.settings.threads, "max_seconds": queue.settings.max_seconds,
                                    "options": json.loads(job["options"]), "output": str(queue.result_file(job_id)),
                                    "progress": str(queue.files / f"{job_id}.progress"),
                                    "checkpoint": str(queue.files / f"{job_id}.checkpoint"), "fingerprint": job['fingerprint'],
                                    "complete": str(queue.files / f"{job_id}.complete")}))
    completion = queue.files / f"{job_id}.complete"
    before_oom = oom_kills()
    process = resident.send(input_file) if resident else subprocess.Popen([sys.executable, "-m", "whisper_service.pipeline", str(input_file)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    began = time.monotonic()
    swept = began
    try:
        while process.poll() is None:
            queue.observe_duration(job_id)
            if resident and completion.exists():
                break
            if time.monotonic() - swept >= 60:
                queue.sweep()
                swept = time.monotonic()
            row = queue.get(job_id)
            if row is None or row["state"] == "cancelled":
                stop(process)
                queue.discard_files(job_id)
                return
            if time.monotonic() - began > queue.settings.job_seconds:
                stop(process)
                queue.finish(job_id, error="processing_timeout")
                return
            if not allowed(queue.settings):
                stop(process)
                queue.postpone(job_id)
                return
            time.sleep(1)
        output = queue.result_file(job_id)
        queue.observe_duration(job_id)  # Include the final phase even if the child finished within one poll.
        code = json.loads(completion.read_text())["code"] if resident and completion.exists() else process.returncode
        if code == 0 and output.exists() and output.stat().st_size <= 16 * 1024 * 1024:
            finished = queue.finish(job_id, result=json.loads(output.read_text()))
            if resident:
                if finished:
                    resident.used += 1
                    resident.finished = time.monotonic()
                else:
                    resident.close()
        else:
            if resident:
                resident.close()
            error = "duration_limit" if code == 3 else "memory_limit" if code == 4 or oom_kills() > before_oom else "processing_failed"
            if error != 'processing_failed' or not queue.retry(job_id):
                queue.finish(job_id, error=error)
    except (OSError, ValueError):
        stop(process)
        if not queue.retry(job_id):
            queue.finish(job_id, error="processing_failed")
    finally:
        if resident and process.poll() is not None:
            resident.close()
        input_file.unlink(missing_ok=True)
        completion.unlink(missing_ok=True)
        (queue.files / f"{job_id}.progress").unlink(missing_ok=True)


def run(queue, stop_event):
    queue.recover()
    resident = Resident(queue.settings)
    try:
        while not stop_event.is_set():
            queue.wake.clear()  # Clear BEFORE checking SQLite, so a concurrent submit cannot be lost.
            queue.sweep()
            available = room(queue.settings)
            resident.expire(pressured=not available)
            if available:
                job = queue.claim()
                if job:
                    try:
                        execute(queue, job, resident=resident)
                    except Exception:
                        resident.close()
                        queue.finish(job["id"], error="processing_failed")
                    continue
            if available:
                queue.wake.wait(15)
            else:
                stop_event.wait(15)  # Submissions must not create a hot loop under host pressure.
    finally:
        resident.close()
