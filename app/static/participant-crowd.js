// Crowd (feedback): previous participants as figures walking to their answer; the own figure
// highlighted. Positions persist for the session, so the next crowd element starts where the
// last one ended. On the same page as the question, the crowd waits for the own answer.
(function () {
  var NS = "http://www.w3.org/2000/svg";
  document.querySelectorAll("[data-crowd]").forEach(function (box) {
    var data = JSON.parse(box.dataset.crowd);
    var svg = box.querySelector(".p-crowd-svg"), stage = box.querySelector(".p-crowd-stage");
    var key = "crowd:" + data.survey, state = {};
    try { state = JSON.parse(sessionStorage.getItem(key) || "{}"); } catch (e) { state = {}; }
    function save() { try { sessionStorage.setItem(key, JSON.stringify(state)); } catch (e) { /* private mode */ } }
    function X(x) { return 30 + 940 * x; }
    function el(name, attrs, parent) {
      var e = document.createElementNS(NS, name);
      for (var k in attrs) e.setAttribute(k, attrs[k]);
      (parent || svg).appendChild(e); return e;
    }
    // floor and bins
    el("ellipse", { cx: 500, cy: 172, rx: 490, ry: 70, class: "crowd-floor" });
    for (var b = 1; b < data.bins; b++) el("line", { x1: X(b / data.bins), x2: X(b / data.bins), y1: 105, y2: 238, class: "crowd-bin" });
    var figures = {};
    // stable number in [0, 1) per figure (speed and entrance distance stay the same across pages)
    function frac(text) {
      var h = 2166136261;
      for (var i = 0; i < text.length; i++) { h ^= text.charCodeAt(i); h = Math.imul(h, 16777619); }
      return ((h >>> 0) % 10000) / 10000;
    }
    var SPEED = 0.3;  // share of the width per second; each figure 0.8x to 1.2x of it
    function offstage(id, x) {  // hidden start left or right of the stage, staggered so they arrive as a stream
      var gap = 0.06 + frac(id + ":o") * 0.45;
      return x < 0.5 ? -gap : 1 + gap;
    }
    function figure(id, d, hue, own) {
      var g = el("g", { class: "av" + (own ? " av-own" : "") });
      if (own) el("ellipse", { cx: 0, cy: 1, rx: 13, ry: 3.5, class: "av-shadow" }, g);  // own figure: a mark on the floor
      var bob = el("g", { class: "av-bob" }, g);
      el("line", { x1: -3, y1: -11, x2: -4, y2: 0, class: "av-leg av-leg-l" }, bob);
      el("line", { x1: 3, y1: -11, x2: 4, y2: 0, class: "av-leg av-leg-r" }, bob);
      el("rect", { x: -7, y: -31, width: 14, height: 22, rx: 7, class: "av-body" }, bob);
      el("circle", { cx: 0, cy: -38, r: 6.5, class: "av-head" }, bob);
      if (own) { var t = el("text", { x: 0, y: -51, class: "av-me" }, bob); t.textContent = data.me; }
      if (!own) g.style.setProperty("--av", "hsl(" + Math.round(200 + hue * 70) + ", 22%, " + Math.round(52 + hue * 16) + "%)");
      var f = { g: g, y: 108 + d * 118, s: own ? 1.6 : 0.72 + d * 0.5, x: null, v: own ? 1 : 0.8 + 0.4 * frac(id + ":v") };
      figures[id] = f;
      return f;
    }
    function put(f, x, animate) {
      if (animate && f.x !== null && Math.abs(x - f.x) < 1e-6) return;  // same target again (input and change both fire): keep the walk
      if (animate && f.x !== null) {
        var dur = Math.max(0.3, Math.abs(x - f.x) / (SPEED * f.v)), delay = 0;  // steady pace, no stretching
        f.g.style.transitionDuration = dur + "s";
        f.g.style.transitionDelay = delay + "s";
        f.g.classList.add("walking");
        clearTimeout(f.timer);
        f.timer = setTimeout(function () { f.g.classList.remove("walking"); }, (dur + delay) * 1000);
      } else {
        f.g.style.transitionDuration = "0s";
      }
      f.x = x;
      f.g.style.transform = "translate(" + X(x) + "px, " + f.y + "px) scale(" + f.s + ")";
    }
    // back to front, so nearer figures cover farther ones; the own figure stays in front of all
    function addFigures(list) {
      list.slice().sort(function (a, b) { return a.d - b.d; }).forEach(function (a) {
        var f = figure(a.id, a.d, a.h, false);
        if (me) svg.insertBefore(f.g, me.g);
      });
    }
    var me = null;
    addFigures(data.avatars);
    me = data.show_own ? figure("me", 0.95, 0, true) : null;
    var started = false, starting = false;
    // positions from an earlier crowd (e.g. the previous loop pass): shown at once, standing still,
    // so the crowd does not pop up after the answer; figures without a position join when it starts
    var hasPrev = data.prev !== false && data.avatars.some(function (a) { return state[a.id] !== undefined; });
    function showPrevious() {
      data.avatars.forEach(function (a) {
        var f = figures[a.id];
        if (state[a.id] !== undefined) put(f, state[a.id], false); else f.g.style.display = "none";
      });
      if (me) { if (state.me !== undefined) put(me, state.me, false); else me.g.style.display = "none"; }
    }
    if (hasPrev) showPrevious();
    function start(own) {
      var first = !started;
      started = true;
      data.avatars.forEach(function (a) {
        var f = figures[a.id];
        if (first && f.x === null) put(f, state[a.id] !== undefined ? state[a.id] : offstage(a.id, a.x), false);  // last position, or stream in from off stage
        f.g.style.display = "";
        state[a.id] = a.x;
      });
      if (me && own !== null) {
        if (me.x === null) put(me, state.me !== undefined ? state.me : offstage("me", own), false);
        me.g.style.display = "";
        state.me = own;
      } else if (me) {
        me.g.style.display = "none";
      }
      save();
      svg.getBoundingClientRect();  // apply the start positions before walking
      requestAnimationFrame(function () {
        if (first) data.avatars.forEach(function (a) { put(figures[a.id], a.x, true); });
        if (me && own !== null) put(me, own, true);
      });
    }
    // own answer: stored (earlier page) or live from the question on this page
    var form = box.closest("form"), live = data.live, field = null;
    if (data.own === null && live && form) field = form.querySelector('[name="' + live.name + '"]');
    function liveOwn() {
      var input = form.querySelector('[name="' + live.name + '"]:checked') || (field && field.type !== "radio" ? field : null);
      if (!input || input.value === "") return null;
      if (live.type === "code") { var i = (data.codes || []).indexOf(input.value); return i < 0 ? null : (i + 0.5) / data.bins; }
      var v = parseFloat(input.value);
      if (isNaN(v)) return null;
      if (live.type === "bin") return (v - live.lo + 0.5) / data.bins;
      return Math.min(Math.max(0.03 + 0.94 * (v - data.lo) / ((data.hi - data.lo) || 1), 0), 1);
    }
    function whenVisible(fn) {
      if (!("IntersectionObserver" in window)) return fn();
      var io = new IntersectionObserver(function (entries) {
        if (entries.some(function (e) { return e.isIntersecting; })) { io.disconnect(); fn(); }
      }, { threshold: 0.35 });
      io.observe(stage);
    }
    if (field) {
      var wait = box.querySelector("[data-crowd-wait]");
      // The button "Fix my answer" (app/feedback_lock.py): the crowd walks only after it, and the question is fixed from
      // then on; kept for the session, so reloading the page does not open the answer again
      var fixBox = data.lock ? box.querySelector("[data-crowd-fix]") : null, fixed = !fixBox;
      var question = field.closest(".p-item"), fixKey = "crowdfix:" + data.survey + ":" + live.name;
      function fixAnswer(keep) {
        fixed = true;
        if (fixBox) fixBox.hidden = true;
        if (question && !question.classList.contains("p-fixed")) {
          question.querySelectorAll("input, select, textarea, button").forEach(function (input) { input.disabled = true; });
          question.classList.add("p-fixed");
          question.querySelectorAll(".p-lock-hint").forEach(function (hint) { hint.remove(); });  // said, now done
          var note = document.createElement("p");
          note.className = "p-fixed-note"; note.textContent = box.dataset.fixedNote || "";
          question.appendChild(note);
        }
        if (keep) {
          var values = [];
          question.querySelectorAll("input, select, textarea").forEach(function (input) {
            if (!input.name || ((input.type === "radio" || input.type === "checkbox") && !input.checked)) return;
            values.push([input.name, input.value]);
          });
          try { sessionStorage.setItem(fixKey, JSON.stringify(values)); } catch (e) { /* private mode */ }
        }
        check();
      }
      // the question on this page and not fixed yet (data.fetch): the page holds no figures; the button sends the
      // answer, the server stores and fixes it and only then gives the figures (app/feedback_lock.py, audit N8)
      function fetchFigures(button) {
        var problem = box.querySelector('[data-crowd-problem]');
        if (!problem) {
          problem = document.createElement('p'); problem.dataset.crowdProblem = '';
          problem.className = 'p-error'; problem.setAttribute('role', 'alert'); box.appendChild(problem);
        }
        problem.textContent = '';
        var controller = new AbortController();
        var timeout = setTimeout(function () { controller.abort(); }, 15000);
        var body = new FormData();
        ["csrf_token", "pos"].forEach(function (name) {
          var input = form.querySelector('[name="' + name + '"]');
          if (input) body.append(name, input.value);
        });
        question.querySelectorAll("input, select, textarea").forEach(function (input) {
          if (!input.name || input.disabled || ((input.type === "radio" || input.type === "checkbox") && !input.checked)) return;
          body.append(input.name, input.value);
        });
        button.disabled = true;
        question.inert = true;  // what is sent stays what is shown while the answer is on its way
        fetch(data.fetch, { method: "POST", body: body, credentials: "same-origin", signal: controller.signal, headers: { "Accept": "application/json" } })
          .then(function (r) { return r.json().then(function (d) {
            if (!r.ok || d.error) throw { validation: r.status === 400 ? d.error : null };
            if (!Array.isArray(d.avatars)) throw {};
            return d;
          }); })
          .then(function (d) {
            clearTimeout(timeout);
            data.avatars = d.avatars || [];
            addFigures(data.avatars);
            question.inert = false;
            fixAnswer(true);
          })
          .catch(function (error) {
            clearTimeout(timeout); question.inert = false; button.disabled = liveOwn() === null;
            problem.textContent = (error.validation ? error.validation + ' ' : '') + uiT("Keine Bestätigung erhalten. Die Eingabe bleibt erhalten. Bitte erneut „Antwort festlegen“ wählen.", "No confirmation received. Your input is retained. Please choose “Fix my answer” again.");
          });
      }
      if (fixBox) {
        fixBox.querySelector("[data-crowd-fix-button]").addEventListener("click", function () {
          if (liveOwn() === null) return;
          if (data.fetch) fetchFigures(this); else fixAnswer(true);
        });
        var kept = null;
        try { kept = JSON.parse(sessionStorage.getItem(fixKey) || "null"); } catch (e) { kept = null; }
        if (kept && kept.length && question && !data.fetch) {  // with data.fetch the server knows what is fixed
          kept.forEach(function (pair) {
            question.querySelectorAll('[name="' + pair[0] + '"]').forEach(function (input) {
              if (input.type === "radio" || input.type === "checkbox") input.checked = input.value === pair[1];
              else input.value = pair[1];
            });
          });
          if (liveOwn() !== null) fixed = true;
          if (fixed) setTimeout(function () { fixAnswer(false); }, 0);
        }
      }
      function check() {
        var own = liveOwn();
        if (!fixed) {  // waiting for the button
          fixBox.hidden = false;
          fixBox.querySelector("[data-crowd-fix-button]").disabled = own === null;
          return;
        }
        if (own === null) return;
        if (!started) {
          if (starting) return;  // already waiting to start (input and change both fire)
          starting = true; wait.hidden = true; stage.hidden = false;
          whenVisible(function () { start(liveOwn()); });
        }
        else if (me) { me.g.style.display = ""; state.me = own; save(); put(me, own, true); }
      }
      if (liveOwn() === null || !fixed) {
        if (hasPrev) stage.hidden = false;  // earlier positions stay visible, without a note
        else { wait.hidden = false; stage.hidden = true; }
      }
      form.addEventListener("change", check);
      form.addEventListener("input", check);
      check();
    } else {
      whenVisible(function () { start(data.own); });
    }
  });
})();
