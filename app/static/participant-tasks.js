// Timed tasks that own the page while they run (IAT, delay discounting): the navigation hides while one runs, and
// "Next" waits for the required ones. Auto-next pages need no navigation (data-assign-answer, see above).
function taskPage(roots, answerOf) {
  var nav = document.querySelector("form[data-auto-next]") ? null : document.querySelector(".p-nav");
  return function sync(running) {
    if (!nav) return;
    nav.hidden = !!running;
    var next = nav.querySelector(".p-next");
    if (next) next.hidden = roots.some(function (r) { return r.dataset.required === "1" && !answerOf(r).value; });
  };
}
// The task's focus goes to the line that says it is done (accessibility concept D7: hiding the task would else drop
// it to the start of the page); `plain`: without the tick (the IAT's "kann ich nicht bearbeiten")
function taskDone(root, stage, task, text, plain) {
  var had = root.contains(document.activeElement) || document.activeElement === document.body;
  task.hidden = true;
  stage.hidden = false;
  stage.innerHTML = "";
  var p = document.createElement("p");
  p.className = "p-assign-done";
  p.textContent = (plain ? "" : "✓ ") + text;
  p.tabIndex = -1;
  stage.appendChild(p);
  if (had) p.focus();
  var item = root.closest(".p-item");
  if (item) item.classList.remove("has-error", "has-nudge");
  root.dispatchEvent(new CustomEvent("item:complete", { bubbles: true }));
}

