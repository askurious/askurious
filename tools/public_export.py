"""The public state of the code: what goes into the public repository askurious/askurious (a fresh history, one
commit per export; Ralf 2026-10-19, way B). Writes the committed state of HEAD without the internal files into a
folder and says which lines there still name the operator's server or private accounts.

    python tools/public_export.py <empty folder>    write the public state there and check it
    python tools/public_export.py --check           only check what an export would carry (nothing written)
    ... --forbidden <file>                          further patterns, one "name: regular expression" per line: the
                                                    operator's server names, kept in its private repository
    ... --instruments <file>                        the installation's wordings of scales of others (instruments.json,
                                                    app/constructs.py INSTRUMENTS): none of them may go public

Exit code 1 while a check finds something: then fix it here in the code (or add the file to INTERNAL) and export
again. The operator's own files (imprint and privacy texts, .env, server notes) belong in its private repository
(askurious-instance-rwth) and come in through instance_overlay/ (instance_overlay/README.md).
"""
import io
import os
import re
import subprocess
import sys
import tarfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# files and folders that stay in the private repositories: decisions and plans, the operator's server notes, tools
# of one developer's computer
INTERNAL = (
    "decisions.md",
    "bucketlist.md",
    "deploy/docker/BETRIEB.md",
    "tools/claude-cpu-begrenzen.cmd",
    "tools/claude-cpu-begrenzen.ps1",
)
# what the public state may never name (name: pattern); the operator's own server names come with --forbidden, so
# that this file does not publish them either
FORBIDDEN = {
    "login of a web hosting": r"\bhosting\d+@",
    "private donation account": r"paypal\.me/\w+",
    "private mail address": r"[\w.+-]+@(?:gmail|googlemail|gmx|web|t-online)\.(?:com|de)\b",
    "private key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
}


def internal(path):
    return any(path == p or path.startswith(p.rstrip("/") + "/") for p in INTERNAL)


def read_forbidden(path):
    """{name: pattern} from a file of lines "name: regular expression" (# comments and empty lines left out)."""
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip() and not line.lstrip().startswith("#"):
                name, _, pattern = line.partition(":")
                out[name.strip()] = pattern.strip()
    return out


def read_instruments(path):
    """{name: pattern} for every question and statement of an instruments.json of at least 20 characters: the wording
    of scales of others stays with the installation (rights review 2026-10-21)."""
    import json

    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    out = {}
    for key, wording in (data.get("instruments") or {}).items():
        texts = list((wording.get("question") or {}).values())
        texts += [t for statement in wording.get("statements") or [] for t in statement.values()]
        for n, text in enumerate(t for t in texts if isinstance(t, str) and len(t.strip()) >= 20):
            out[f"wording of {key} ({n + 1})"] = re.escape(text.strip())
    return out


def members():
    """(path, bytes, mode) of every committed file of HEAD that goes public."""
    # core.autocrlf=false: the files as committed (LF), also from a Windows checkout (first export 2026-10-23 had CRLF)
    data = subprocess.run(["git", "-C", ROOT, "-c", "core.autocrlf=false", "archive", "--format=tar", "HEAD"],
                          capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        for info in tar:
            if info.isfile() and not internal(info.name):
                yield info.name, tar.extractfile(info).read(), info.mode


def findings(files, forbidden=FORBIDDEN):
    """[(path, line number, what)] for every line naming something forbidden; the matched text itself stays out."""
    out = []
    patterns = {name: re.compile(p) for name, p in forbidden.items()}
    for path, data, _mode in files:
        if b"\0" in data[:8000]:
            continue
        for number, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
            out += [(path, number, name) for name, rx in patterns.items() if rx.search(line)]
    return out


def main(argv):
    forbidden = dict(FORBIDDEN)
    if "--forbidden" in argv:
        at = argv.index("--forbidden")
        forbidden.update(read_forbidden(argv[at + 1]))
        argv = argv[:at] + argv[at + 2:]
    if "--instruments" in argv:
        at = argv.index("--instruments")
        forbidden.update(read_instruments(argv[at + 1]))
        argv = argv[:at] + argv[at + 2:]
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 2
    files = list(members())
    if argv[0] != "--check":
        target = argv[0]
        if os.path.exists(target) and os.listdir(target):
            print(f"{target} is not empty")
            return 2
        for path, data, mode in files:
            dest = os.path.join(target, *path.split("/"))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(data)
            os.chmod(dest, mode)
        print(f"{len(files)} files written to {target}")
    found = findings(files, forbidden)
    for path, number, name in found:
        print(f"{path}:{number}: {name}")
    print(f"{len(found)} lines to clean" if found else "clean: nothing of the operator's server or private accounts")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
