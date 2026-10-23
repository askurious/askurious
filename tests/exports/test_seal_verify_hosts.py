"""Audit 2026-10-11, package V1 "Siegelprüfskript" (H1, TN7).

H1: seal_verify.py asked the "server" named in the very seal.json it checks (field "url"). Whoever removed cases and
appended deletion records of their own pointed "url" at a server serving the forged chain, and the recommended call
`python seal_verify.py seal.json data.csv` said "RESULT: holds.". Now the address in the file counts only on one of
Askurious's own hosts (OWN_HOSTS, https, the path of a chain), any other server only with --server; without a server
to trust, an unstamped end gives "holds only up to entry n" (exit code 3), never "holds".
TN7: /verify/<code>/seal.json builds "url" from the configured address, not from the Host header."""
import json
import re
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from app import seal_verify
from tests.exports.test_audit11_siegelpruefung import dropped, forge
from tests.exports.test_seal import tsa  # noqa: F401
from tests.exports.test_seal_browser_check import sealed, to_csv  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("url", [
    "https://askurious.de/verify/1-2-abc/seal.json",
    "https://ask.askurious.de/verify/1-2-abc/seal.json",
    "https://polltergeist.de/verify/12-30-0f3a/seal.json",
    "https://askurious.de:443/verify/1-2-abc/seal.json",
])
def test_own_addresses_are_trusted(url):
    assert seal_verify.own_url(url)


@pytest.mark.parametrize("url", [
    None, "", 42, "askurious.de/verify/1-2-abc/seal.json",
    "http://askurious.de/verify/1-2-abc/seal.json",              # not https
    "https://angreifer.example/verify/1-2-abc/seal.json",       # a host of its own
    "https://askurious.de.angreifer.example/verify/1/seal.json",
    "https://evilaskurious.de/verify/1-2-abc/seal.json",
    "https://askurious.de@angreifer.example/verify/1/seal.json",  # user name: the host is another
    "https://x@askurious.de/verify/1-2-abc/seal.json",
    "https://askurious.de:8443/verify/1-2-abc/seal.json",       # another port
    "https://askurious.de/login?next=https://angreifer.example/seal.json",  # not the path of a chain
    "https://askurious.de/verify/1-2-abc/seal.json?x=1",
    "https://askurious.de/verify/../r/seal.json",
    "https://127.0.0.1/verify/1-2-abc/seal.json",
])
def test_other_addresses_are_not(url):
    assert not seal_verify.own_url(url)


def _serve(payload, hits):
    body = json.dumps(payload).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            hits.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_h1_a_forged_file_naming_its_own_server_does_not_hold(tmp_path, sealed, capsys):  # noqa: F811
    """The attack of the audit (fixes/siegel/test_f_s2_url_aus_datei.py): two cases removed, deletion records appended
    20 minutes ago, "url" on a server of the attacker serving the forged chain."""
    drop, kept = dropped(sealed)
    forged = forge(sealed["chain"], drop, datetime.now(timezone.utc) - timedelta(minutes=20))
    hits = []
    srv = _serve(forged, hits)
    try:
        forged["url"] = f"http://127.0.0.1:{srv.server_port}/verify/{sealed['code']}/seal.json"
        (tmp_path / "seal.json").write_text(json.dumps(forged), encoding="utf-8")
        (tmp_path / "data.csv").write_text(to_csv(kept), encoding="utf-8")
        code = seal_verify.main(["seal_verify.py", str(tmp_path / "seal.json"), str(tmp_path / "data.csv")])
    finally:
        srv.shutdown()
    out = capsys.readouterr().out
    assert hits == []                                         # the server named in the file is not asked
    assert code == 1 and "RESULT: holds." not in out, out     # the removed cases are missing
    assert "MISSING without deletion record" in out and "not one of Askurious's own" in out and "--server" in out
    # the same with an https address on a host of its own
    forged["url"] = f"https://angreifer.example/verify/{sealed['code']}/seal.json"
    (tmp_path / "seal.json").write_text(json.dumps(forged), encoding="utf-8")
    assert seal_verify.main(["x", str(tmp_path / "seal.json"), str(tmp_path / "data.csv")]) == 1