// IAT (items/iat.py): seven blocks from the server's plan; E = left, I = right, or tap. A wrong answer shows a
// red ✗ and waits for the right one; the time runs from the word's appearance to the right answer.
(function () {
  var roots = Array.prototype.slice.call(document.querySelectorAll("[data-iat]"));
  if (!roots.length) return;
  var answerOf = function (r) { return r.querySelector("[data-iat-answer]"); };
  var sync = taskPage(roots, answerOf);
  roots.forEach(function (root) {
    var plan = JSON.parse(root.querySelector("[data-iat-plan]").textContent);
    var words = JSON.parse(root.querySelector("[data-iat-words]").textContent);
    var cats = JSON.parse(root.querySelector("[data-iat-cats]").textContent);
    var order = parseInt(root.dataset.order, 10) || 1;
    var stage = root.querySelector("[data-iat-stage]"), task = root.querySelector("[data-iat-task]");
    var word = root.querySelector("[data-iat-word]"), wrong = root.querySelector("[data-iat-wrong]");
    var intro = root.querySelector("[data-iat-intro]"), introText = root.querySelector("[data-iat-intro-text]");
    var leftEl = root.querySelector("[data-iat-left]"), rightEl = root.querySelector("[data-iat-right]");
    var round = root.querySelector("[data-iat-round]"), go = root.querySelector("[data-iat-go]");
    var status = root.querySelector("[data-iat-status]"), cannot = root.querySelector("[data-iat-cannot]");
    if (cannot) cannot.hidden = false;  // the way out needs this script, like the task itself
    var b = 0, i = 0, results = [], shownAt = 0, active = false, erred = 0, inIntro = false, running = false;
    var interruption = interruptibleTask(root, stage, task, function () {
      running = false; active = false; inIntro = false; results = []; answerOf(root).value = ""; sync(false);
    });
    function attribute(c) { return c === "X" || c === "Y"; }
    function heads(el, list) {
      el.innerHTML = "";
      list.forEach(function (c, n) {
        if (n) { var or = document.createElement("span"); or.className = "p-iat-or"; or.textContent = uiT("oder", "or"); el.appendChild(or); }
        var s = document.createElement("span");
        s.className = attribute(c) ? "p-iat-cat is-attribute" : "p-iat-cat";
        s.textContent = cats[c];
        el.appendChild(s);
      });
    }
    function names(list) { return list.map(function (c) { return cats[c]; }).join(" " + uiT("oder", "or") + " "); }
    function introBlock() {
      var block = plan[b];
      heads(leftEl, block.left); heads(rightEl, block.right);
      round.textContent = uiT("Runde {n} von {m}", "Round {n} of {m}", { n: b + 1, m: plan.length });
      word.textContent = ""; wrong.hidden = true;
      introText.textContent = uiT("Links: {l}. Rechts: {r}. So schnell wie möglich, Fehler einfach korrigieren.",
                                  "Left: {l}. Right: {r}. As quickly as you can; just correct any mistakes.",
                                  { l: names(block.left), r: names(block.right) });
      intro.hidden = false; inIntro = true;
      if (status) status.textContent = "";
      go.focus();  // the button reads the round and its sides (aria-describedby)
    }
    function show() {
      var t = plan[b].trials[i];
      word.textContent = words[t[0]][0];
      word.classList.toggle("is-attribute", attribute(words[t[0]][1]));
      erred = 0;
      var started = false;
      function go() { if (!started) { started = true; shownAt = performance.now(); active = true; } }
      interruption.painted(go);
    }
    function startBlock() {
      if (!inIntro) return;
      intro.hidden = true; inIntro = false; i = 0;
      task.focus();  // the button is gone: the focus stays in the task, where E and I work
      interruption.later(show, 400);
    }
    function respond(side) {
      if (inIntro || !active) return;
      var t = plan[b].trials[i];
      if (side !== t[1]) {
        erred = 1; wrong.hidden = false;
        if (status) status.textContent = uiT("Falsch, bitte die andere Seite wählen.", "Wrong, please choose the other side.");
        return;
      }
      active = false; wrong.hidden = true;
      if (status) status.textContent = "";
      // a long pause (tab switched) is capped at the server's MAX_RT (60 s), not lost: the D score drops it anyway
      results.push([b + 1, t[0], Math.min(Math.round(performance.now() - shownAt), 60000), erred]);
      word.textContent = "";
      i += 1;
      if (i < plan[b].trials.length) { interruption.later(show, 250); return; }
      b += 1;
      if (b < plan.length) { introBlock(); return; }
      running = false;
      interruption.done();
      answerOf(root).value = JSON.stringify({ o: order, t: results });
      taskDone(root, stage, task, uiT("Aufgabe abgeschlossen. Es kann weitergehen.", "Task completed. You can continue."));
      sync(false);
    }
    function start() {
      interruption.start();
      b = 0; results = []; answerOf(root).value = ""; running = true;
      stage.hidden = true; task.hidden = false;
      sync(true);
      introBlock();
    }
    root.addEventListener("click", function (e) {
      if (e.target.closest("[data-iat-start]")) start();
      if (e.target.closest("[data-iat-cannot]")) {
        // a missing value of its own (items/iat.py, CANNOT): the page can go on, the results leave it out
        answerOf(root).value = "cannot";
        taskDone(root, stage, task, uiT("Sie haben angegeben, dass Sie diese Aufgabe nicht bearbeiten können. Es kann weitergehen.",
                                        "You said that you cannot do this task. You can continue."), true);
        sync(false);
        return;
      }
      if (e.target.closest("[data-iat-go]")) startBlock();
      var key = e.target.closest("[data-iat-key]");
      if (key) respond(parseInt(key.dataset.iatKey, 10));
    });
    document.addEventListener("keydown", function (e) {
      if (!running || e.repeat || !task.contains(document.activeElement) || (e.target.closest && e.target.closest("input, textarea, select, [contenteditable]"))) return;
      var k = e.key.toLowerCase();
      if (inIntro && document.activeElement === go && (k === " " || k === "enter")) { e.preventDefault(); startBlock(); return; }
      if (k === "e") { e.preventDefault(); respond(0); }
      if (k === "i") { e.preventDefault(); respond(1); }
    });
  });
  sync(false);
})();

