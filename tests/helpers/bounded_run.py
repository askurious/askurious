"""A script run in a test that ends within its time limit, together with everything it started.

subprocess.run(..., capture_output=True, timeout=t) kills only the process it started. A shell script's children
(Git's bash on Windows starts several processes per script, the docker stand-in a waiting loop) keep the output pipes
open, and subprocess.run then waits for them without any limit after its own timeout: the test, and with it the
worker of a full run, hung for good (full suites stuck at 80 to 92 %, 2026-10-15). Here the whole tree is killed and
the run ends with subprocess.TimeoutExpired, which fails the test."""
import os
import signal
import subprocess


def _kill_tree(proc):
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=30)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    proc.kill()


def run(args, timeout, input=None, **kwargs):
    """Like subprocess.run(args, capture_output=True, timeout=timeout, **kwargs), but a timeout kills the whole
    process tree and never waits longer than a few seconds for the pipes after that."""
    kwargs.pop("capture_output", None)
    kwargs.setdefault("stdout", subprocess.PIPE)
    kwargs.setdefault("stderr", subprocess.PIPE)
    if input is not None:
        kwargs["stdin"] = subprocess.PIPE
    if os.name == "nt":
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(args, **kwargs)
    try:
        out, err = proc.communicate(input, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            out, err = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:       # a grandchild outside the tree still holds a pipe: do not wait for it
            out = err = None
        raise subprocess.TimeoutExpired(args, timeout, output=out, stderr=err) from None
    return subprocess.CompletedProcess(args, proc.returncode, out, err)
