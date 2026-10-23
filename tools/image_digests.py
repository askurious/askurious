"""The base images of deploy/docker pinned by digest, and the way to raise them (audit 2026-10-18, CFG-05).

A tag such as python:3.12-slim moves with every rebuild of the official image: what the weekly maintenance window
pulled was whatever the tag pointed to that Sunday, unchecked. Now every FROM and image: line of deploy/docker names
its tag *and* the digest of the image index (all platforms) it was checked at: "python:3.12-slim@sha256:...".
Docker then takes exactly that image; the tag only says what it is.

    python tools/image_digests.py            list the pinned images and whether the registry has a newer state
    python tools/image_digests.py --update   write the registry's current digest of each tag into the files

--update on a developer's computer, then a PR (the local tests and the vulnerability check), merge and deploy:
that is how the images get their security fixes now (BETRIEB.md, "Images anheben"). maintenance.sh runs the plain
check each week and says in Slack when a tag has moved, so a fix waits days, not months. Exit code 1 when a tag has
moved (or the registry could not be asked), 0 when all are current.

Only the registry's public API is asked (Docker Hub, anonymous); nothing is pulled or run.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# the WhisperX image is pinned too, but built and validated by hand with its models (whisper/README.md):
# maintenance.sh reminds of its base after 60 days, this tool leaves it alone
FILES = ("deploy/docker/Dockerfile", "deploy/docker/nginx-image/Dockerfile", "deploy/docker/compose.yaml")
# an image of Docker Hub with tag and digest, in a FROM line or an image: line
PINNED = re.compile(r"(?P<lead>(?:^FROM\s+|^\s*image:\s+))(?P<name>[a-z0-9][a-z0-9._/-]*):(?P<tag>[A-Za-z0-9._-]+)"
                    r"@(?P<digest>sha256:[0-9a-f]{64})", re.M)
# images built here, never fetched (compose.yaml: pull_policy never)
LOCAL = ("askurious", "askurious-nginx", "askurious-whisperx")
TIMEOUT = 20


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def pinned():
    """[(file, name, tag, digest)] of every pinned image in FILES."""
    out = []
    for rel in FILES:
        for m in PINNED.finditer(_read(rel)):
            out.append((rel, m["name"], m["tag"], m["digest"]))
    return out


def unpinned():
    """[(file, line)] of FROM and image: lines that name a registry image without a digest (images built here and the
    stages of a multi-stage build excepted)."""
    out = []
    for rel in FILES:
        stages = set()
        for line in _read(rel).splitlines():
            stage = re.match(r"^FROM\s+(\S+)(?:\s+AS\s+(\S+))?", line, re.I)
            m = stage or re.match(r"^\s*image:\s+(\S+)", line)
            if not m:
                continue
            ref = m.group(1)
            if stage and stage.group(2):
                stages.add(stage.group(2))
            name = ref.split("@")[0].rsplit(":", 1)[0]
            if ref in stages or name in LOCAL or "${" in ref or "@sha256:" in ref:
                continue
            out.append((rel, line.strip()))
    return out


def registry_digest(name, tag):
    """The digest Docker Hub gives the tag now (the image index); raises on a network or registry error."""
    repo = name if "/" in name else f"library/{name}"
    url = f"https://hub.docker.com/v2/repositories/{repo}/tags/{tag}"
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "askurious-image-digests"}),
                                timeout=TIMEOUT) as r:
        digest = json.load(r).get("digest") or ""
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError(f"no digest for {name}:{tag}")
    return digest


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    update = "--update" in argv
    loose = unpinned()
    for rel, line in loose:
        print(f"not pinned: {rel}: {line}")
    moved, failed = [], False
    seen = {}
    for rel, name, tag, digest in pinned():
        key = (name, tag)
        try:
            now = seen[key] if key in seen else seen.setdefault(key, registry_digest(name, tag))
        except (OSError, ValueError, urllib.error.URLError) as e:
            print(f"{name}:{tag}: registry not asked ({type(e).__name__})")
            failed = True
            continue
        state = "current" if now == digest else f"moved to {now}"
        print(f"{rel}: {name}:{tag}@{digest[:19]}... {state}")
        if now != digest:
            moved.append((rel, name, tag, digest, now))
    if update:
        for rel in {m[0] for m in moved}:
            text = _read(rel)
            for r, name, tag, old, new in moved:
                if r == rel:
                    text = text.replace(f"{name}:{tag}@{old}", f"{name}:{tag}@{new}")
            with open(os.path.join(ROOT, rel), "w", encoding="utf-8", newline="") as f:
                f.write(text)
            print(f"written: {rel}")
        return 1 if failed or loose else 0
    if moved:
        print(f"{len(moved)} pinned image(s) have a newer state: python tools/image_digests.py --update, then a PR")
    return 1 if moved or failed or loose else 0


if __name__ == "__main__":
    sys.exit(main())
