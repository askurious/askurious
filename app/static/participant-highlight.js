// Text highlighting (items/highlight.py): choose a marker above the text, then tap words; tapping a word again
// with the same marker removes it, another marker recolours it. With a mouse one can drag over several words.
// Keyboard: arrow keys move between words, Space/Enter marks, the keys 1-3 choose the marker.
(function () {
  document.querySelectorAll("[data-hl]").forEach(function (root) {
    var input = root.querySelector("[data-hl-value]"), tools = root.querySelectorAll("[data-hl-tool]");
    var words = Array.prototype.slice.call(root.querySelectorAll("[data-hl-w]"));
    var clear = root.querySelector("[data-hl-clear]"), count = root.querySelector("[data-hl-count]");
    var tool = 1, value = {};
    // with several markers the marker of a word is also said (its name) and shown (its number), not only coloured
    var multi = tools.length > 1, names = {};
    tools.forEach(function (t) { names[t.dataset.hlTool] = t.dataset.hlName; });
    if (multi) root.classList.add("p-hl-multi");
    try { value = JSON.parse(input.value || "{}") || {}; } catch (e) { value = {}; }
    function sync() {
      input.value = JSON.stringify(value);
      var n = Object.keys(value).length;
      clear.hidden = !n;
      count.textContent = n ? (n === 1 ? uiT("{n} Wort markiert", "{n} word marked", {n: n}) : uiT("{n} Wörter markiert", "{n} words marked", {n: n})) : "";
    }
    function set(w, cat) {
      if (cat) { w.dataset.cat = cat; value[w.dataset.hlW] = cat; }
      else { delete w.dataset.cat; delete value[w.dataset.hlW]; }
      w.setAttribute("aria-pressed", cat ? "true" : "false");
      if (multi) {
        if (cat) w.setAttribute("aria-label", w.textContent + ", " + names[cat]); else w.removeAttribute("aria-label");
      }
    }
    function choose(k) {
      tool = k;
      tools.forEach(function (t) {
        if (!t.hasAttribute("role")) return;
        t.setAttribute("aria-checked", String(Number(t.dataset.hlTool) === k));
        t.tabIndex = Number(t.dataset.hlTool) === k ? 0 : -1;
      });
    }
    words.forEach(function (w, i) {
      w.setAttribute("role", "button");
      set(w, Number(w.dataset.cat) || 0);
      w.tabIndex = i === 0 ? 0 : -1;
    });
    tools.forEach(function (t, i) {
      t.addEventListener("click", function () { choose(Number(t.dataset.hlTool)); });
      // a radio group: the arrow keys move between the markers and choose them
      t.addEventListener("keydown", function (e) {
        if (!multi) return;
        var step = (e.key === forward() || e.key === "ArrowDown") ? 1 : (e.key === back() || e.key === "ArrowUp") ? -1 : 0;
        if (!step) return;
        var to = tools[(i + step + tools.length) % tools.length];
        choose(Number(to.dataset.hlTool)); to.focus(); e.preventDefault();
      });
    });
    clear.addEventListener("click", function () {
      words.forEach(function (w) { set(w, 0); }); sync();
      // the button hides itself: the focus goes into the text (the word one reaches with Tab), not to the top of the page
      var to = words.filter(function (w) { return w.tabIndex === 0; })[0] || words[0];
      if (to) to.focus();
    });
    var painting = null, byMouse = false;  // the category a mouse drag paints with (0 = removes)
    root.addEventListener("pointerdown", function (e) {
      var w = e.target.closest("[data-hl-w]");
      if (!w || e.pointerType !== "mouse" || e.button !== 0) return;
      e.preventDefault();
      byMouse = true;
      painting = Number(w.dataset.cat) === tool ? 0 : tool;
      set(w, painting); sync();
    });
    root.addEventListener("pointerover", function (e) {
      if (painting === null) return;
      var w = e.target.closest("[data-hl-w]");
      if (w) { set(w, painting); sync(); }
    });
    document.addEventListener("pointerup", function () { painting = null; });
    root.addEventListener("click", function (e) {
      var w = e.target.closest("[data-hl-w]");
      if (!w) return;
      if (byMouse) { byMouse = false; return; }  // already handled on pointerdown
      set(w, Number(w.dataset.cat) === tool ? 0 : tool); sync();
    });
    root.addEventListener("keydown", function (e) {
      if (/^[1-3]$/.test(e.key) && root.querySelector('[data-hl-tool="' + e.key + '"]')) { choose(Number(e.key)); e.preventDefault(); return; }
      var w = e.target.closest("[data-hl-w]");
      if (!w) return;
      var i = words.indexOf(w), to = null;
      if (e.key === forward() || e.key === "ArrowDown") to = words[i + 1];
      else if (e.key === back() || e.key === "ArrowUp") to = words[i - 1];
      else if (e.key === "Home") to = words[0];
      else if (e.key === "End") to = words[words.length - 1];
      else if (e.key === " " || e.key === "Enter") { set(w, Number(w.dataset.cat) === tool ? 0 : tool); sync(); e.preventDefault(); return; }
      if (to) { w.tabIndex = -1; to.tabIndex = 0; to.focus(); e.preventDefault(); }
    });
    choose(1);
    sync();
  });
})();
