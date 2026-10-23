/* A data file compared with a seal chain in the browser (/verify/<code>, siegelkette-konzept.md E9): the file is read
   here and never sent anywhere. The checks are those of app/seal_verify.py (check_chain without the timestamps,
   state, check_rows, read_rows), in the same order and with the same results; tests/test_seal_browser_check.py runs
   both on the same files. The timestamps of the DFN are checked by the server on the page itself (the cited state);
   a seal.json picked here must hold that state, so the stamp checked there covers it. Its entries are compared one
   by one with the chain the server holds for the code (audit 2026-10-10, S2): anyone can append entries to a file,
   such as deletion records of cases removed from the data, so an entry the server does not have makes the check fail;
   if the server cannot be reached, only the entries up to the cited state count. Against the cited state, two
   findings of the checker are notes here, not problems: a case changed or deleted after the cited entry, whose row
   still holds the cited state (a data set published at that state, e.g. on Zenodo). What is checked is which cases
   there are and which were deleted, not the answer values (decision 1a): the hash of a case has a salt that is not
   exported. */
(function (root) {
  "use strict";

  var ENDED = ["completed", "screenout", "quotafull", "excluded"];

  function hex(buffer) {
    return Array.prototype.map.call(new Uint8Array(buffer), function (b) { return ("0" + b.toString(16)).slice(-2); }).join("");
  }

  function sha256(text) {
    return root.crypto.subtle.digest("SHA-256", new TextEncoder().encode(text)).then(hex);
  }

  function genesis(uid) { return sha256("queryous-seal|" + uid); }

  // Python: str(x or "") for the optional fields, str(seq), and a field of another type makes the hash fail
  function opt(v) {
    if (v === undefined || v === null || v === "" || v === 0 || v === false) return "";
    if (typeof v !== "string") throw new TypeError("field");
    return v;
  }

  function linkText(prev, e) {
    if (typeof e.seq !== "number" || typeof e.kind !== "string" || typeof e.at !== "string") throw new TypeError("field");
    var ref = e.ref === undefined || e.ref === null || e.ref === 0 || e.ref === "" ? "" : String(e.ref);
    return [prev, String(e.seq), e.kind, ref, opt(e.status), opt(e.content), opt(e.reason), e.at].join("|");
  }

  function link(prev, e) { return sha256(linkText(prev, e)); }

  var SKEW = 10 * 60 * 1000;  // seal_verify.SKEW

  // seal_verify._written_from: the earliest moment an entry can have been written (the day's start for a day), or null
  function writtenFrom(at) {
    if (typeof at !== "string") return null;
    var m = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}):(\d{2}))?/.exec(at);
    if (!m || (at.length !== 10 && !m[4])) return null;
    var t = Date.UTC(+m[1], +m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0), +(m[6] || 0));
    return isNaN(t) ? null : t;
  }

  // ({case: [status, content]}, {case: true} deleted), as seal_verify.state
  function state(entries) {
    var cases = {}, gone = {};
    (entries || []).forEach(function (e) {
      var kind = e.kind;
      if (kind === "sealed") cases[e.seq] = [e.status, e.content];
      else if (kind === "amended" && Object.prototype.hasOwnProperty.call(cases, e.ref)) cases[e.ref] = [e.status, e.content];
      else if (kind === "deleted") gone[e.ref] = true;
      else if (kind === "purged" || kind === "closed") Object.keys(cases).forEach(function (n) { gone[n] = true; });
    });
    return { cases: cases, gone: gone };
  }

  // the links of a chain: [{what: "order"|"follow"|"hash"|"anchor", n}] and how many entries and cases it holds
  function checkChain(chain) {
    var entries = chain.entries || [], problems = [], bySeq = {};
    return genesis(chain.survey || "").then(function (start) {
      var prev = start, i = 0;
      function next() {
        if (i >= entries.length) return Promise.resolve();
        var e = entries[i] || {}, n = ++i;
        if (e.seq !== n) problems.push({ what: "order", n: n, seq: e.seq });
        if (e.prev !== prev) problems.push({ what: "follow", n: n });
        var made;
        try { made = link(prev, e); } catch (err) { made = Promise.resolve(null); }
        return made.then(function (digest) {
          if (digest === null || digest !== e.digest) problems.push({ what: "hash", n: n });
          var written = writtenFrom(e.at);
          if (written !== null && written > Date.now() + SKEW) problems.push({ what: "future", n: n });
          prev = e.digest || "";
          bySeq[e.seq] = e;
          return next();
        });
      }
      return next();
    }).then(function () {
      (chain.anchors || []).forEach(function (a) {
        var e = bySeq[a.seq];
        if (!e || e.digest !== a.digest) problems.push({ what: "anchor", n: a.seq });
      });
      var st = state(entries);
      return { problems: problems, entries: entries.length, cases: Object.keys(st.cases).length,
               gone: Object.keys(st.gone).length, bySeq: bySeq };
    });
  }

  function text(row, key) { var v = row[key]; return v === undefined || v === null ? "" : String(v).trim(); }

  // Python int(float(raw)): a decimal number, cut towards zero; null if it is none
  function caseNumber(raw) {
    if (!/^[+-]?(\d+(_\d+)*\.?(\d+(_\d+)*)?|\.\d+(_\d+)*)([eE][+-]?\d+)?$/.test(raw)) return null;
    var value = parseFloat(raw.replace(/_/g, ""));
    return isFinite(value) ? Math.trunc(value) : null;
  }

  // seal_verify.check_rows, plus "given": {case: seal_hash of its row}
  function checkRows(chain, rows) {
    var st = state(chain.entries || []), cases = st.cases, gone = st.gone, seen = {};
    var out = { missing: {}, deleted: [], unknown: [], changed: [], duplicate: [], unsealed: [], nohash: [], rows: 0,
                columns: [], without: {}, given: {} };
    if (rows.length) out.columns = ["seal_seq", "status"].filter(function (c) { return !(c in rows[0]); });
    rows.forEach(function (row, idx) {
      var number = idx + 1, raw = text(row, "seal_seq");
      if (!raw) {
        var source = text(row, "source") || "real", status = text(row, "status"), key = source + "/" + (status || "?");
        out.without[key] = (out.without[key] || 0) + 1;
        if (source === "real" && (ENDED.indexOf(status) >= 0 || !status)) out.unsealed.push(number);
        return;
      }
      out.rows += 1;
      var n = caseNumber(raw);
      if (n === null) { out.unknown.push(raw); return; }
      if (seen[n]) { out.duplicate.push(n); return; }
      seen[n] = true;
      var given = text(row, "seal_hash");
      out.given[n] = given;
      if (gone[n]) out.deleted.push(n);
      else if (!Object.prototype.hasOwnProperty.call(cases, n)) out.unknown.push(n);
      else if (!given) out.nohash.push(n);
      else if (given !== cases[n][1]) out.changed.push(n);
    });
    Object.keys(cases).map(Number).sort(function (a, b) { return a - b; }).forEach(function (n) {
      if (!seen[n] && !gone[n]) out.missing[n] = cases[n][0];
    });
    return out;
  }

  // seal_verify.read_rows: comma, semicolon or tab (the one most often in the first line), UTF-8 with or without BOM,
  // quoted fields as the csv module reads them (doubled quotes, separators and line breaks inside quotes)
  function readRows(source) {
    var s = source.charCodeAt(0) === 0xFEFF ? source.slice(1) : source;
    var first = s.split("\n", 1)[0], sep = ";", most = -1;
    [";", ",", "\t"].forEach(function (c) { var k = first.split(c).length - 1; if (k > most) { most = k; sep = c; } });
    var records = [], field = "", record = [], quoted = false, i = 0, started = false;
    function endField() { record.push(field); field = ""; }
    function endRecord() { endField(); records.push(record); record = []; started = false; }
    while (i < s.length) {
      var ch = s[i];
      if (quoted) {
        if (ch === '"') {
          if (s[i + 1] === '"') { field += '"'; i += 2; continue; }
          quoted = false; i += 1; continue;
        }
        field += ch; i += 1; continue;
      }
      if (ch === '"' && field === "") { quoted = true; started = true; i += 1; continue; }
      if (ch === sep) { endField(); started = true; i += 1; continue; }
      if (ch === "\r" || ch === "\n") {
        if (started || field !== "" || record.length) endRecord(); else records.push([]);
        i += ch === "\r" && s[i + 1] === "\n" ? 2 : 1;
        continue;
      }
      field += ch; started = true; i += 1;
    }
    if (started || field !== "" || record.length) endRecord();
    records = records.filter(function (r) { return r.length; });  // DictReader skips empty lines
    if (!records.length) return [];
    var head = records[0];
    return records.slice(1).map(function (r) {
      var row = {};
      head.forEach(function (name, k) { row[name] = k < r.length ? r[k] : null; });
      return row;
    });
  }

  function sortNum(list) { return list.slice().sort(function (a, b) { return (+a) - (+b); }); }

  // the entries of a chain (a file) the server's chain does not hold like this (seal_verify.against_server): their
  // numbers, or ["*"] if the server's chain is of another survey
  function againstServer(chain, server) {
    if (!server || server.format !== "queryous-seal/1" || server.survey !== chain.survey) return ["*"];
    var bySeq = {};
    (server.entries || []).forEach(function (e) { bySeq[e.seq] = e.digest; });
    return (chain.entries || []).filter(function (e) {
      return !Object.prototype.hasOwnProperty.call(bySeq, e.seq) || bySeq[e.seq] !== e.digest;
    }).map(function (e) { return e.seq; });
  }

  // everything the page shows: the chain against the cited state (seq, digest) and the file against the chain;
  // completed: the file holds completed participations only (Zenodo, Coscine), as seal_verify --completed.
  // server: the chain the server holds for the code, when `chain` is a seal.json picked by the person: every entry of
  // the file must be in it, unchanged; null = the server could not be reached, then only the entries up to the cited
  // state count; undefined = `chain` is the server's own (nothing to compare).
  function compare(chain, rows, cited, completed, server) {
    return checkChain(chain).then(function (ch) {
      var entry = ch.bySeq[cited.seq];
      var belongs = !!entry && entry.digest === cited.digest;
      var foreign = server ? againstServer(chain, server) : [], offline = server === null, beyond = 0, use = chain;
      if (offline) {  // the cited state is stamped and checked on the page; what the file adds after it is not relied on
        use = Object.assign({}, chain, { entries: (chain.entries || []).filter(function (e) {
          return typeof e.seq === "number" && e.seq <= cited.seq;
        }) });
        rows = rows.filter(function (row) {
          var n = caseNumber(text(row, "seal_seq"));
          if (n !== null && n > cited.seq) { beyond += 1; return false; }
          return true;
        });
      }
      var r = checkRows(use, rows);
      var at = state((chain.entries || []).filter(function (e) { return typeof e.seq === "number" && e.seq <= cited.seq; }));
      // a case changed after the cited entry whose row holds the cited state; a case deleted after it
      var older = r.changed.filter(function (n) { return at.cases[n] && r.given[n] === at.cases[n][1]; });
      var laterGone = r.deleted.filter(function (n) { return !at.gone[n]; });
      var missing = {}, later = [];
      Object.keys(r.missing).forEach(function (n) {
        if (+n > cited.seq) later.push(+n); else missing[n] = r.missing[n];
      });
      var leftOut = {};  // the cases that ended otherwise are not in such a file by design: counted, not missed
      if (completed) Object.keys(missing).forEach(function (n) {
        var s = missing[n] || "?";
        if (s !== "completed") { leftOut[s] = (leftOut[s] || 0) + 1; delete missing[n]; }
      });
      var problems = {
        deleted: r.deleted.filter(function (n) { return laterGone.indexOf(n) < 0; }),
        unknown: r.unknown, changed: r.changed.filter(function (n) { return older.indexOf(n) < 0; }),
        duplicate: r.duplicate, unsealed: r.unsealed, nohash: r.nohash
      };
      var dataOk = !r.columns.length && !Object.keys(missing).length && Object.keys(problems).every(function (k) {
        return !problems[k].length;
      });
      return { chainOk: !ch.problems.length && belongs && !foreign.length, belongs: belongs, chain: ch, rows: r,
               missing: missing, later: sortNum(later), older: sortNum(older), laterGone: sortNum(laterGone),
               problems: problems, dataOk: dataOk, completed: !!completed, leftOut: leftOut, foreign: foreign,
               offline: offline, compared: !!server, beyond: beyond };
    });
  }

  var api = { genesis: genesis, link: link, linkText: linkText, state: state, checkChain: checkChain,
              checkRows: checkRows, readRows: readRows, compare: compare, againstServer: againstServer };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof document === "undefined") return;

  // --- the page --------------------------------------------------------------------------------------------------

  var box = document.querySelector("[data-seal-compare]");
  if (!box) return;
  var T = JSON.parse(document.getElementById("seal-compare-texts").textContent);
  var rowsInput = box.querySelector("[data-seal-rows]"), chainInput = box.querySelector("[data-seal-chain]");
  var completedInput = box.querySelector("[data-seal-completed]");
  var out = box.querySelector("[data-seal-out]");
  var cited = { seq: +box.dataset.seq, digest: box.dataset.digest };

  function say(key, n, extra) {
    var t = T[key];
    if (Array.isArray(t)) t = n === 1 ? t[0] : t[1];
    t = t.replace("{n}", n === undefined ? "" : String(n));
    Object.keys(extra || {}).forEach(function (k) { t = t.replace("{" + k + "}", extra[k]); });
    return t;
  }

  function list(nums) {
    var shown = nums.slice(0, 30).join(", ");
    return nums.length > 30 ? shown + " …" : shown;
  }

  function el(tag, cls, content) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (content !== undefined) node.textContent = content;
    return node;
  }

  function readFile(file) {
    return new Promise(function (resolve, reject) {
      var reader = new FileReader();
      reader.onload = function () { resolve(reader.result); };
      reader.onerror = function () { reject(reader.error); };
      reader.readAsText(file, "utf-8");
    });
  }

  // the chain the server holds for this code, or null if it cannot be loaded (busy, offline)
  function serverChain() {
    return fetch(box.dataset.chain, { credentials: "same-origin" }).then(function (resp) {
      return resp.ok ? resp.json() : null;
    }).catch(function () { return null; });
  }

  function loadChain() {
    if (chainInput && chainInput.files && chainInput.files[0]) {
      return readFile(chainInput.files[0]).then(function (raw) {
        var data;
        try { data = JSON.parse(raw); } catch (err) { throw new Error(T.not_chain); }
        if (!data || data.format !== "queryous-seal/1") throw new Error(T.not_chain);
        return { data: data, own: true };
      });
    }
    return fetch(box.dataset.chain, { credentials: "same-origin" }).then(function (resp) {
      if (resp.status === 429) throw new Error(T.busy);
      if (!resp.ok) throw new Error(T.no_chain);
      return resp.json().then(function (data) { return { data: data, own: false }; });
    });
  }

  function render(res, own, fileName) {
    out.textContent = "";
    var ok = res.chainOk && res.dataOk;
    var badge = el("div", "seal-badge " + (ok ? "is-sealed" : "is-broken"));
    var icon = el("span", "seal-icon", ok ? "🔒" : "⚠");
    icon.setAttribute("aria-hidden", "true");
    badge.appendChild(icon);
    var body = el("div");
    body.appendChild(el("strong", "", ok ? T.fits : T.fits_not));
    var lines = el("span", "seal-lines");
    var chainLine = res.chainOk ? say("chain_ok", cited.seq)
      : (!res.belongs ? T.chain_other : (res.foreign.length && !res.chain.problems.length ? T.chain_foreign : T.chain_bad));
    lines.appendChild(el("span", res.chainOk ? "is-ok" : "is-bad", T.chain + " " + chainLine));
    var dataLine;
    if (res.dataOk) dataLine = say("data_ok", res.rows.rows);
    else {
      var parts = [], miss = Object.keys(res.missing).length;
      if (res.rows.columns.length) parts.push(say("columns_short", undefined, { cols: res.rows.columns.join(", ") }));
      if (miss) parts.push(say("missing_short", miss));
      var other = Object.keys(res.problems).reduce(function (s, k) { return s + res.problems[k].length; }, 0);
      if (other) parts.push(say("other_short", other));
      dataLine = parts.join(", ");
    }
    lines.appendChild(el("span", res.dataOk ? "is-ok" : "is-bad", T.data + " " + dataLine));
    body.appendChild(lines);
    body.appendChild(el("span", "seal-when", say("file", undefined, { name: fileName })));
    badge.appendChild(body);
    out.appendChild(badge);

    var facts = el("ul", "seal-facts" + (ok ? "" : " is-broken"));
    function fact(t) { facts.appendChild(el("li", "", t)); }
    res.chain.problems.slice(0, 10).forEach(function (p) { fact(say("p_" + p.what, p.n, { seq: String(p.seq) })); });
    if (!res.belongs) fact(say("p_other", cited.seq));
    if (res.foreign.length) fact(say("p_foreign", res.foreign[0] === "*" ? res.chain.entries : res.foreign.length,
                                     { list: res.foreign[0] === "*" ? "*" : list(res.foreign) }));
    if (res.rows.columns.length) fact(say("p_columns", undefined, { cols: res.rows.columns.join(", ") }));
    var byStatus = {};
    Object.keys(res.missing).forEach(function (n) { var s = res.missing[n] || "?"; (byStatus[s] = byStatus[s] || []).push(+n); });
    Object.keys(byStatus).sort().forEach(function (s) {
      fact(say("p_missing", byStatus[s].length, { status: T.status[s] || s, list: list(byStatus[s]) }));
    });
    ["deleted", "unknown", "changed", "duplicate", "unsealed", "nohash"].forEach(function (k) {
      if (res.problems[k].length) fact(say("p_" + k, res.problems[k].length, { list: list(res.problems[k]) }));
    });
    var notes = el("ul", "seal-facts");
    function note(t) { notes.appendChild(el("li", "", t)); }
    note(say("n_rows", res.rows.rows));
    if (res.completed) {
      var out_ = Object.keys(res.leftOut).sort().map(function (k) { return (T.status[k] || k) + " " + res.leftOut[k]; });
      note(say("n_completed", undefined, { list: out_.length ? out_.join(", ") : T.none }));
    }
    var without = Object.keys(res.rows.without);
    if (without.length) {
      var total = without.reduce(function (s, k) { return s + res.rows.without[k]; }, 0);
      note(say("n_without", total, { list: without.sort().map(function (k) { return k + " " + res.rows.without[k]; }).join(", ") }));
    }
    if (res.later.length) note(say("n_later", res.later.length, { list: list(res.later), seq: String(cited.seq) }));
    if (res.older.length) note(say("n_older", res.older.length, { list: list(res.older), seq: String(cited.seq) }));
    if (res.laterGone.length) note(say("n_gone", res.laterGone.length, { list: list(res.laterGone), seq: String(cited.seq) }));
    note(own ? say("n_own", res.chain.entries) : say("n_server", res.chain.entries));
    if (res.compared && !res.foreign.length) note(T.n_server_same);
    if (res.offline) note(say("n_offline", undefined, { seq: String(cited.seq) }));
    if (res.beyond) note(say("n_beyond", res.beyond, { seq: String(cited.seq) }));
    note(T.n_values);
    note(box.dataset.stamp ? say("n_stamp", cited.seq, { when: box.dataset.stamp }) : T.n_nostamp);
    if (facts.children.length) out.appendChild(facts);
    out.appendChild(notes);
    out.hidden = false;
  }

  function run() {
    var file = rowsInput.files && rowsInput.files[0];
    if (!file) return;
    out.hidden = false;
    out.textContent = T.working;
    if (/\.(xlsx|xls|sav|dta|omv|jasp|rds|zip|docx|parquet)$/i.test(file.name)) {
      out.textContent = T.not_csv;
      return;
    }
    Promise.all([readFile(file), loadChain()]).then(function (got) {
      var only = !!(completedInput && completedInput.checked);
      // a seal.json of one's own is compared with the server's chain, entry by entry (S2)
      return (got[1].own ? serverChain() : Promise.resolve(undefined)).then(function (server) {
        return compare(got[1].data, readRows(got[0]), cited, only, server);
      }).then(function (res) { render(res, got[1].own, file.name); });
    }).catch(function (err) {
      out.textContent = err && err.message && err.message.indexOf(" ") > 0 ? err.message : T.failed;
    });
  }

  rowsInput.addEventListener("change", run);
  if (chainInput) chainInput.addEventListener("change", run);
  if (completedInput) completedInput.addEventListener("change", run);
})(typeof window !== "undefined" ? window : globalThis);
