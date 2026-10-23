"""Takes the screenshots of the handbook (/docs/) anew, once with the German and once with the English researcher UI.

    python tools/handbook_shots/run.py                 # both languages, all shots
    python tools/handbook_shots/run.py --lang en       # one language
    python tools/handbook_shots/run.py --only editor,quotas

For each language: a fresh throwaway database in a temporary folder (never instance/app.db), the demo state from
seed.py, the app on http://localhost:5070 (local only, DEV_LOGIN), headless Microsoft Edge driven by shots.mjs over the
DevTools protocol; the pictures go to app/static/handbook/<lang>/<name>.jpg. Server and Edge are stopped at the end.
Needs Python with the app's requirements, Node 22 or later and Microsoft Edge; installs nothing.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
import shotapp  # noqa: E402

EDGE = os.environ.get("EDGE_PATH", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
# below normal priority on Windows: the machine is shared with other work
LOW = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)


def answering(url):
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return r.status < 500
    except Exception:  # noqa: BLE001
        return False


def run_language(lang, work_root, only, node):
    work = os.path.join(work_root, lang)
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    print(f"[{lang}] seeding {work}")
    subprocess.run([sys.executable, os.path.join(HERE, "seed.py"), work, lang], check=True, cwd=ROOT, env=env,
                   creationflags=LOW)
    log = open(os.path.join(work, "server.log"), "w", encoding="utf-8")
    server = subprocess.Popen([sys.executable, os.path.join(HERE, "serve.py"), work], cwd=ROOT, env=env, stdout=log,
                              stderr=subprocess.STDOUT, creationflags=LOW)
    base = f"http://localhost:{shotapp.PORT}"
    try:
        for _ in range(120):
            if answering(base + "/"):
                break
            if server.poll() is not None:
                raise SystemExit(f"server stopped, see {log.name}")
            time.sleep(0.5)
        else:
            raise SystemExit(f"server did not answer, see {log.name}")
        out = os.path.join(ROOT, "app", "static", "handbook", lang)
        cmd = [node, os.path.join(HERE, "shots.mjs"), os.path.join(work, "seed.json"), out, "--base", base, "--edge", EDGE]
        if only:
            cmd += ["--only", only]
        print(f"[{lang}] shooting into {out}")
        subprocess.run(cmd, check=True, cwd=HERE, creationflags=LOW)
    finally:
        server.terminate()
        try:
            server.wait(10)
        except subprocess.TimeoutExpired:
            server.kill()
        log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--lang", default="de,en", help="de, en or de,en (default)")
    parser.add_argument("--only", default="", help="comma-separated shot names, e.g. editor,quotas")
    parser.add_argument("--work", default=os.path.join(tempfile.gettempdir(), "askurious-handbook-shots"),
                        help="folder for the throwaway databases (default: a folder in the system's temp folder)")
    args = parser.parse_args()
    node = shutil.which("node")
    if not node:
        raise SystemExit("Node.js not found")
    if not os.path.exists(EDGE):
        raise SystemExit(f"Microsoft Edge not found at {EDGE} (set EDGE_PATH)")
    if answering(f"http://localhost:{shotapp.PORT}/"):
        raise SystemExit(f"something already runs on port {shotapp.PORT}; stop it first (or set SHOTS_PORT)")
    for lang in [x.strip() for x in args.lang.split(",") if x.strip()]:
        if lang not in ("de", "en"):
            raise SystemExit(f"unknown language {lang}")
        run_language(lang, args.work, args.only, node)
    print("done")


if __name__ == "__main__":
    main()
