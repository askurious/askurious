// Filters "Nur stellen, wenn …" of a Leitfrage or a Themenblock in the interview view (app/interview_gate.py,
// concept konzept-filter-leitfragen, E1-E7 as recommended, Ralf 2026-11-03). Only the rules, without the page:
// interview.js hands over its pages and reads the answers through the functions it passes; tests/interview_gate_cases.js
// runs the same rules in node.
//   pages      [{gates: [filter], question: Leitfrage id or null, subs: [Unterfrage ids]}] in the order of the view
//   linear     the order "Der Reihe nach": only then pages are left out and states set
//   fits()     {"<Leitfrage>:<row>": "yes" | "no"}: the switches "trifft zu"
//   answer(id) [codes] chosen for a question of the Kurzfragebogen
// A filter is {"of", "probe", "fits", "n", "why", "gone", "wait"} or {"item", "code", "why"}; while what it names is
// not given yet it is open and leaves nothing out.
(function (global) {
  function make(pages, linear, fits, answer) {
    var stepOf = {};
    pages.forEach(function (p, n) { if (p.question) stepOf[String(p.question)] = n; });
    function holds(g, seen) {  // true, false, or null while still open
      if (g.of !== undefined) {
        var at = stepOf[String(g.of)];
        if (at !== undefined && failing(at, seen)) return false;  // its Leitfrage is left out itself (E7)
        var fit = (fits() || {})[g.of + ":" + g.probe];
        if (fit !== "yes" && fit !== "no") return null;
        return (fit === "yes") === !!g.fits;
      }
      var given = answer(g.item) || [];
      if (!given.length) return null;
      return given.indexOf(String(g.code)) >= 0;
    }
    function failing(n, seen) {  // the first filter of page n that does not hold (both must hold, E2), else null
      seen = seen || {};
      if (seen[n]) return null;
      seen[n] = true;
      var out = null;
      ((pages[n] || {}).gates || []).some(function (g) { if (holds(g, seen) === false) { out = g; return true; } return false; });
      delete seen[n];
      return out;
    }
    function filtered(n) { return !!failing(n); }
    function why(g) {
      var at = g.of !== undefined ? stepOf[String(g.of)] : undefined;
      return at !== undefined && filtered(at) ? g.gone : g.why;
    }
    function forward(n) {  // Next to page n: over the pages left out (E1); the closing page is never one
      var skipped = [], t = n;
      if (linear) while (t < pages.length - 1 && filtered(t)) { skipped.push(t); t += 1; }
      return { to: t, skipped: skipped };
    }
    function back(from) {  // Back: over the pages left out (no list of visited pages)
      var t = from - 1;
      if (linear) { while (t > 0 && filtered(t)) t -= 1; if (t >= 0 && filtered(t)) t = from; }
      return Math.max(0, t);
    }
    function jumpBack(n, from) {  // "Zu Themenblock …" to an earlier block: its first page not left out
      var t = n;
      while (linear && t < from && filtered(t)) t += 1;
      return t;
    }
    function waiting(from, switches) {  // E3: a switch on page `from` that a later filter names and that is open
      if (!linear) return null;
      var on = {};
      (switches || []).forEach(function (k) { on[k] = true; });
      for (var n = from + 1; n < pages.length; n++) {
        var hit = ((pages[n] || {}).gates || []).filter(function (g) {
          var key = g.of + ":" + g.probe;
          return g.of !== undefined && on[key] && !(fits() || {})[key];
        })[0];
        if (hit) return hit;
      }
      return null;
    }
    function settle(states, by) {  // E5: state skipped by the filter, taken back when it holds; never a state set by hand
      var changed = false;
      pages.forEach(function (p, n) {
        if (!p.question) return;
        var off = linear && filtered(n);
        [p.question].concat(p.subs || []).forEach(function (q) {
          q = String(q);
          if (off) { if (!states[q]) { states[q] = "skipped"; by[q] = "filter"; changed = true; } }
          else if (by[q] === "filter") { delete states[q]; delete by[q]; changed = true; }
        });
      });
      return changed;
    }
    function count(skipped) {  // how many Leitfragen (and pages) Next left out
      return { questions: skipped.filter(function (n) { return !!(pages[n] || {}).question; }).length, pages: skipped.length };
    }
    return { holds: holds, failing: failing, filtered: filtered, why: why, forward: forward, back: back, jumpBack: jumpBack,
             waiting: waiting, settle: settle, count: count };
  }
  global.AskuriousGate = { make: make };
})(typeof window !== "undefined" ? window : this);
