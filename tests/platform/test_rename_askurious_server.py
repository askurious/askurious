"""Rename to Askurious (2026-10-09), the server part: the Docker server answers under askurious.de, and the names
from before (queryous.de, ask.queryous.de, status.queryous.de) only redirect there, path and query kept."""
import os
import re

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _read(*parts):
    with open(os.path.join(HERE, *parts), encoding="utf-8") as f:
        return f.read()


def test_new_names_are_the_default():
    compose = _read("deploy", "docker", "compose.yaml")
    deploy = _read("deploy", "docker", "deploy-docker.sh")
    for name, host in (("RESEARCHER_HOST", "askurious.de"), ("PARTICIPANT_HOST", "ask.askurious.de"),
                       ("STATUS_HOST", "status.askurious.de")):
        assert f"${{{name}:-{host}}}" in compose
        assert f'{name}="${{{name}:-{host}}}"' in deploy
    assert "queryous.de" not in re.sub(r"OLD_\w+: \$\{OLD_\w+:-[\w.]+\}", "", compose)


# the old names' redirects in nginx: tests/platform/test_audit9b_q7_betrieb.py (one server block per name, B1 and B4)
