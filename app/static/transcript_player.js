// Listening while correcting a transcript (templates/interview/transcript.html, Ralf 2026-10-29). Only drawn while
// the recording is there. The paragraph the cursor stands in is shown with its number ("Pos. 12", counted as in the
// coding view and the exports: paragraphs separated by an empty line, without one each line) and its time; "Hier
// abspielen" plays the recording from the start of that paragraph, two seconds earlier. The start of a paragraph is
// the Dresing & Pehl mark #hh:mm:ss-0# at the end of the paragraph before (the marks close a turn); the right part of
// a recording in several parts is found by the interview-clock time each part began at (data-start). F4 or Esc in
// the text plays and pauses; going on after a pause goes back two seconds. Tempo 0.75, 1 and 1.25.
var transcriptPlayer = (function () {
  var MARK = /#(\d{1,2}):(\d{2}):(\d{2})(?:-\d+)?#/g;
  function paragraphs(text) {
    var trimmed = text.replace(/^\s+|\s+$/g, "");
    var byEmpty = /\n[ \t\r]*\n/.test(trimmed), out = [], pos = 0, gap = true;
    text.split("\n").forEach(function (line) {
      var end = pos + line.length;
      if (line.trim()) {
        if (gap || !byEmpty) out.push([pos, end]); else out[out.length - 1][1] = end;
        gap = false;
      } else gap = true;
      pos = end + 1;
    });
    return out;
  }
  function seconds(m) { return Number(m[1]) * 3600 + Number(m[2]) * 60 + Number(m[3]); }
  function lastMark(text) {
    var m, last = null;
    MARK.lastIndex = 0;
    while ((m = MARK.exec(text))) last = seconds(m);
    return last;
  }
  function clock(s) {
    s = Math.max(0, Math.floor(s));
    var h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, x = s % 60;
    return (h < 10 ? "0" : "") + h + ":" + (m < 10 ? "0" : "") + m + ":" + (x < 10 ? "0" : "") + x;
  }
  // {n, start: interview-clock seconds where the paragraph at `cursor` begins (the mark closing the one before; 0 for
  // the first; null when the one before has no mark), end: its own mark or null}
  function at(text, cursor) {
    var paras = paragraphs(text);
    if (!paras.length) return null;
    var n = 1;
    for (var i = 0; i < paras.length; i++) if (paras[i][0] <= cursor) n = i + 1;
    var prev = n > 1 ? lastMark(text.slice(paras[n - 2][0], paras[n - 2][1])) : 0;
    return { n: n, start: prev, end: lastMark(text.slice(paras[n - 1][0], paras[n - 1][1])) };
  }
  // [index of the part that holds the interview-clock time t, seconds within it]; starts: when each part began
  function part(starts, t) {
    var best = 0;
    starts.forEach(function (s, i) { if (s <= t) best = i; });
    return [best, Math.max(0, t - (starts[best] || 0))];
  }
  return { paragraphs: paragraphs, lastMark: lastMark, clock: clock, at: at, part: part };
})();
if (typeof module !== "undefined" && module.exports) module.exports = transcriptPlayer;

(function () {
  if (typeof document === "undefined") return;
  var bar = document.querySelector("[data-tp]");
  var area = document.querySelector("textarea[name=text]");
  if (!bar || !area) return;
  var audios = Array.prototype.slice.call(document.querySelectorAll("[data-tp-part]"));
  if (!audios.length) return;
  var posEl = bar.querySelector("[data-tp-pos]"), playBtn = bar.querySelector("[data-tp-play]");
  var BACK = 2, tp = transcriptPlayer, clock = tp.clock;
  var current = audios[0], rate = 1;

  function here() { return tp.at(area.value, area.selectionStart || 0); }
  function show() {
    var h = here();
    if (!h) { posEl.textContent = ""; return; }
    posEl.textContent = "Pos. " + h.n + (h.start !== null ? " · " + de_en("ab ", "from ") + clock(h.start) : "") +
      (h.end !== null ? " – " + clock(h.end) : "");
    playBtn.disabled = h.start === null;
  }
  function partFor(t) {  // the part that holds the interview-clock time t, and the time within it
    var i = tp.part(audios.map(function (a) { return Number(a.getAttribute("data-start") || 0); }), t);
    return [audios[i[0]], i[1]];
  }
  function play(a, at) {
    audios.forEach(function (x) { if (x !== a) x.pause(); });
    current = a;
    a.playbackRate = rate;
    if (at !== undefined) {
      var go = function () { a.currentTime = at; a.play(); };
      if (a.readyState >= 1) go(); else a.addEventListener("loadedmetadata", go, { once: true });
    } else a.play();
  }
  function toggle() {
    if (!current.paused) { current.pause(); return; }
    play(current, Math.max(0, current.currentTime - BACK));  // on again: two seconds back
  }

  playBtn.addEventListener("click", function () {
    var h = here();
    if (!h || h.start === null) return;
    var p = partFor(Math.max(0, h.start - BACK));
    play(p[0], p[1]);
    area.focus();
  });
  bar.querySelectorAll("[data-tp-rate]").forEach(function (b) {
    b.addEventListener("click", function () {
      rate = Number(b.getAttribute("data-tp-rate"));
      audios.forEach(function (a) { a.playbackRate = rate; });
      bar.querySelectorAll("[data-tp-rate]").forEach(function (x) { x.setAttribute("aria-pressed", x === b ? "true" : "false"); });
    });
  });
  audios.forEach(function (a) { a.addEventListener("play", function () { current = a; a.playbackRate = rate; }); });
  area.addEventListener("keydown", function (e) {
    if (e.key === "F4" || (e.key === "Escape" && !e.altKey && !e.ctrlKey && !e.metaKey)) {
      e.preventDefault();
      toggle();
    }
  });
  ["keyup", "click", "select", "input", "focus"].forEach(function (ev) { area.addEventListener(ev, show); });
  show();
})();
