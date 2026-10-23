"""Entry point for Plesk's Python hosting (Phusion Passenger).

netcup web hosting has no pip, so dependencies are shipped pre-built in ./vendor
(pip install --target vendor -r requirements.lock for the server's Python). Locally, the .venv is used instead.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VENDOR = os.path.join(HERE, "vendor")

if os.path.isdir(VENDOR):
    sys.path.insert(0, VENDOR)
sys.path.insert(0, HERE)

from app import create_app  # noqa: E402

application = create_app(server=True)  # a server: PUBLIC_BASE_URL must be in its .env (CFG-01)
