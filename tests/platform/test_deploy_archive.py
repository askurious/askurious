"""Everything the Dockerfile copies goes up with the deploy's archive (deploy-docker.sh, git archive). #154 added
LICENSE, LICENSE.de.txt and NOTICE to the image, the archive left them out, and the build on the server stopped
("/LICENSE": not found, deploy 2026-10-29)."""
import os
import re

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _read(*parts):
    with open(os.path.join(HERE, *parts), encoding="utf-8") as f:
        return f.read()


def _archived():
    script = _read("deploy", "docker", "deploy-docker.sh")
    m = re.search(r'git -C "\$HERE" archive --format=tar HEAD (.*?)>', script, re.S)
    assert m, "the git archive line of deploy-docker.sh"
    return [t for t in m.group(1).replace("\\n", " ").split() if not t.startswith('"')]


def _copied():
    sources = []
    for line in _read("deploy", "docker", "Dockerfile").splitlines():
        m = re.match(r"(?:COPY|ADD)\s+(?!--from)(.*)", line.strip())
        if m:
            sources += [s for s in m.group(1).split()[:-1] if not s.startswith("--")]
    return sources


def test_every_file_the_dockerfile_copies_goes_up_with_the_archive():
    archived = _archived()
    # modules/ joins the archive by "${MODULES_DIR[@]}" where the checkout has it, the wheels come from MODULES_SRC
    missing = [s for s in _copied() if s != "modules"
               and not any(s == a or s.startswith(a.rstrip("/") + "/") for a in archived)]
    assert not missing, f"deploy-docker.sh's archive lacks what the Dockerfile copies: {missing}"
