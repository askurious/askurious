"""Bring back an encrypted uploads snapshot (lock_uploads.sh) where the private key is, e.g. on an operator's machine.

    python restore_uploads.py <folder> <stamp> <target>          # GPG: gpg asks for the passphrase (agent)
    python restore_uploads.py <folder> <stamp> <target> --age <identity file>

<folder> is a copy of ~/backups/uploads (or the folder on the server, with the key there for the moment); <stamp> a
snapshot time (newest: the last <stamp>.manifest.*), <target> an empty folder. Every file of the snapshot is
decrypted from blobs/<sha256> to its path under <target>, with its time; each content is checked against its
SHA-256 sum. Then copy <target> back into the volume as BETRIEB.md says ("Uploads zurückholen"). Prints the number of
files and ends with exit 1 if one is missing or does not match."""
import hashlib
import json
import os
import subprocess
import sys


def decrypt(path, identity=None):
    args = ["age", "-d", "-i", identity, path] if identity else ["gpg", "--quiet", "--decrypt", path]
    done = subprocess.run(args, capture_output=True)
    if done.returncode:
        raise SystemExit(f"{args[0]} could not decrypt {path}: {done.stderr.decode(errors='replace').strip()}")
    return done.stdout


def main(folder, stamp, target, identity=None):
    suffix = "age" if identity else "gpg"
    manifest = decrypt(os.path.join(folder, f"{stamp}.manifest.{suffix}"), identity).decode("utf-8")
    bad = done = 0
    for line in manifest.splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        rel = entry["path"]
        if rel.startswith("/") or ".." in rel.split("/"):
            print(f"skipped, unsafe path: {rel}", file=sys.stderr)
            bad += 1
            continue
        blob = os.path.join(folder, "blobs", f"{entry['sha256']}.{suffix}")
        if not os.path.isfile(blob):
            print(f"missing: {rel}", file=sys.stderr)
            bad += 1
            continue
        data = decrypt(blob, identity)
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            print(f"does not match its sum: {rel}", file=sys.stderr)
            bad += 1
            continue
        out = os.path.join(target, *rel.split("/"))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "wb") as f:
            f.write(data)
        os.utime(out, (entry["mtime"], entry["mtime"]))
        done += 1
    print(f"{done} files restored to {target}" + (f", {bad} not" if bad else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    args = sys.argv[1:]
    identity = args[args.index("--age") + 1] if "--age" in args else None
    plain = [a for i, a in enumerate(args) if a != "--age" and (i == 0 or args[i - 1] != "--age")]
    sys.exit(main(plain[0], plain[1], plain[2], identity))
