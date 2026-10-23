"""A data file compared with the seal chain in the browser (siegelkette-konzept.md E9, Ralf 2026-10-10 "wie
empfohlen"): /verify/<code> lets a reviewer pick data.csv (and the seal.json of the same package); app/static/
seal_check.js checks it there, the file is never uploaded. Its checks are those of app/seal_verify.py: the tests run
both on the same files and compare the results."""
import csv
import io
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app import seal_verify
from tests.exports.test_seal import export_rows, tsa  # noqa: F401
from tests.exports.test_seal_data_check import cases, cite

STATIC = Path(__file__).resolve().parents[2] / "app" / "static"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="needs Node to run app/static/seal_check.js")

HARNESS = """
const api = require(process.argv[2]);
const inp = JSON.parse(require("fs").readFileSync(process.argv[3], "utf8"));
(async () => {
  const rows = api.readRows(inp.csv);
  const ch = await api.checkChain(inp.chain);
  const r = api.checkRows(inp.chain, rows);
  delete r.given;
  const c = await api.compare(inp.chain, rows, inp.cited);
  process.stdout.write(JSON.stringify({rows, problems: ch.problems.map(p => p.what + " " + p.n), check: r,
    cmp: {chainOk: c.chainOk, dataOk: c.dataOk, belongs: c.belongs, later: c.later, older: c.older,
          laterGone: c.laterGone, missing: c.missing, problems: c.problems}}));
})();
"""


def run_js(tmp_path, chain, text, cited):
    script = tmp_path / "harness.js"
    script.write_text(HARNESS, encoding="utf-8")
    data = tmp_path / "input.json"
    data.write_text(json.dumps({"chain": chain, "csv": text, "cited": cited}), encoding="utf-8")
    done = subprocess.run([NODE, str(script), str(STATIC / "seal_check.js"), str(data)], capture_output=True,
                          text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def python_rows(tmp_path, text):
    path = tmp_path / "data.csv"
    path.write_bytes(text.encode("utf-8"))
    return seal_verify.read_rows(str(path))


def as_json(check):
    return json.loads(json.dumps({**check, "missing": {str(k): v for k, v in check["missing"].items()}}))


def same_as_python(tmp_path, chain, text, cited):
    """The browser's result, after checking that its rows, chain problems and row checks are the checker's."""
    got = run_js(tmp_path, chain, text, cited)
    rows = python_rows(tmp_path, text)
    assert got["rows"] == rows
    problems = seal_verify.check_chain({**chain, "anchors": [a for a in chain.get("anchors") or []]})["problems"]
    timestamps = [p for p in problems if p.startswith("timestamp of entry") and "not a hash of this chain" not in p]
    assert len(got["problems"]) == len(problems) - len(timestamps)
    assert got["check"] == as_json(seal_verify.check_rows(chain, rows))
    return got


def to_csv(rows, sep=","):
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=list(rows[0]), delimiter=sep)
    w.writeheader()
    w.writerows(rows)
    return out.getvalue()


@pytest.fixture
def sealed(app, client, survey, tsa):  # noqa: F811
    """A survey with four sealed cases, its code, the chain behind it and the data file."""
    cases(app, client, ("a", "b", "c", "d"))
    code = cite(app)
    page = app.test_client().get(f"/verify/{code}")
    chain = json.loads(app.test_client().get(f"/verify/{code}/seal.json").data)
    seq, digest = re.search(r'data-seq="(\d+)" data-digest="([0-9a-f]{64})"', page.data.decode()).groups()
    return {"code": code, "page": page.data.decode(), "chain": chain, "rows": export_rows(client),
            "cited": {"seq": int(seq), "digest": digest}}


def test_the_page_offers_the_comparison_in_the_browser(sealed):
    page = sealed["page"]
    assert "Datensatz abgleichen" in page and "verlässt Ihren Rechner nicht" in page
    assert 'src="/static/seal_check.js' in page and "data-seal-rows" in page and "data-seal-chain" in page
    assert f'data-chain="/verify/{sealed["code"]}/seal.json"' in page
    texts = json.loads(re.search(r'<script type="application/json" id="seal-compare-texts">(.*?)</script>', page,
                                 re.S).group(1))
    assert texts["fits"] == "Datensatz passt zur Kette" and len(texts["data_ok"]) == 2
    assert sealed["chain"]["entries"][sealed["cited"]["seq"] - 1]["digest"] == sealed["cited"]["digest"]
    assert 'enctype="multipart' not in page                  # nothing is sent: no form takes the file


@needs_node
def test_an_intact_data_file_matches_like_the_checker(tmp_path, sealed):
    got = same_as_python(tmp_path, sealed["chain"], to_csv(sealed["rows"]), sealed["cited"])
    assert got["cmp"]["chainOk"] and got["cmp"]["dataOk"] and got["check"]["rows"] == 4
    lines, ok = seal_verify.report(sealed["chain"], sealed["rows"])
    assert ok, lines


@needs_node
def test_a_case_left_out_or_changed_is_found(tmp_path, sealed):
    rows = [dict(r) for r in sealed["rows"]]
    left = rows.pop(1)
    rows[0]["seal_hash"] = "0" * 64
    got = same_as_python(tmp_path, sealed["chain"], to_csv(rows, ";"), sealed["cited"])
    assert not got["cmp"]["dataOk"] and got["cmp"]["chainOk"]
    assert list(got["cmp"]["missing"]) == [left["seal_seq"]]
    assert got["cmp"]["problems"]["changed"] == [int(rows[0]["seal_seq"])]


@needs_node
def test_a_changed_chain_or_another_ones_does_not_hold(tmp_path, sealed):
    chain = json.loads(json.dumps(sealed["chain"]))
    chain["entries"][2]["status"] = "screenout"
    got = same_as_python(tmp_path, chain, to_csv(sealed["rows"]), sealed["cited"])
    assert not got["cmp"]["chainOk"] and "hash 3" in got["problems"]
    other = {**sealed["cited"], "digest": "f" * 64}
    got = same_as_python(tmp_path, sealed["chain"], to_csv(sealed["rows"]), other)
    assert not got["cmp"]["belongs"] and not got["cmp"]["chainOk"]


@needs_node
def test_cases_after_the_cited_entry_are_notes_not_problems(tmp_path, sealed):
    """A data set published at the cited state lacks the cases sealed later: the checker, given today's chain, names
    them missing; the page against the cited code names them as added later."""
    cited = {"seq": 3, "digest": sealed["chain"]["entries"][2]["digest"]}
    later = [r for r in sealed["rows"] if int(r["seal_seq"]) <= 3]
    got = same_as_python(tmp_path, sealed["chain"], to_csv(later), cited)
    assert got["check"]["missing"] and got["cmp"]["dataOk"] and got["cmp"]["later"]


@needs_node
def test_the_reader_takes_what_the_csv_module_takes(tmp_path):
    chain = {"format": seal_verify.FORMAT, "survey": "u", "entries": [], "anchors": []}
    text = ('﻿id;seal_seq;status;note\r\n1;;completed;"a;b"\r\n\r\n2;7;screenout;"zwei\r\nZeilen ""zitiert"""\r\n'
            '3;x;;\r\n4\r\n')
    got = same_as_python(tmp_path, chain, text, {"seq": 0, "digest": ""})
    assert got["rows"][1]["note"] == 'zwei\r\nZeilen "zitiert"' and got["rows"][3]["status"] is None
    assert got["check"]["unknown"] == [7, "x"] and got["check"]["unsealed"] == [1, 4]
