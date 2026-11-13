"""The prepared models checked against their hashes (manifest.json, written by prepare.py) before the service starts.

The accepted vulnerabilities of torch and transformers (osv-accepted.txt, until 2027-01-31) rest on "only the hashed,
read-only models are loaded"; this makes it hold: a file of the model volume that is missing, changed or not in the
manifest stops the start (operations audit 2026-11-11, Q6)."""
import hashlib
from pathlib import Path


def problems(root, manifest):
    """[text] of what does not match: [] when every file of the volume is in the manifest with its SHA-256."""
    root = Path(root)
    expected = manifest.get("sha256") or {}
    if not expected:
        return ["manifest.json lists no hashes: prepare the model volume again (python -m whisper_service.prepare)"]
    found = []
    seen = set()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name in ("manifest.json", "manifest.tmp") or ".cache" in path.parts:
            continue
        name = str(path.relative_to(root)).replace("\\", "/")
        seen.add(name)
        want = expected.get(name)
        if want is None:
            found.append(f"not in the manifest: {name}")
            continue
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != want:
            found.append(f"hash differs: {name}")
    found += [f"missing: {name}" for name in sorted(set(expected) - seen)]
    return found
