"""A snapshot of the uploads (volume "instance") for backup.sh, only changed files copied (Ralf 2026-10-12).

Runs inside an app container, read from stdin (no file of its own in the image):
    docker compose run --rm --no-deps -T -v ~/backups/uploads:/snap researcher python - /app/instance /snap 14 < snapshot_uploads.py

Every snapshot is a complete folder <snap>/<YYYYmmdd-HHMMSS>/ with every file of the source. A file with the same
size and modification time as in the newest snapshot before is a hard link to it, not a copy: it takes no space a
second time (like rsync --link-dest). So each snapshot can be restored on its own (copy the folder back), and only
new or changed files cost space. A file deleted in the app is in no later snapshot; the older ones go after <days>
days, the newest always stays. A broken run leaves only <stamp>.part, which the next run removes.
Prints one line: the snapshot, its files, how many were copied and their bytes.

With --staged (BACKUP_ENCRYPT_KEY set; Ralf 2026-10-21: the snapshots held images, recordings and scans readable)
the snapshot is not kept as a folder: <snap>/<stamp>.stage/ gets manifest.jsonl (path, SHA-256, size and time of
every file) and new/<sha256> for each content not yet in <snap>/blobs/. lock_uploads.sh on the server then
encrypts those into blobs/<sha256>.gpg (or .age) and the manifest into <stamp>.manifest.gpg, and removes the stage:
only new or changed contents cost space, as before, and nothing readable stays. --stamp <stamp> with a former
plain snapshot folder as source converts it into the same form (its time kept)."""
import calendar
import hashlib
import json
import os
import shutil
import sys
import time


def newest(snap):
    names = sorted(n for n in os.listdir(snap) if len(n) == 15 and n[8] == "-" and n.replace("-", "").isdigit())
    return names[-1] if names else None


def take(src, snap, stamp):
    before = newest(snap)
    old = os.path.join(snap, before) if before else None
    part = os.path.join(snap, stamp + ".part")
    files = copied = size = 0
    for root, dirs, names in os.walk(src):
        dirs.sort()
        rel = os.path.relpath(root, src)
        os.makedirs(os.path.join(part, rel), exist_ok=True)
        for name in sorted(names):
            if rel == "." and name == ".storage-backup.lock":
                continue  # live coordination inode, never restored user data
            path = os.path.join(root, name)
            if os.path.islink(path) or not os.path.isfile(path):
                continue
            target = os.path.join(part, rel, name)
            st = os.stat(path)
            prev = os.path.join(old, rel, name) if old else None
            if prev and os.path.isfile(prev):
                pst = os.stat(prev)
                if pst.st_size == st.st_size and digest(prev) == digest(path):
                    os.link(prev, target)
                    files += 1
                    continue
            shutil.copy2(path, target)
            files += 1
            copied += 1
            size += st.st_size
    os.rename(part, os.path.join(snap, stamp))
    return files, copied, size


def stage(src, snap, stamp):
    """The staged snapshot for lock_uploads.sh: manifest.jsonl and the contents the blob store does not hold yet.
    Files are read in sorted order; a file that changes while it is read is taken as read (its digest is of the
    copy, which is hashed again from the copy)."""
    blobs = os.path.join(snap, "blobs")
    known = {name.split(".", 1)[0] for name in os.listdir(blobs)} if os.path.isdir(blobs) else set()
    part = os.path.join(snap, stamp + ".stage.part")
    new = os.path.join(part, "new")
    os.makedirs(new)
    files = copied = size = 0
    with open(os.path.join(part, "manifest.jsonl"), "w", encoding="utf-8") as manifest:
        for root, dirs, names in os.walk(src):
            dirs.sort()
            if os.path.abspath(root) == os.path.abspath(snap):   # never the backup folder itself
                dirs[:] = []
                continue
            for name in sorted(names):
                path = os.path.join(root, name)
                if os.path.islink(path) or not os.path.isfile(path):
                    continue
                rel = os.path.relpath(path, src).replace(os.sep, "/")
                if rel.startswith(".storage-backup.lock"):        # the live lock of the backup window, not data
                    continue
                st = os.stat(path)
                sha = digest(path).hex()
                if sha not in known:
                    target = os.path.join(new, sha)
                    if not os.path.exists(target):
                        shutil.copy2(path, target)
                        if digest(target).hex() != sha:            # changed while copied: take the copy as it is
                            fixed = digest(target).hex()
                            os.replace(target, os.path.join(new, fixed))
                            sha = fixed
                        copied += 1
                        size += st.st_size
                files += 1
                manifest.write(json.dumps({"path": rel, "sha256": sha, "size": st.st_size, "mtime": int(st.st_mtime)},
                                          ensure_ascii=False) + "\n")
    os.rename(part, os.path.join(snap, stamp + ".stage"))
    return files, copied, size


def digest(path):
    """Size and timestamps alone cannot prove that a file's contents are unchanged."""
    with open(path, "rb") as source:
        return hashlib.file_digest(source, "sha256").digest()


def sweep(snap, days, now):
    """Snapshots older than days go, the newest always stays; leftovers of a broken run too."""
    keep = newest(snap)
    gone = []
    for name in os.listdir(snap):
        path = os.path.join(snap, name)
        if name.endswith(".part"):
            shutil.rmtree(path, ignore_errors=True)
        elif name != keep and len(name) == 15 and name.replace("-", "").isdigit() \
                and now - calendar.timegm(time.strptime(name, "%Y%m%d-%H%M%S")) > days * 86400:
            shutil.rmtree(path)
            gone.append(name)
    return gone


def main(src, snap, days, staged=False, stamp=None):
    os.makedirs(snap, exist_ok=True)
    os.chmod(snap, 0o700)
    for name in os.listdir(snap):                        # a broken run before
        if name.endswith(".part"):
            shutil.rmtree(os.path.join(snap, name), ignore_errors=True)
    stamp = stamp or time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    if staged:
        if os.path.exists(os.path.join(snap, stamp + ".stage")):
            raise SystemExit(f"{stamp}.stage exists already")
        files, copied, size = stage(src, snap, stamp)
        print(f"uploads {stamp} staged: {files} files, {copied} new ({size} bytes)")
        return
    if os.path.exists(os.path.join(snap, stamp)):
        raise SystemExit(f"{stamp} exists already")
    files, copied, size = take(src, snap, stamp)
    gone = sweep(snap, days, time.time())
    print(f"uploads {stamp}: {files} files, {copied} copied ({size} bytes)"
          + (f", removed {' '.join(sorted(gone))}" if gone else ""))


if __name__ == "__main__":
    os.umask(0o077)
    args = [a for a in sys.argv[1:]]
    staged = "--staged" in args
    stamp = args[args.index("--stamp") + 1] if "--stamp" in args else None
    plain = [a for i, a in enumerate(args) if not a.startswith("--") and (i == 0 or args[i - 1] != "--stamp")]
    main(plain[0], plain[1], float(plain[2]) if len(plain) > 2 else 14, staged=staged, stamp=stamp)
