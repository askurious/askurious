// Card sort and Q sort (items/card_sort.py, items/q_sort.py): tap a card, then a group (or the stack, to take it
// back); with a mouse also drag. Nothing is picked when the page opens; after a card was laid by a tap the next one
// still on the stack is picked, and the line under the task says so (X38). On a phone the groups stand as a bar of
// buttons at the bottom of the screen while a card is picked, so nobody scrolls past the stack (S20, X39). A Q sort's columns
// (data-cap) take only so many cards; its presort (data-sort-presort) first asks for each card's pile, one card at
// a time, then shows the grid with the stack in piles (agree first). Own groups (data-sort-max-own): "+ Neue
// Gruppe" adds one with a name field, "Gruppe auflösen" puts its cards back. With data-sort-rank the cards in a
// group move with ↑/↓. The hidden field holds the sort as JSON after every change.
(function () {
  document.querySelectorAll("[data-sort]").forEach(function (box) {
    var value = box.querySelector("[data-sort-value]");
    var stack = box.querySelector(".p-sort-stack");
    var groupsBox = box.querySelector("[data-groups]");
    var q = box.hasAttribute("data-sort-q"), rank = box.hasAttribute("data-sort-rank");
    var maxOwn = parseInt(box.dataset.sortMaxOwn || "0", 10);
    var said = box.querySelector("[data-sort-said]");
    var bar = box.querySelector("[data-sort-bar]");
    var selected = null;
    function cards(scope) { return Array.prototype.slice.call((scope || box).querySelectorAll(".p-sort-card")); }
    function groups() { return Array.prototype.slice.call(groupsBox.querySelectorAll(".p-sort-group")); }
    function list(section) { return section.querySelector("[data-list]"); }
    function label(card) { return card.querySelector(".p-sort-pick").textContent.trim(); }
    function groupName(section) {
      var name = section.querySelector("[data-name]");
      if (name) return name.value.trim() || uiT("neuer Stapel", "new pile");
      var drop = section.querySelector("[data-drop]").cloneNode(true), count = drop.querySelector("[data-count]");
      if (count) count.remove();
      return drop.textContent.trim();
    }
    function full(section) {
      var cap = parseInt(section.dataset.cap || "0", 10);
      return cap > 0 && list(section).children.length >= cap;
    }
    function select(card) {
      selected = card;
      cards().forEach(function (c) {
        c.classList.toggle("is-selected", c === card);
        c.querySelector(".p-sort-pick").setAttribute("aria-pressed", c === card ? "true" : "false");
      });
      drawBar();
    }
    function tell(text) {  // said aloud and shown: under the task, and in the bar on a phone
      if (said) said.textContent = text;
      var line = bar && bar.querySelector("[data-bar-said]");
      if (line) line.textContent = text;
    }
    // the bar of targets (phones, CSS): the picked card and one button per group it can go to
    function drawBar() {
      if (!bar) return;
      bar.hidden = !selected;
      if (!selected) return;
      bar.querySelector("[data-bar-card]").textContent = label(selected);
      var targets = bar.querySelector("[data-bar-targets]"), here = selected.closest(".p-sort-group, .p-sort-stack");
      targets.textContent = "";
      [stack].concat(groups()).forEach(function (section) {
        if (section === here) return;
        var b = document.createElement("button"), name = groupName(section);
        b.type = "button";
        b.className = "p-sort-target" + (section === stack ? " is-stack" : "");
        b.textContent = name;
        if (section !== stack && full(section)) {
          b.disabled = true;
          b.textContent = name + " · " + uiT("voll", "full");
        }
        b.addEventListener("click", function () {
          if (selected && place(selected, section)) after(section);
        });
        targets.appendChild(b);
      });
    }
    function after(section) {  // a card laid by a tap: the next one from the stack, said
      var laid = selected;
      select(section === stack ? null : nextOnStack());
      if (selected && selected !== laid && said) tell(said.textContent + " " + uiT("Nächste Karte: {card}", "Next card: {card}", { card: label(selected) }));
      if (!selected) section.querySelector("[data-drop]").focus();
    }
    function nextOnStack() { return cards(stack)[0] || null; }
    function save() {
      var data;
      if (q) {
        data = {q: {}, p: {}};
        groups().forEach(function (g) {
          cards(g).forEach(function (c) { data.q[c.dataset.card] = parseInt(g.dataset.group, 10); });
        });
        cards().forEach(function (c) { if (c.dataset.pile !== undefined) data.p[c.dataset.card] = parseInt(c.dataset.pile, 10); });
      } else {
        data = {s: {}, own: {}};
        groups().forEach(function (g) {
          var codes = cards(g).map(function (c) { return c.dataset.card; });
          if (codes.length) data.s[g.dataset.group] = codes;
          var name = g.querySelector("[data-name]");
          if (name) data.own[g.dataset.group] = name.value.trim();
        });
      }
      value.value = JSON.stringify(data);
      [stack].concat(groups()).forEach(function (g) {
        var n = list(g).children.length, cap = parseInt(g.dataset.cap || "0", 10), count = g.querySelector("[data-count]");
        if (count) count.textContent = cap > 0 ? "(" + n + "/" + cap + ")" : "(" + n + ")";
        g.classList.toggle("is-full", cap > 0 && n >= cap);
      });
      stack.classList.toggle("is-empty", !cards(stack).length);
      var item = box.closest(".p-item");
      if (item) item.classList.remove("has-error", "has-nudge");
      box.dispatchEvent(new Event("change", {bubbles: true}));
      drawBar();
    }
    function place(card, section, before) {
      if (section !== stack && section !== card.closest(".p-sort-group") && full(section)) {
        tell(uiT("Diese Spalte ist voll.", "This column is full."));
        section.classList.add("is-refused");
        setTimeout(function () { section.classList.remove("is-refused"); }, 600);
        return false;
      }
      list(section).insertBefore(card, before || null);
      if (said) tell(said.dataset.text.replace("%s", label(card)).replace("%s", groupName(section)));
      save();
      return true;
    }
    function limit() {
      if (!make) return;
      var own = groupsBox.querySelectorAll(".p-sort-group.is-own").length;
      make.disabled = own >= maxOwn;
      box.querySelector("[data-sort-full]").hidden = own < maxOwn;
    }
    box.addEventListener("click", function (e) {
      var card = e.target.closest(".p-sort-card");
      if (card && e.target.closest(".p-sort-up")) {
        if (card.previousElementSibling) { card.parentNode.insertBefore(card, card.previousElementSibling); save(); e.target.closest("button").focus(); }
        return;
      }
      if (card && e.target.closest(".p-sort-down")) {
        if (card.nextElementSibling) { card.parentNode.insertBefore(card.nextElementSibling, card); save(); e.target.closest("button").focus(); }
        return;
      }
      if (card && e.target.closest(".p-sort-pick")) {
        select(selected === card ? null : card);
        return;
      }
      if (e.target.closest("[data-remove]")) {
        var gone = e.target.closest(".p-sort-group");
        cards(gone).forEach(function (c) { list(stack).appendChild(c); });
        gone.remove();
        save(); limit();
        return;
      }
      var section = e.target.closest(".p-sort-group, .p-sort-stack");
      if (!section || e.target.closest("[data-name]") || !selected || selected.closest(".p-sort-group, .p-sort-stack") === section) return;
      if (place(selected, section)) after(section);
    });
    if (bar) bar.querySelector("[data-bar-close]").addEventListener("click", function () { select(null); });
    box.addEventListener("input", function (e) { if (e.target.closest("[data-name]")) save(); });
    // own groups
    var make = box.querySelector("[data-sort-new]"), pattern = box.querySelector("template[data-sort-own]");
    if (make && pattern) {
      make.addEventListener("click", function () {
        // the smallest free number 101 ... 100 + max own among the own groups (given groups may have any code)
        var taken = {};
        groupsBox.querySelectorAll(".p-sort-group.is-own").forEach(function (g) { taken[g.dataset.group] = true; });
        var key = 101;
        while (taken[String(key)] && key < 100 + maxOwn) key++;
        if (taken[String(key)]) return;
        var section = pattern.content.firstElementChild.cloneNode(true);
        section.dataset.group = String(key);
        groupsBox.appendChild(section);  // empty: a picked card stays picked until a group is tapped (X38)
        section.querySelector("[data-name]").focus();
        save(); limit();
      });
      limit();
    }
    // dragging with a mouse
    var dragged = null;
    box.addEventListener("dragstart", function (e) {
      dragged = e.target.closest(".p-sort-card");
      if (!dragged) return;
      dragged.classList.add("is-dragging");
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", dragged.dataset.card);
    });
    box.addEventListener("dragover", function (e) {
      var section = dragged && e.target.closest(".p-sort-group, .p-sort-stack");
      if (!section) return;
      if (section !== stack && section !== dragged.closest(".p-sort-group") && full(section)) return;
      e.preventDefault();
      if (rank && section !== stack) {
        var over = e.target.closest(".p-sort-card");
        if (over && over !== dragged) {
          var r = over.getBoundingClientRect();
          list(section).insertBefore(dragged, e.clientY < r.top + r.height / 2 ? over : over.nextElementSibling);
        }
      }
    });
    box.addEventListener("drop", function (e) {
      var section = dragged && e.target.closest(".p-sort-group, .p-sort-stack");
      if (!section) return;
      e.preventDefault();
      if (dragged.closest(".p-sort-group, .p-sort-stack") !== section) place(dragged, section);
      else save();
    });
    box.addEventListener("dragend", function () {
      if (dragged) { dragged.classList.remove("is-dragging"); dragged = null; }
    });
    // the presort of a Q sort: every card once into a pile, then the grid
    var pre = box.querySelector("[data-pre]");
    function grid(show) {
      box.querySelectorAll("[data-grid-part]").forEach(function (p) { p.hidden = !show; });
      if (pre) pre.hidden = show;
      var a = box.querySelector("[data-pre-hint]"), b = box.querySelector("[data-grid-hint]");
      if (a) a.hidden = show;
      if (b) b.hidden = !show;
    }
    function piled() {
      // the stack in piles: agree first, then neutral, then disagree; within a pile in the given order
      var order = {"1": 0, "0": 1, "-1": 2};
      cards(stack).sort(function (a, b) { return (order[a.dataset.pile] || 0) - (order[b.dataset.pile] || 0); })
        .forEach(function (c) { list(stack).appendChild(c); });
    }
    if (pre) {
      var all = cards(), count = box.querySelector("[data-pre-count]"), shown = null, history = [];
      var cardText = pre.querySelector("[data-pre-card]"), undo = box.querySelector("[data-pre-undo]");
      var open = function () { return all.filter(function (c) { return c.dataset.pile === undefined; }); };
      var nextPre = function () {
        shown = open()[0] || null;
        if (!shown) { piled(); grid(true); select(null); save(); return; }
        cardText.textContent = label(shown);
        count.textContent = count.dataset.text.replace("%s", all.length - open().length + 1).replace("%s", all.length);
        undo.disabled = !history.length;
      };
      pre.addEventListener("click", function (e) {
        var to = e.target.closest("[data-pile-to]");
        if (to && shown) {
          shown.dataset.pile = to.dataset.pileTo; history.push(shown); save(); nextPre();
          if (!pre.hidden) to.focus();
        }
        if (e.target.closest("[data-pre-undo]") && history.length) {
          delete history.pop().dataset.pile; save(); nextPre();
        }
      });
      if (open().length) { grid(false); nextPre(); } else { piled(); grid(true); }
    }
    save();
  });
})();