// Delay discounting (items/discounting.py): the same procedure as the server's replay, to show the next offer.
// The fifth choice of the quick procedure moves nothing here: it only decides ED50, which the server computes.
(function () {
  var roots = Array.prototype.slice.call(document.querySelectorAll("[data-dd]"));
  if (!roots.length) return;
  var answerOf = function (r) { return r.querySelector("[data-dd-answer]"); };
  var sync = taskPage(roots, answerOf);
  roots.forEach(function (root) {
    var cfg = JSON.parse(root.querySelector("[data-dd-task]").textContent);
    var stage = root.querySelector("[data-dd-stage]"), task = root.querySelector("[data-dd-task-box]");
    var progress = root.querySelector("[data-dd-progress]"), status = root.querySelector("[data-dd-status]");
    var sooner = root.querySelector('[data-dd-choice="S"]'), later = root.querySelector('[data-dd-choice="L"]');
    var choices = "", rts = [], shownAt = 0, active = false;
    var interruption = interruptibleTask(root, stage, task, function () {
      active = false; choices = ""; rts = []; answerOf(root).value = ""; task.classList.remove("is-changing"); sync(false);
    });
    var j = 0, now = 0, step = 0, c = 0, idx = 0, k = 0;
    function money(v) {
      var r = roundHalfUp(v, 2), t = r % 1 === 0 ? String(r) : r.toFixed(2);  // half up as the server (Z-9)
      return cfg.unit.split("{n}").join(cfg.comma ? t.replace(".", ",") : t);
    }
    function offer() {
      return cfg.mode === "quick" ? { now: cfg.amount / 2, when: cfg.ladder[idx] } : { now: now, when: cfg.delays[j] };
    }
    function show() {
      var o = offer();
      progress.textContent = (choices.length + 1) + " / " + cfg.total;
      sooner.querySelector("[data-dd-amount]").textContent = money(o.now);
      sooner.querySelector("[data-dd-when]").textContent = cfg.now;
      later.querySelector("[data-dd-amount]").textContent = money(cfg.amount);
      later.querySelector("[data-dd-when]").textContent = o.when;
      // a new offer said in words (accessibility concept D7): the buttons change under the focus without a sound
      if (status && choices.length) status.textContent = uiT("Neue Wahl {n} von {m}: {s} oder {l}.", "New choice {n} of {m}: {s} or {l}.",
        { n: choices.length + 1, m: cfg.total, s: (money(o.now) + " " + (cfg.now || "")).trim(), l: (money(cfg.amount) + " " + (o.when || "")).trim() });
      shownAt = performance.now(); active = true;
    }
    function choose(x) {
      if (!active) return;
      active = false;
      choices += x; rts.push(Math.round(performance.now() - shownAt));
      if (cfg.mode === "quick") {
        if (k < cfg.moves.length) idx += x === "L" ? cfg.moves[k] : -cfg.moves[k];
        k += 1;
      } else {
        now += x === "L" ? step : -step; step /= 2; c += 1;
        if (c === cfg.steps) { j += 1; now = cfg.amount / 2; step = cfg.amount / 4; c = 0; }
      }
      if (choices.length < cfg.total) { task.classList.add("is-changing"); interruption.later(function () { task.classList.remove("is-changing"); show(); }, 200); return; }
      interruption.done();
      answerOf(root).value = JSON.stringify({ c: choices, rt: rts });
      taskDone(root, stage, task, uiT("Alle Entscheidungen getroffen. Es kann weitergehen.", "All choices made. You can continue."));
      sync(false);
    }
    function start() {
      interruption.start();
      choices = ""; rts = []; j = 0; c = 0; k = 0; idx = cfg.start || 0; now = cfg.amount / 2; step = cfg.amount / 4;
      answerOf(root).value = "";
      stage.hidden = true; task.hidden = false;
      sync(true);
      show();
      sooner.focus();  // "Start" is gone: the focus goes to the first offer
    }
    root.addEventListener("click", function (e) {
      if (e.target.closest("[data-dd-start]")) start();
      var b = e.target.closest("[data-dd-choice]");
      if (b) choose(b.dataset.ddChoice);
    });
  });
  sync(false);
})();
