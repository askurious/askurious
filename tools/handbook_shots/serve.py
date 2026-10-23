"""Serves the screenshot instance on http://localhost:5070 (local only).

    python tools/handbook_shots/serve.py <work folder>
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import shotapp  # noqa: E402

if __name__ == "__main__":
    app = shotapp.make_app(sys.argv[1])
    from werkzeug.serving import run_simple

    run_simple("localhost", shotapp.PORT, app, threaded=True, use_reloader=False, use_debugger=False)
