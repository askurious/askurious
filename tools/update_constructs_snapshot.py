"""Renew the snapshot of the construct catalogue (app/data/constructs/manifest.json) that the app seeds from.

    python tools/update_constructs_snapshot.py --release 2026.11.0
        the manifest and its signature of a release of askurious-constructs from GitHub; the signature must hold
        (public key construct_catalog.PUBLIC_KEY)
    python tools/update_constructs_snapshot.py --manifest manifest.json [--sig manifest.json.sig]
        a manifest file; with --sig its signature is checked, without it the snapshot is unsigned (say so in the PR)
    python tools/update_constructs_snapshot.py --checkout ../askurious-constructs [--name 2026.11.0-dev]
        build it in a clean checkout of askurious-constructs (its tools/build.py, unsigned, names the commit); --python
        names a Python with the catalogue's requirements (PyYAML, jsonschema) if this one lacks them

The new snapshot goes in by pull request like a lock bump; tests/analysis/test_construct_catalog.py then shows what
changed against the seeds of before. A licensed construct with wording in the manifest is refused.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import construct_catalog as catalog  # noqa: E402


def _download(url, timeout=60):
    import requests

    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response.content


def from_release(name):
    base = f"{catalog.RELEASES}/download/{name}"
    data = _download(f"{base}/manifest.json")
    signature = _download(f"{base}/manifest.json.sig").decode("utf-8")
    comment = catalog.verify_signature(data, signature)
    return data, f"signature verified ({comment})"


def from_file(path, sig=None):
    with open(path, "rb") as f:
        data = f.read()
    if not sig:
        return data, "unsigned (no --sig)"
    with open(sig, encoding="utf-8") as f:
        comment = catalog.verify_signature(data, f.read())
    return data, f"signature verified ({comment})"


def from_checkout(folder, name, python=sys.executable):
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "manifest.json")
        run = subprocess.run([python, os.path.join(folder, "tools", "build.py"), "--root", folder,
                              "--release", name, "--out", out], capture_output=True, text=True, timeout=600)
        if run.returncode:
            raise SystemExit(run.stdout + run.stderr)
        with open(out, "rb") as f:
            return f.read(), "built from a checkout, unsigned"


def write(data, target=catalog.PATH):
    manifest = json.loads(data.decode("utf-8"))
    problems = catalog.check_manifest(manifest)
    if problems:
        raise SystemExit("not taken:\n" + "\n".join(problems))
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
        f.write("\n")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--release", help="release name of askurious-constructs, e.g. 2026.11.0")
    source.add_argument("--manifest", help="a manifest.json file")
    source.add_argument("--checkout", help="a clean checkout of askurious-constructs")
    parser.add_argument("--sig", help="the signature of --manifest (manifest.json.sig)")
    parser.add_argument("--name", default="", help="release name for --checkout (default: <YYYY.MM>.0-dev)")
    parser.add_argument("--python", default=sys.executable, help="Python for --checkout")
    parser.add_argument("--out", default=catalog.PATH, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.release:
            data, how = from_release(args.release)
        elif args.manifest:
            data, how = from_file(args.manifest, args.sig)
        else:
            import datetime

            name = args.name or datetime.date.today().strftime("%Y.%m") + ".0-dev"
            data, how = from_checkout(os.path.abspath(args.checkout), name, args.python)
    except ValueError as exc:
        raise SystemExit(f"signature: {exc}") from exc
    manifest = write(data, args.out)
    print(f"{args.out}: release {manifest.get('release')}, commit {manifest.get('commit') or '-'}, "
          f"{len(manifest['constructs'])} constructs; {how}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
