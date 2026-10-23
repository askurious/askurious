"""Ask OSV (https://osv.dev) about every package version in requirements.lock; exit code 1 if one has a known
vulnerability (security audit 2026-10-04, H4). deploy-docker.sh runs it before
the upload, there also for deploy/docker/requirements-docker.lock and the JavaScript libraries shipped in
app/static/vendor (tools/vendor_versions.txt, lines "npm name==version"); the server's maintenance window runs it
every week as well, so a vulnerability found between deploys is noticed (audit 2026-10-13, DP-10).

    python tools/check_vulnerabilities.py [requirements.lock ...] [tools/vendor_versions.txt] [--accepted FILE]

--accepted FILE: lines "<package> <vulnerability id>" that were judged and documented (the WhisperX image,
deploy/docker/whisper/osv-accepted.txt and whisper/README.md): they are listed but do not fail the check; any other
id does, also a new one for the same package (Abgleich 2026-10-15, T08-N-3).

Sends only package names and versions; needs no package beyond the standard library.
"""
import json
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIN = re.compile(r"^(?:(npm)\s+)?([A-Za-z0-9_.\-\[\]]+)==([^\s\\;]+)")


def pins(path):
    """[(name, version)] of the PyPI pins ("name==version"), [(name, version, "npm")] of lines "npm name==version"."""
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = PIN.match(line.strip())
            if m and m.group(1):
                out.append((m.group(2), m.group(3), m.group(1)))
            elif m:
                out.append((re.sub(r"\[.*\]", "", m.group(2)).lower(), m.group(3)))
    return out


def _ecosystem(pkg):
    return pkg[2] if len(pkg) > 2 else "PyPI"


def check(packages):
    """{pin: [ids]} of the versions OSV knows vulnerabilities for (pins as pins() gives them)."""
    query = {"queries": [{"package": {"name": p[0], "ecosystem": _ecosystem(p)}, "version": p[1]} for p in packages]}
    request = urllib.request.Request("https://api.osv.dev/v1/querybatch", data=json.dumps(query).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as answer:
        results = json.load(answer)["results"]
    return {pkg: [v["id"] for v in res.get("vulns", [])] for pkg, res in zip(packages, results) if res.get("vulns")}


def accepted(path):
    """{(package, id)} of the lines "<package> <id>" in path (# starts a comment)."""
    out = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            words = line.split("#", 1)[0].split()
            if len(words) >= 2:
                out.add((words[0].lower(), words[1]))
    return out


def main():
    args, known = sys.argv[1:], set()
    while "--accepted" in args:
        i = args.index("--accepted")
        known |= accepted(args[i + 1])
        del args[i:i + 2]
    paths = args or [os.path.join(HERE, "requirements.lock")]
    packages = [pin for path in paths for pin in pins(path)]
    found = check(packages)
    open_ = {}
    for pkg, ids in sorted(found.items()):
        new = [i for i in ids if (pkg[0].lower(), i) not in known]
        note = f" (accepted: {len(ids) - len(new)})" if len(new) < len(ids) else ""
        print(f"{pkg[0]} {pkg[1]}: {', '.join(ids)}{note}  (https://osv.dev/list?ecosystem={_ecosystem(pkg)}&q={pkg[0]})")
        if new:
            open_[pkg] = new
    print(f"{len(packages)} packages checked, {len(open_)} with known vulnerabilities"
          + (f", {len(found) - len(open_)} only with accepted ones" if len(found) > len(open_) else ""))
    sys.exit(1 if open_ else 0)


if __name__ == "__main__":
    main()
