"""Audit Daten und Berechnungen 2026-10-10, closing PR: the comparison in the browser on /verify/ has the mode of
seal_verify.py --completed (E-4) for data files that hold completed participations only (Zenodo, Coscine)."""
import json
import subprocess

from tests.exports.test_seal import tsa  # noqa: F401
from tests.exports.test_seal_browser_check import NODE, STATIC, needs_node, sealed, to_csv  # noqa: F401

HARNESS = """
const api = require(process.argv[2]);
const inp = JSON.parse(require("fs").readFileSync(process.argv[3], "utf8"));
(async () => {
  const c = await api.compare(inp.chain, api.readRows(inp.csv), inp.cited, inp.completed);
  process.stdout.write(JSON.stringify({dataOk: c.dataOk, missing: c.missing, leftOut: c.leftOut, completed: c.completed}));
})();
"""


def run(tmp_path, chain, rows, cited, completed):
    script = tmp_path / "harness.js"
    script.write_text(HARNESS, encoding="utf-8")
    data = tmp_path / "input.json"
    data.write_text(json.dumps({"chain": chain, "csv": to_csv(rows), "cited": cited, "completed": completed}),
                    encoding="utf-8")
    done = subprocess.run([NODE, str(script), str(STATIC / "seal_check.js"), str(data)], capture_output=True,
                          text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_the_page_offers_the_completed_mode(sealed):  # noqa: F811
    page = sealed["page"]
    assert "data-seal-completed" in page and "nur abgeschlossene Teilnahmen" in page
    assert "n_completed" in page


@needs_node
def test_cases_that_ended_otherwise_are_left_out_only_in_the_completed_mode(tmp_path, sealed):  # noqa: F811
    chain = json.loads(json.dumps(sealed["chain"]))
    rows = [dict(r) for r in sealed["rows"]]
    gone = rows.pop(1)
    for e in chain["entries"]:  # the case left out ended as a screen-out (as in a package of completes only)
        if e.get("kind") == "sealed" and str(e.get("seq")) == str(gone["seal_seq"]):
            e["status"] = "screenout"
    plain = run(tmp_path, chain, rows, sealed["cited"], False)
    assert not plain["dataOk"] and list(plain["missing"]) == [str(gone["seal_seq"])]
    only = run(tmp_path, chain, rows, sealed["cited"], True)
    assert only["dataOk"] and only["missing"] == {} and only["leftOut"] == {"screenout": 1} and only["completed"]


@needs_node
def test_a_missing_completed_case_still_counts_in_the_completed_mode(tmp_path, sealed):  # noqa: F811
    rows = [dict(r) for r in sealed["rows"]]
    gone = rows.pop(0)
    got = run(tmp_path, sealed["chain"], rows, sealed["cited"], True)
    assert not got["dataOk"] and list(got["missing"]) == [str(gone["seal_seq"])] and got["leftOut"] == {}