def _grown(chain):
    """The true chain with one more entry the server has and no stamp covers yet (a case sealed since the stamp)."""
    chain = json.loads(json.dumps(chain))
    last = chain["entries"][-1]
    e = {"seq": last["seq"] + 1, "kind": "sealed", "ref": None, "status": "completed", "content": "ab" * 32,
         "reason": None, "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "prev": last["digest"]}
    e["digest"] = seal_verify.link(last["digest"], e)
    chain["entries"].append(e)
    return chain


def test_h1_an_unstamped_end_without_a_trusted_server_is_partial(tmp_path, sealed, monkeypatch, capsys):  # noqa: F811
    grown = _grown(sealed["chain"])
    asked = []
    monkeypatch.setattr(seal_verify, "fetch", lambda url, timeout=20: asked.append(url) or grown)
    path = tmp_path / "seal.json"
    # an address that is not Askurious's own: not asked, holds only up to the stamped state
    path.write_text(json.dumps({**grown, "url": "https://andere-installation.example/verify/1-2-a/seal.json"}),
                    encoding="utf-8")
    assert seal_verify.main(["x", str(path)]) == 3
    out = capsys.readouterr().out
    assert asked == [] and seal_verify.PARTIAL in out and "RESULT: holds." not in out
    # named with --server, that installation is asked, and the whole chain holds
    assert seal_verify.main(["x", "--server", "https://andere-installation.example/verify/1-2-a/seal.json",
                             str(path)]) == 0
    assert asked == ["https://andere-installation.example/verify/1-2-a/seal.json"]
    assert "RESULT: holds." in capsys.readouterr().out
    # an address on Askurious's own host is asked without --server
    own = f"https://askurious.de/verify/{sealed['code']}/seal.json"
    path.write_text(json.dumps({**grown, "url": own}), encoding="utf-8")
    assert seal_verify.main(["x", str(path)]) == 0 and asked[-1] == own
    assert "Compared with the chain on the server: " + own in capsys.readouterr().out


def test_tn7_seal_json_names_the_configured_address_not_the_host_header(app, sealed, monkeypatch):  # noqa: F811
    base = app.config["PUBLIC_BASE_URL"]
    assert sealed["chain"]["url"] == f"{base}/verify/{sealed['code']}/seal.json"
    # a key of its own, so that the app answers under another name at all (with the development key only localhost)
    monkeypatch.setitem(app.config, "SECRET_KEY", "k" * 64)
    got = app.test_client().get(f"/verify/{sealed['code']}/seal.json", headers={"Host": "angreifer.example"})
    assert got.status_code == 200
    assert json.loads(got.data)["url"] == f"{base}/verify/{sealed['code']}/seal.json"
    assert "angreifer" not in got.data.decode()


def test_own_hosts_follow_the_deploy():
    """OWN_HOSTS is written into the checker (it runs on its own at third parties): it has to hold the addresses the
    deploy serves (deploy/docker/deploy-docker.sh: RESEARCHER_HOST, PARTICIPANT_HOST) and those of every brand."""
    script = (ROOT / "deploy" / "docker" / "deploy-docker.sh").read_text(encoding="utf-8")
    for var in ("RESEARCHER_HOST", "PARTICIPANT_HOST"):
        host = re.search(var + r'="\$\{' + var + r':-([^}]+)\}"', script).group(1)
        assert host in seal_verify.OWN_HOSTS, host
    assert "polltergeist.de" in seal_verify.OWN_HOSTS and "ask.polltergeist.de" in seal_verify.OWN_HOSTS
    assert all(h == h.lower() and "/" not in h and ":" not in h for h in seal_verify.OWN_HOSTS)
