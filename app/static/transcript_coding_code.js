// The coding view of one interview (app/transcript_coding_view.py, templates/transcript_coding/code.html): draws the
// transcript with its coded passages, turns a marked passage into offsets in the transcript, offers the categories to
// assign (filter, keys 1-9 for the ones used last, Enter, a new category on the spot) and shows what a coded passage
// carries. The server widens a passage to whole words and stores it; the page draws it anew from the answer.
(function () {
  var root = document.querySelector("[data-tc-code]");
  var dataEl = document.getElementById("tc-data");
  if (!root || !dataEl) return;
  var data = JSON.parse(dataEl.textContent);
  var text = data.text, codings = data.codings, cats = data.categories, sugg = data.suggestions || [];
  var anchors = data.anchors || {}, editable = data.editable || {}, examples = data.examples || {};
  var box = root.querySelector("[data-tc-text]"), side = root.querySelector("[data-tc-side]");
  var pop = document.querySelector("[data-tc-pop]"), info = document.querySelector("[data-tc-info]");
  var filter = pop.querySelector("[data-tc-filter]"), list = pop.querySelector("[data-tc-list]");
  var quickDialog = document.querySelector("[data-tc-quick-dialog]"), quick = quickDialog.querySelector("[data-tc-quick]");
  var status = document.querySelector("[data-tc-status]");
  var pending = null, created = false;  // the marked passage while the dialog "Neue Kategorie" is open
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
      .then(function (r) { return r.json().catch(function () { return { ok: false }; }); },
            // no connection: an answer like the server's, so every caller says it and frees its buttons (UI/UX audit
            // 2026-10-29, WQ-10); whether it arrived is open, the list of codings shows it after a reload
            function () {
              return { ok: false, network: true,
                       error: de_en("Nicht gespeichert: Verbindung unterbrochen. Bitte erneut versuchen.",
                                    "Not saved: the connection broke off. Please try again.") };
            });
  }

  // --- drawing --------------------------------------------------------------------------------------------------
  function draw() {
    var cuts = {};
    var pos = 0;
    text.split("\n").forEach(function (line) { cuts[pos] = 1; cuts[pos + line.length] = 1; pos += line.length + 1; });
    codings.concat(sugg).forEach(function (c) { cuts[c.start] = 1; cuts[c.end] = 1; });
    var points = Object.keys(cuts).map(Number).sort(function (a, b) { return a - b; });
    box.textContent = "";
    var start = 0;
    text.split("\n").forEach(function (line, i) {
      var p = document.createElement("p"), end = start + line.length;
      // the paragraph number ("Pos." as in MAXQDA and the Excel export) in the margin: drawn by CSS from data-pos,
      // so it is no text of the transcript and cannot be marked
      if ((data.pos || [])[i]) p.setAttribute("data-pos", data.pos[i]);
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
        // AI suggestions not taken yet: dashed, in the colour of their category (app/transcript_coding_ai.py)
        var hereS = sugg.filter(function (c) { return c.start <= s && c.end >= e; });
        if (hereS.length) {
          span.classList.add("tc-sug");
          if (!here.length) span.classList.add("tc-hl");
          span.style.setProperty("--ts", colour(catOf(hereS[0].cat)));
          span.setAttribute("data-sugg", hereS.map(function (c) { return c.id; }).join(" "));
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
      if (c.level > 2) li.style.paddingLeft = (18 * (c.level - 1)) + "px";  // deeper levels further in
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

  // touch (Ralf 2026-10-28): the last pointer decides; a phone shows the list and the info box as a sheet from below
  var lastPointer = window.matchMedia && window.matchMedia("(pointer: coarse)").matches ? "touch" : "mouse";
  document.addEventListener("pointerdown", function (e) { lastPointer = e.pointerType || "mouse"; }, true);
  function touching() { return lastPointer !== "mouse"; }
  function narrow() { return !!(window.matchMedia && window.matchMedia("(max-width: 600px)").matches); }

  function placeBox(el, rect) {
    el.hidden = false;
    var sheet = narrow() && (el === pop || el === info);
    el.classList.toggle("is-sheet", sheet);
    if (sheet) {  // fixed at the bottom by its class; the marked passage stays visible above it
      el.style.top = el.style.left = "";
      var free = window.innerHeight - el.offsetHeight - 16;
      if (rect.bottom > free) window.scrollBy(0, rect.bottom - free);
      return;
    }
    var top = rect.bottom + 8, left = Math.max(8, Math.min(rect.left, window.innerWidth - el.offsetWidth - 8));
    if (top + el.offsetHeight > window.innerHeight - 8) top = Math.max(8, rect.top - el.offsetHeight - 8);
    el.style.top = top + "px";
    el.style.left = left + "px";
    keepInView(el);
  }

  // the box stays inside the window: after it grew (the form "Neue Kategorie" opened), after the window changed, and
  // while it is dragged by its handle (Ralf 2026-10-28: it stuck out at the bottom and could not be moved)
  function keepInView(el) {
    if (el.hidden || (el.tagName === "DIALOG" && !el.open) || el.classList.contains("is-sheet")) return;
    var r = el.getBoundingClientRect();
    var top = Math.min(r.top, window.innerHeight - 8 - r.height), left = Math.min(r.left, window.innerWidth - 8 - r.width);
    el.style.top = Math.max(8, top) + "px";
    el.style.left = Math.max(8, left) + "px";
  }
  window.addEventListener("resize", function () { keepInView(pop); keepInView(info); if (quickDialog.open && quickDialog.style.margin === "0px") keepInView(quickDialog); });
  [quickDialog].forEach(function (el) {
    var handle = el.querySelector("[data-tc-drag]");
    if (!handle) return;
    handle.addEventListener("pointerdown", function (e) {
      e.preventDefault();
      if (e.target.closest("button")) return;  // the close button stays a button
      var r = el.getBoundingClientRect(), dx = e.clientX - r.left, dy = e.clientY - r.top;
      el.style.margin = "0";  // a modal dialog is centred by its margins: from now on placed by left and top
      el.style.left = r.left + "px";
      el.style.top = r.top + "px";
      handle.setPointerCapture(e.pointerId);
      function move(ev) {
        el.style.left = ev.clientX - dx + "px";
        el.style.top = ev.clientY - dy + "px";
        keepInView(el);
      }
      function up() {
        handle.removeEventListener("pointermove", move);
        handle.removeEventListener("pointerup", up);
      }
      handle.addEventListener("pointermove", move);
      handle.addEventListener("pointerup", up);
    });
  });

  function openPop(m) {
    selection = m;
    info.hidden = true;
    filter.value = "";
    fill();
    placeBox(pop, m.rect);
    if (!touching()) filter.focus();  // on a phone or tablet the keyboard would cover the list
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
      if (!d.ok) {
        if (d.network) selection = m;  // the passage stays marked: choosing the category again tries once more
        say(d.error || de_en("Nicht gespeichert.", "Not saved."));
        return;
      }
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
    pending = selection;
    created = false;
    pop.hidden = true;  // the list closes; the passage stays marked for the dialog
    quickDialog.style.margin = "";
    quickDialog.style.left = quickDialog.style.top = "";
    if (quickDialog.showModal && !quickDialog.open) quickDialog.showModal();
    quick.elements.name.value = name || "";
    var sel = quick.querySelector("[data-tc-parents]");
    sel.textContent = "";
    cats.filter(function (c) { return c.kind !== "candidate" && c.deeper !== false; }).forEach(function (c) {
      var o = document.createElement("option");
      o.value = c.id;
      o.textContent = label(c);  // the whole path: a subcategory may stand under any level
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
      created = true;
      selection = pending;
      quickDialog.close();
      assign(d.category.id);
    });
  });
  quickDialog.querySelectorAll("[data-tc-quick-cancel]").forEach(function (b) {
    b.addEventListener("click", function () { quickDialog.close(); });
  });
  // Esc or "Abbrechen": back to the list of the marked passage
  quickDialog.addEventListener("close", function () {
    if (!created && pending) openPop(pending);
    pending = null;
  });

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
    if (touching()) return;  // a tap's mouseup: the selection below decides
    setTimeout(function () { var m = marked(); if (m) openPop(m); }, 0);
  });
  // marking by touch (long press, then the handles): the list opens once the selection rests a moment, and follows
  // the handles while it is open
  var resting = null, shown = "";
  document.addEventListener("selectionchange", function () {
    if (!touching()) return;
    clearTimeout(resting);
    resting = setTimeout(function () {
      var m = marked();
      if (!m) return;
      var key = m.start + ":" + m.end;
      if (!pop.hidden && key === shown) return;
      shown = key;
      openPop(m);
    }, 450);
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
    var sids = (span.getAttribute("data-sugg") || "").split(" ");
    info.textContent = "";
    sugg.filter(function (c) { return sids.indexOf(c.id) >= 0; }).forEach(function (c) {
      var cat = catOf(c.cat), row = document.createElement("div");
      row.className = "tc-info-row tc-info-sug";
      var head = document.createElement("p");
      head.className = "small";
      var strong = document.createElement("strong");
      strong.textContent = de_en("KI-Vorschlag: ", "AI suggestion: ") + (cat ? label(cat) : "");
      head.appendChild(strong);
      row.appendChild(head);
      if (c.reason) { var why = document.createElement("p"); why.className = "small muted"; why.textContent = c.reason; row.appendChild(why); }
      var actions = document.createElement("div");
      actions.className = "tc-info-actions";
      [["accept", de_en("Übernehmen", "Accept"), { ids: [c.id] }], ["discard", de_en("Verwerfen", "Discard"), { ids: [c.id] }],
       ["accept", de_en("Alle dieser Kategorie übernehmen", "Accept all of this category"), { cat: c.cat }],
       ["discard", de_en("Alle dieser Kategorie verwerfen", "Discard all of this category"), { cat: c.cat }]].forEach(function (a, i) {
        var b = document.createElement("button");
        b.type = "button";
        b.className = "button button-small " + (i === 0 ? "button-ai" : "button-secondary");
        b.textContent = a[1];
        b.addEventListener("click", function () { suggestionAction(a[0], a[2]); });
        actions.appendChild(b);
      });
      row.appendChild(actions);
      info.appendChild(row);
    });
    codings.filter(function (c) { return ids.indexOf(c.id) >= 0; }).forEach(function (c) {
      var cat = catOf(c.cat), row = document.createElement("div");
      row.className = "tc-info-row";
      var head = document.createElement("p");
      head.className = "small";
      var strong = document.createElement("strong");
      strong.textContent = cat ? label(cat) : "";
      head.appendChild(strong);
      head.appendChild(document.createTextNode(" · " + (c.origin === "ai" ? de_en("KI-Vorschlag, übernommen von ", "AI suggestion, accepted by ") + (c.accepted_by || c.by_name || "") : (c.by_name || ""))));
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
      // memos at this passage (app/transcript_coding_memos.py): the dialog of transcript_coding_memos.js
      var memoEl = document.getElementById("tc-memos"), memoN = 0;
      if (memoEl) JSON.parse(memoEl.textContent || "[]").forEach(function (m) { if (m.kind === "passage" && m.coding === c.id) memoN++; });
      var mb = document.createElement("button");
      mb.type = "button";
      mb.className = "button button-small button-secondary";
      mb.textContent = de_en("Memo", "Memo") + (memoN ? " (" + memoN + ")" : "");
      mb.setAttribute("data-memo-open", "");
      mb.setAttribute("data-kind", "passage");
      mb.setAttribute("data-rid", root.getAttribute("data-rid") || "");
      mb.setAttribute("data-coding", c.id);
      mb.setAttribute("data-title", "„" + (c.quote.length > 60 ? c.quote.slice(0, 60) + "…" : c.quote) + "“");
      mb.addEventListener("click", function () { info.hidden = true; });
      actions.appendChild(mb);
      // up to two further examples of the category (app/transcript_coding_view.py, toggle_example)
      if (editable[String(c.cat)] && anchors[String(c.cat)] !== c.id) {
        var isEx = (examples[String(c.cat)] || []).indexOf(c.id) >= 0;
        var ex = document.createElement("button");
        ex.type = "button";
        ex.className = "button button-small button-secondary";
        ex.textContent = isEx ? de_en("Kein Beispiel mehr", "No longer an example") : de_en("Als weiteres Beispiel", "As further example");
        ex.addEventListener("click", function () {
          post(base + "/" + c.id + "/example").then(function (d) {
            if (!d.ok) { say(d.error || ""); return; }
            var list = (examples[String(c.cat)] || []).filter(function (x) { return x !== c.id; });
            if (d.chosen) list.push(c.id);
            examples[String(c.cat)] = list;
            info.hidden = true;
            say(d.chosen ? de_en("Als weiteres Beispiel gewählt.", "Chosen as further example.") : de_en("Kein weiteres Beispiel mehr.", "No longer a further example."));
          });
        });
        actions.appendChild(ex);
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
      if (!d.ok) { say(d.network ? d.error : de_en("Nicht entfernt.", "Not removed.")); return; }
      var gone = codings.filter(function (c) { return c.id === id; })[0];
      codings = codings.filter(function (c) { return c.id !== id; });
      if (gone) { var k = catOf(gone.cat); if (k) k.n = Math.max(0, k.n - 1); if (anchors[String(gone.cat)] === id) delete anchors[String(gone.cat)]; }
      info.hidden = true;
      draw();
      say(de_en("Entfernt.", "Removed."));
    });
  }

  // --- AI suggestions: ask section by section, accept, discard, take back -----------------------------------------
  var suggUrl = root.getAttribute("data-sugg-url") || "";
  var undoBox = null;
  function offerUndo(ids, message) {
    say(message);
    if (!status) return;
    undoBox = document.createElement("button");
    undoBox.type = "button";
    undoBox.className = "button button-small button-secondary tc-undo";
    undoBox.textContent = de_en("Rückgängig", "Undo");
    undoBox.addEventListener("click", function () {
      post(suggUrl.replace("ACTION", "undo"), { ids: ids }).then(function (d) {
        if (d.ok) location.reload(); else say(d.error || de_en("Nicht zurückgenommen.", "Not undone."));
      });
    });
    status.appendChild(document.createTextNode(" "));
    status.appendChild(undoBox);
  }
  function suggestionAction(action, body) {
    var chosen = sugg.filter(function (c) { return body.ids ? body.ids.indexOf(c.id) >= 0 : c.cat === body.cat; });
    post(suggUrl.replace("ACTION", action), body).then(function (d) {
      if (!d.ok) { say(d.network ? d.error : de_en("Nicht gespeichert.", "Not saved.")); return; }
      var ids = chosen.map(function (c) { return c.id; });
      sugg = sugg.filter(function (c) { return ids.indexOf(c.id) < 0; });
      if (action === "accept") {
        (d.codings || []).forEach(function (c) { codings.push(c); var k = catOf(c.cat); if (k) k.n += 1; });
        codings.sort(function (a, b) { return a.start - b.start || a.end - b.end; });
      }
      info.hidden = true;
      draw();
      offerUndo(ids, action === "accept" ? de_en("Übernommen: ", "Accepted: ") + ids.length : de_en("Verworfen: ", "Discarded: ") + ids.length);
    });
  }
  var suggestButton = document.querySelector("[data-tc-suggest]");
  if (suggestButton) {
    suggestButton.addEventListener("click", function () {
      var total = Number(suggestButton.getAttribute("data-sections")) || 1, found = 0;
      suggestButton.disabled = true;
      function next(i) {
        if (i >= total) {
          suggestButton.disabled = false;
          say(de_en("Fertig: ", "Done: ") + found + de_en(" Vorschläge. Gestrichelte Stellen anklicken, um sie zu übernehmen oder zu verwerfen.", " suggestions. Click a dashed passage to accept or discard it."));
          return;
        }
        say(de_en("KI liest Abschnitt ", "AI reads section ") + (i + 1) + de_en(" von ", " of ") + total + " …");
        post(suggestButton.getAttribute("data-url"), { section: i }).then(function (d) {
          if (!d.ok) { suggestButton.disabled = false; say(d.error || de_en("Die KI hat nicht geantwortet.", "The AI did not answer.")); return; }
          sugg = sugg.concat(d.suggestions || []);
          found += (d.suggestions || []).length;
          draw();
          next(i + 1);
        });
      }
      next(0);
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

  // a hit of the search (app/transcript_coding_search.py) opened with ?at=start-end: marked, in view, categories open
  (function () {
    var at = /[?&]at=(\d+)-(\d+)/.exec(location.search);
    if (!at) return;
    var a = Number(at[1]), b = Number(at[2]);
    function point(off) {
      var spans = box.querySelectorAll("[data-s]");
      for (var i = 0; i < spans.length; i++) {
        var s = Number(spans[i].getAttribute("data-s")), node = spans[i].firstChild;
        if (node && off >= s && off <= s + node.length) return [node, off - s];
      }
      return null;
    }
    var p = point(a), q = point(b);
    if (!p || !q) return;
    var range = document.createRange();
    range.setStart(p[0], p[1]);
    range.setEnd(q[0], q[1]);
    var sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    p[0].parentNode.scrollIntoView({ block: "center" });
    var m = marked();
    if (m) openPop(m);
  })();
})();
