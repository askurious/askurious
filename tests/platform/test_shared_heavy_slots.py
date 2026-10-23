"""Heavy computation slots apply across independent processes, not once per gunicorn worker."""
import subprocess
import sys

from flask import Flask

from app import heavy


def test_an_independent_process_cannot_take_an_occupied_slot(tmp_path):
    app = Flask(__name__)
    app.config.update(UPLOAD_FOLDER=str(tmp_path), HEAVY_PARALLEL=1, HEAVY_WAIT=0)
    script = ("from flask import Flask; from app import heavy; import sys; "
              "app=Flask('child'); app.config.update(UPLOAD_FOLDER=sys.argv[1],HEAVY_PARALLEL=1,HEAVY_WAIT=0); "
              "ctx=app.test_request_context(); ctx.push(); print(heavy.hold()); heavy.release(); ctx.pop()")
    with app.test_request_context():
        assert heavy.hold()
        try:
            result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], capture_output=True, text=True, timeout=15)
            assert result.returncode == 0 and result.stdout.strip() == "False"
        finally:
            heavy.release()
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0 and result.stdout.strip() == "True"
