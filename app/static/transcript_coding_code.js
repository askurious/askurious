// The coding view of one interview (app/transcript_coding_view.py, templates/transcript_coding/code.html): draws the
// transcript with its coded passages, turns a marked passage into offsets in the transcript, offers the categories to
// assign (filter, keys 1-9 for the ones used last, Enter, a new category on the spot) and shows what a coded passage
// carries. The server widens a passage to whole words and stores it; the page draws it anew from the answer.
(function () {
  var root = document.querySelector("[data-tc-code]");
  var dataEl = document.getElementById("tc-data");
  if (!root || !dataEl) return;
  var data = JSON.parse(dataEl.textContent);
  var text = data.text, codings = data.codings, cats = data.categories;
  var anchors = data.anchors || {}, editable = data.editable || {};
  var box = root.querySelector("[data-tc-text]"), side = root.querySelector("[data-tc-side]");
  var pop = document.querySelector("[data-tc-pop]"), info = document.querySelector("[data-tc-info]");
  var filter = pop.querySelector("[data-tc-filter]"), list = pop.querySelector("[data-tc-list]");
  var quick = pop.querySelector("[data-tc-quick]"), status = document.querySelector("[data-tc-status]");
  var csrf = root.getAttribute("data-csrf"), base = root.getAttribute("data-base-url");
  var PALETTE = ["#8A4B14", "#2957A4", "#1F6B2A", "#7A3FD1", "#B3261E", "#0B6E86", "#7A5B00"];
  var RECENT_KEY = "tc-recent-" + root.getAttribute("data-project");
  var selection = null;  // {start, end} of the marked passage

  function catOf(id) { return cats.filter(function (c) { return c.id === id; })[0]; }
  function colour(cat) {
    if (!cat) return PALETTE[0];
    if (cat.colour) return cat.colour;
    var i = cats.indexOf(cat);
    return PALETTE[(i < 0 ? 0 : i) % PALETTE.length];
  }
  function label(cat) { return cat.parent ? cat.parent + " › " + cat.name : cat.name; }
  function say(msg) { if (status) status.textContent = msg; }
  function recent() {
    try { var r = JSON.parse(localStorage.getItem(RECENT_KEY) || "[]"); return Array.isArray(r) ? r : []; } catch (e) { return []; }
  }
  function remember(id) {
    var r = recent().filter(function (x) { return x !== id; });
    r.unshift(id);
    try { localStorage.setItem(RECENT_KEY, JSON.stringify(r.slice(0, 9))); } catch (e) { /* only a convenience */ }
  }
  function post(url, body) {
    return fetch(url, { method: "POST", credentials: "same-origin",
                        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf, "X-Requested-With": "fetch" },
                        body: JSON.stringify(body || {}) })
      .then(function (r) { return r.json().catch(function () { return { ok: false }; }); });
  }

  // --- drawing --------------------------------------------------------------------------------------------------
  function draw() {
    var cuts = {};
    var pos = 0;
    text.split("\n").forEach(function (line) { cuts[pos] = 1; cuts[pos + line.length] = 1; pos += line.length + 1; });
    codings.forEach(function (c) { cuts[c.start] = 1; cuts[c.end] = 1; });
    var points = Object.keys(cuts).map(Number).sort(function (a, b) { return a - b; });
    box.textContent = "";
    var start = 0;
    text.split("\n").forEach(function (line) {
      var p = document.createElement("p"), end = start + line.length;
      if (!line.length) p.appendChild(document.createElement("br"));
      var bounds = points.filter(function (x) { return x > start && x < end; });
      var edges = [start].concat(bounds, [end]);
      for (var i = 0; i < edges.length - 1; i++) {
        var s = edges[i], e = edges[i + 1];
        if (e <= s) continue;
        var span = document.createElement("span");
        span.textContent = text.slice(s, e);
        span.setAttribute("data-s", s);
        var here = codings.filter(function (c) { return c.start <= s && c.end >= e; });
        if (here.length) {
          var first = catOf(here[0].cat), last = catOf(here[here.length - 1].cat);
          span.className = "tc-hl" + (here.length > 1 ? " tc-hl-more" : "");
          span.style.setProperty("--tc", colour(first));
          span.style.setProperty("--tc2", colour(last));
          span.setAttribute("data-codings", here.map(function (c) { return c.id; }).join(" "));
          span.title = here.map(function (c) { var k = catOf(c.cat); return k ? k.name : ""; }).join(", ");
          span.tabIndex = 0;
        }
        p.appendChild(span);
      }
      box.appendChild(p);
      start = end + 1;
    });
    var n = document.querySelector("[data-tc-count]");
    if (n) n.textContent = codings.length;
    drawSide();
  }

  function drawSide() {
    side.textContent = "";
    cats.forEach(function (c) {
      var li = document.createElement("li");
      li.className = "tc-side-" + c.kind;
      var dot = document.createElement("span");
      dot.className = "tc-dot";
      dot.style.background = colour(c);
      li.appendChild(dot);
      var name = document.createElement("span");
      name.className = "tc-side-name";
      name.textContent = c.name + (c.kind === "candidate" ? " (" + de_en("Kandidat", "candidate") + ")" : "");
      if (c.definition) name.title = c.definition;
      li.appendChild(name);
      var n = document.createElement("span");
      n.className = "muted small";
      n.textContent = c.n;
      li.appendChild(n);
      side.appendChild(li);
    });
  }

  // --- marking -------------------------------------------------------------------------------------------------
  function offsetOf(node, off) {
    var el = node.nodeType === 3 ? node.parentNode : node;
    if (node.nodeType !== 3) {  // an element: the start of its child at `off`, or the end of the one before
      var child = node.childNodes[off];
      if (child && child.getAttribute && child.getAttribute("data-s") !== null) return Number(child.getAttribute("data-s"));
      var prev = node.childNodes[off - 1];
      if (prev && prev.getAttribute && prev.getAttribute("data-s") !== null) return Number(prev.getAttribute("data-s")) + prev.textContent.length;
      return null;
    }
    var s = el && el.getAttribute ? el.getAttribute("data-s") : null;
    return s === null ? null : Number(s) + off;
  }

  function marked() {
    var sel = window.getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount) return null;
    var r = sel.getRangeAt(0);
    if (!box.contains(r.startContainer) || !box.contains(r.endContainer)) return null;
    var a = offsetOf(r.startContainer, r.startOffset), b = offsetOf(r.endContainer, r.endOffset);
    if (a === null || b === null || a === b) return null;
    var rect = r.getBoundingClientRect();
    if (!rect.height) {  // some browsers give an empty box for a range: the line it starts in instead
      var host = r.startContainer.nodeType === 3 ? r.startContainer.parentNode : r.startContainer;
      rect = host.getBoundingClientRect();
    }
    return { start: Math.min(a, b), end: Math.max(a, b), rect: rect };
  }

  function placeBox(el, rect) {
    el.hidden = false;
    var top = rect.bottom + 8, left = Math.max(8, Math.min(rect.left, window.innerWidth - el.offsetWidth - 8));
    if (top + el.offsetHeight > window.innerHeight - 8) top = Math.max(8, rect.top - el.offsetHeight - 8);
    el.style.top = top + "px";
    el.style.left = left + "px";
  }

  function openPop(m) {
    selection = m;
    info.hidden = true;
    quick.hidden = true;
    filter.value = "";
    fill();
    placeBox(pop, m.rect);
    filter.focus();
  }
  function closePop() { pop.hidden = true; selection = null; }

  function matches() {
    var q = filter.value.trim().toLowerCase();
    if (!q) {
      var r = recent().map(catOf).filter(Boolean);
      var rest = cats.filter(function (c) { return r.indexOf(c) < 0; });
      return r.concat(rest);
    }
    return cats.filter(function (c) { return (c.name + " " + (c.parent || "")).toLowerCase().indexOf(q) >= 0; });
  }

  function fill() {
    list.textContent = "";
    var rec = recent();
    matches().slice(0, 12).forEach(function (c, i) {
      var li = document.createElement("li");
      var b = document.createElement("button");
      b.type = "button";
      b.className = "tc-pop-item";
      var dot = document.createElement("span");
      dot.className = "tc-dot";
      dot.style.background = colour(c);
      b.appendChild(dot);
      var t = document.createElement("span");
      t.textContent = label(c);
      b.appendChild(t);
      var key = rec.indexOf(c.id);
      if (!filter.value.trim() && key >= 0 && key < 9) {
        var k = document.createElement("kbd");
        k.textContent = String(key + 1);
        b.appendChild(k);
      }
      b.addEventListener("click", function () { assign(c.id); });
      li.appendChild(b);
      list.appendChild(li);
    });
    var q = filter.value.trim();
    var li = document.createElement("li");
    var nb = document.createElement("button");
    nb.type = "button";
    nb.className = "tc-pop-item tc-pop-new";
    nb.textContent = q ? de_en("+ Neue Kategorie „", "+ New category “") + q + de_en("“", "”") : de_en("+ Neue Kategorie", "+ New category");
    nb.addEventListener("click", function () { openQuick(q); });
    li.appendChild(nb);
    list.appendChild(li);
  }

  function assign(catId) {
    if (!selection) return;
    var m = selection;
    closePop();
    post(root.getAttribute("data-add-url"), { start: m.start, end: m.end, cat: catId }).then(function (d) {
      if (!d.ok) { say(d.error || de_en("Nicht gespeichert.", "Not saved.")); return; }
      remember(catId);
      codings.push(d.coding);
      codings.sort(function (a, b) { return a.start - b.start || a.end - b.end; });
      var c = catOf(catId);
      if (c) c.n += 1;
      window.getSelection().removeAllRanges();
      draw();
      say(de_en("Zugeordnet: ", "Assigned: ") + (c ? c.name : ""));
    });
  }

  // a new category on the spot: under a main category (a subcategory), or a main category / candidate
  function openQuick(name) {
    quick.hidden = false;
    quick.elements.name.value = name || "";
    var sel = quick.querySelector("[data-tc-parents]");
    sel.textContent = "";
    cats.filter(function (c) { return c.kind === "main"; }).forEach(function (c) {
      var o = document.createElement("option");
      o.value = c.id;
      o.textContent = c.name;
      sel.appendChild(o);
    });
    var top = document.createElement("option");
    top.value = "";
    top.textContent = root.getAttribute("data-may-main") ? de_en("— als Hauptkategorie —", "— as a main category —")
      : de_en("— als Kandidat für eine Hauptkategorie —", "— as a candidate for a main category —");
    sel.appendChild(top);
    quick.querySelector("[data-tc-quick-error]").hidden = true;
    quick.elements.definition.focus();
  }

  quick.addEventListener("submit", function (e) {
    e.preventDefault();
    var parent = quick.elements.parent.value;
    var body = { name: quick.elements.name.value, definition: quick.elements.definition.value,
                 kind: parent ? "sub" : "main", parent: parent, anyway: quick.getAttribute("data-anyway") || "" };
    post(root.getAttribute("data-quick-url"), body).then(function (d) {
      var err = quick.querySelector("[data-tc-quick-error]");
      if (!d.ok) {
        err.hidden = false;
        if (d.similar && d.similar.length) {
          err.textContent = d.error + " " + d.similar.map(function (s) { return s.name; }).join(", ") + ". " +
            de_en("Nochmals „Anlegen und zuordnen“ legt sie trotzdem an.", "“Create and assign” again creates it anyway.");
          quick.setAttribute("data-anyway", "1");
        } else {
          err.textContent = d.error || de_en("Nicht angelegt.", "Not created.");
        }
        return;
      }
      quick.removeAttribute("data-anyway");
      cats.push(d.category);
      editable[String(d.category.id)] = true;
      assign(d.category.id);
    });
  });
  pop.querySelector("[data-tc-quick-cancel]").addEventListener("click", function () { quick.hidden = true; filter.focus(); });

  filter.addEventListener("input", fill);
  filter.addEventListener("keydown", function (e) {
    if (e.key === "Escape") { closePop(); return; }
    if (e.key === "Enter") {
      e.preventDefault();
      var first = matches()[0];
      if (first) assign(first.id);
      return;
    }
    if (/^[1-9]$/.test(e.key) && !filter.value) {
      var id = recent()[Number(e.key) - 1];
      if (id && catOf(id)) { e.preventDefault(); assign(id); }
    }
  });

  box.addEventListener("mouseup", function () {
    setTimeout(function () { var m = marked(); if (m) openPop(m); }, 0);
  });
  document.addEventListener("keyup", function (e) {
    if (e.shiftKey && pop.hidden) { var m = marked(); if (m) openPop(m); }  // marked with the keyboard
  });
  document.addEventListener("mousedown", function (e) {
    if (!pop.hidden && !pop.contains(e.target)) closePop();
    if (!info.hidden && !info.contains(e.target) && !e.target.closest(".tc-hl")) info.hidden = true;
  });

  // --- a coded passage: its categories, removing, the anchor example ----------------------------------------------
  function showInfo(span) {
    var ids = (span.getAttribute("data-codings") || "").split(" ");
    info.textContent = "";
    codings.filter(function (c) { return ids.indexOf(c.id) >= 0; }).forEach(function (c) {
      var cat = catOf(c.cat), row = document.createElement("div");
      row.className = "tc-info-row";
      var head = document.createElement("p");
      head.className = "small";
      var strong = document.createElement("strong");
      strong.textContent = cat ? label(cat) : "";
      head.appendChild(strong);
      head.appendChild(document.createTextNode(" · " + (c.by_name || "")));
      if (anchors[String(c.cat)] === c.id) head.appendChild(document.createTextNode(" · " + de_en("Ankerbeispiel", "anchor example")));
      row.appendChild(head);
      var q = document.createElement("p");
      q.className = "small muted";
      q.textContent = "„" + c.quote + "“";
      row.appendChild(q);
      var actions = document.createElement("div");
      actions.className = "tc-info-actions";
      if (editable[String(c.cat)] && anchors[String(c.cat)] !== c.id) {
        var a = document.createElement("button");
        a.type = "button";
        a.className = "button button-small button-secondary";
        a.textContent = de_en("Als Ankerbeispiel", "As anchor example");
        a.addEventListener("click", function () {
          post(base + "/" + c.id + "/anchor").then(function (d) {
            if (d.ok) { anchors[String(c.cat)] = c.id; info.hidden = true; say(de_en("Ankerbeispiel gesetzt.", "Anchor example set.")); }
            else say(d.error || "");
          });
        });
        actions.appendChild(a);
      }
      if (c.mine) {
        var r = document.createElement("button");
        r.type = "button";
        r.className = "button button-small button-secondary";
        r.textContent = de_en("Entfernen", "Remove");
        r.addEventListener("click", function () { removeCoding(c.id); });
        actions.appendChild(r);
      }
      row.appendChild(actions);
      info.appendChild(row);
    });
    placeBox(info, span.getBoundingClientRect());
  }

  function removeCoding(id) {
    post(base + "/" + id + "/delete").then(function (d) {
      if (!d.ok) { say(de_en("Nicht entfernt.", "Not removed.")); return; }
      var gone = codings.filter(function (c) { return c.id === id; })[0];
      codings = codings.filter(function (c) { return c.id !== id; });
      if (gone) { var k = catOf(gone.cat); if (k) k.n = Math.max(0, k.n - 1); if (anchors[String(gone.cat)] === id) delete anchors[String(gone.cat)]; }
      info.hidden = true;
      draw();
      say(de_en("Entfernt.", "Removed."));
    });
  }

  box.addEventListener("click", function (e) {
    var span = e.target.closest(".tc-hl");
    if (!span || !window.getSelection().isCollapsed) return;
    showInfo(span);
  });
  box.addEventListener("keydown", function (e) {
    var span = e.target.closest && e.target.closest(".tc-hl");
    if (span && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); showInfo(span); }
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") { if (!info.hidden) info.hidden = true; }
  });
  document.querySelectorAll("[data-tc-remove]").forEach(function (b) {
    b.addEventListener("click", function () {
      post(base + "/" + b.getAttribute("data-tc-remove") + "/delete").then(function (d) { if (d.ok) b.closest("li").remove(); });
    });
  });

  draw();
})();
