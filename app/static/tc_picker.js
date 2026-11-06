// A category picker for the analyses of "Interviews kodieren" (feedback 2026-11-06): a <select data-tc-pick> becomes a
// search field over the category tree, each category with its number, its level indented and its passages; the select
// stays in the form (hidden) and keeps the value, so the forms post as before and work without script. Options carry
// data-num, data-level, data-main ("1": a main category), data-path (searched as well) and data-n (passages).
// Used by "Verschieben nach …" (transcript_coding/_category.html), "Zwei Kategorien zusammenführen"
// (transcript_coding/overview.html) and "Unter" in "Neue Kategorie" (transcript_coding/code.html).
(function () {
  var NUMBER = /^\d+(\.\d*)*$/;

  function hit(o, q) {
    if (!q) return true;
    if (NUMBER.test(q)) {
      var n = o.getAttribute("data-num") || "", bare = q.replace(/\.$/, "");
      return n === bare || n.indexOf(bare + ".") === 0;
    }
    return ((o.getAttribute("data-path") || "") + " " + o.textContent).toLowerCase().indexOf(q) >= 0;
  }

  function build(sel) {
    if (sel.tcPicker) { sel.tcPicker.draw(); return sel.tcPicker; }
    var wrap = document.createElement("div");
    wrap.className = "tc-pick";
    var search = document.createElement("input");
    search.type = "search";
    search.autocomplete = "off";
    search.className = "tc-pick-search";
    search.placeholder = de_en("Suchen: Name oder Nummer", "Search: name or number");
    search.setAttribute("aria-label", de_en("Kategorie suchen", "Search category"));
    var list = document.createElement("ul");
    list.className = "tc-pick-list";
    list.setAttribute("role", "listbox");
    wrap.appendChild(search);
    wrap.appendChild(list);
    sel.classList.add("visually-hidden");
    sel.setAttribute("tabindex", "-1");
    sel.setAttribute("aria-hidden", "true");
    sel.parentNode.insertBefore(wrap, sel.nextSibling);
    var active = -1, shown = [];

    function choose(o) {
      sel.value = o.value;
      sel.dispatchEvent(new Event("change", { bubbles: true }));
      draw();
    }
    function mark(i, scroll) {
      active = Math.max(0, Math.min(i, shown.length - 1));
      list.querySelectorAll(".tc-pick-item").forEach(function (b, k) {
        b.classList.toggle("is-active", k === active);
        if (k === active && scroll && b.scrollIntoView) b.scrollIntoView({ block: "nearest" });
      });
    }
    function draw() {
      var q = search.value.trim().toLowerCase();
      list.textContent = "";
      shown = Array.prototype.filter.call(sel.options, function (o) { return !o.disabled && hit(o, q); });
      shown.forEach(function (o) {
        var li = document.createElement("li");
        var b = document.createElement("button");
        b.type = "button";
        b.className = "tc-pick-item" + (o.getAttribute("data-main") === "1" ? " is-main" : "");
        b.setAttribute("role", "option");
        b.setAttribute("aria-selected", o.value === sel.value ? "true" : "false");
        var level = Number(o.getAttribute("data-level")) || 1;
        if (!q && level > 1) b.style.paddingLeft = (6 + 14 * (level - 1)) + "px";
        if (o.getAttribute("data-num")) {
          var num = document.createElement("span");
          num.className = "tc-side-num";
          num.textContent = o.getAttribute("data-num");
          b.appendChild(num);
        }
        var name = document.createElement("span");
        name.className = "tc-pick-name";
        name.textContent = q && o.getAttribute("data-path") ? o.getAttribute("data-path") : o.textContent;
        b.appendChild(name);
        if (o.hasAttribute("data-n")) {
          var n = document.createElement("span");
          n.className = "tc-side-n" + (Number(o.getAttribute("data-n")) ? "" : " is-zero");
          n.textContent = o.getAttribute("data-n");
          b.appendChild(n);
        }
        b.addEventListener("click", function () { choose(o); });
        li.appendChild(b);
        list.appendChild(li);
      });
      if (!shown.length) {
        var none = document.createElement("li");
        none.className = "muted small";
        none.textContent = de_en("Keine Kategorie gefunden.", "No category found.");
        list.appendChild(none);
      }
      var chosen = list.querySelector('[aria-selected="true"]');
      if (chosen && !q && chosen.scrollIntoView && wrap.offsetParent) chosen.scrollIntoView({ block: "nearest" });
      active = -1;
    }
    search.addEventListener("input", draw);
    search.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        mark(active + (e.key === "ArrowDown" ? 1 : -1), true);
      } else if (e.key === "Enter") {
        e.preventDefault();  // the form is sent with its own button
        var o = shown[active >= 0 ? active : 0];
        if (o) choose(o);
      }
    });
    sel.tcPicker = { draw: draw, search: search };
    draw();
    return sel.tcPicker;
  }

  // a select inside a closed fold is built when the fold opens (a list of many categories, each with its own)
  function prepare(scope) {
    (scope || document).querySelectorAll("select[data-tc-pick]").forEach(function (sel) {
      var fold = sel.closest("details");
      if (fold && !fold.open) {
        if (!fold.tcPickWaits) {
          fold.tcPickWaits = true;
          fold.addEventListener("toggle", function () { if (fold.open) fold.querySelectorAll("select[data-tc-pick]").forEach(build); });
        }
        return;
      }
      build(sel);
    });
  }
  window.tcPicker = build;
  prepare();
  document.addEventListener("tc:redrawn", function () { prepare(); });
})();
