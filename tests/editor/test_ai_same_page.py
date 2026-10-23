"""An AI call whose route sends back to the same page with only another #anchor (the consent text: "Mit KI formulieren"
answers with ".../items/1?lang=de#body") loads the page anew; location.assign alone only jumped to the anchor, and
the "being written" box stayed although the draft was saved (Ralf, 2026-10-11)."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app import ai
from tests.privacy.test_consent import consent_item

NODE = shutil.which("node")
SCRIPT = Path(__file__).parents[2] / "app" / "static" / "app.js"
AI_CALL = {"X-AI-Call": "1"}


def test_the_consent_draft_answers_with_its_own_page_and_an_anchor(app, client, survey, monkeypatch):
    monkeypatch.setattr(ai, "generate_text", lambda *a, **k: "**Worum geht es?** KI-Text")
    d = client.post("/surveys/1/editor/items/1", data={"question": "Einwilligung", "body": "", "agree": "Ja",
                                                       "disagree": "Nein", "draft": "ai"}, headers=AI_CALL).get_json()
    assert d["status"] == "redirect" and d["url"].endswith("/surveys/1/editor/items/1?lang=de#body")
    assert consent_item(app).texts["de"]["body"] == "**Worum geht es?** KI-Text"


_HARNESS = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
const body = src.match(/window\.aiGo = function \(url, loc\) \{[\s\S]*?\n  \};/)[0];
const replaced = [];
global.history = { replaceState: function (s, t, url) { replaced.push(url); } };
global.window = {};
eval(body);
function run(here, url) {
  const calls = [];
  const loc = { href: here, hash: new URL(here).hash,
                assign: function (u) { calls.push(["assign", u]); }, reload: function () { calls.push(["reload"]); } };
  replaced.length = 0;
  const how = window.aiGo(url, loc);
  return { how: how, calls: calls, replaced: replaced.slice() };
}
const P = "https://askurious.de/surveys/1/editor/items/1?lang=de";
console.log(JSON.stringify({
  anchor: run(P, "/surveys/1/editor/items/1?lang=de#body"),
  again: run(P + "#body", "/surveys/1/editor/items/1?lang=de#body"),
  same: run(P, P),
  other: run(P, "/surveys/1/editor/items/2?lang=de"),
  lang: run(P, "/surveys/1/editor/items/1?lang=en#body"),
}));
"""


@pytest.mark.skipif(NODE is None, reason="needs Node to run app/static/app.js")
def test_a_redirect_to_the_same_page_loads_it_anew(tmp_path):
    harness = tmp_path / "go.js"
    harness.write_text(_HARNESS, encoding="utf-8")
    done = subprocess.run([NODE, str(harness), str(SCRIPT)], capture_output=True, check=True, timeout=60)
    r = json.loads(done.stdout.decode("utf-8"))
    assert r["anchor"]["calls"] == [["reload"]]
    assert r["anchor"]["replaced"] == ["https://askurious.de/surveys/1/editor/items/1?lang=de#body"]
    assert r["again"]["calls"] == [["reload"]] and r["again"]["replaced"] == []
    assert r["same"]["calls"] == [["reload"]]
    assert r["other"]["calls"] == [["assign", "https://askurious.de/surveys/1/editor/items/2?lang=de"]]
    assert r["lang"]["how"] == "assign"


def test_the_ai_script_no_longer_only_assigns_the_address():
    script = SCRIPT.read_text(encoding="utf-8")
    assert re.search(r'd\.status === "redirect"\) \{ window\.aiGo\(d\.url\)', script)
