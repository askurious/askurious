// An analysis of interview transcripts (app/transcript_coding.py, templates/transcript_coding/project.html): the
// buttons "Hauptkategorie", "Kandidat vorschlagen" and "Unterkategorie" open one dialog; while the name is typed, the
// categories with a similar name show first, and once they were shown the button says "Trotzdem neu anlegen".
// The dialog of a new analysis (index.html): the Themenblöcke only for "dem Leitfaden".
(function () {
  var projectForm = document.querySelector("[data-tc-new-project]");
  if (projectForm) {
    var blocks = projectForm.querySelector("[data-tc-blocks]");
    var sync = function () {
      var start = projectForm.querySelector("[data-tc-start]:checked");
      if (blocks) blocks.hidden = !start || start.value !== "guide";
    };
    projectForm.addEventListener("change", sync);
    sync();
  }

  // merging a blurring pair (overview.html): the direction chosen sets what goes into what
  document.addEventListener("change", function (e) {
    var radio = e.target.closest && e.target.closest("[data-tc-merge]");
    if (!radio) return;
    var parts = radio.getAttribute("data-tc-merge").split(":"), f = radio.form;
    f.elements.target.value = parts[0];
    f.elements.source.value = parts[1];
  });

  var form = document.querySelector("[data-tc-form]");
  if (!form) return;
  var dialog = form.closest("dialog");
  var name = form.querySelector("[data-tc-name]"), box = form.querySelector("[data-tc-similar]");
  var list = form.querySelector("[data-tc-similar-list]"), submit = form.querySelector("[data-tc-submit]");
  var anyway = form.querySelector("[data-tc-anyway]"), where = form.querySelector("[data-tc-where]");
  var definition = form.querySelector("[data-tc-definition]");
  var timer = null, asked = 0, shown = false;

  function reset() {
    shown = false;
    anyway.value = "";
    box.hidden = true;
    list.textContent = "";
    submit.textContent = de_en("Anlegen", "Create");
  }

  // the category system, list and map: drag a category (the handle ⋮⋮ in the list, the node on the map; with a finger
  // on the map after holding it a moment, so that the map still scrolls). Onto a category: a dialog asks "unter B
  // hängen" or "mit B zusammenführen und vereinheitlichen"; onto its upper or lower edge between siblings: reordered;
  // onto "als Hauptkategorie" (the list's zone, the map's root): a main category. The server checks everything.
  var area = document.querySelector("[data-tc-csrf]"), dropMain = document.querySelector("[data-tc-drop-main]");
  var info = document.getElementById("tc-drop-data"), data = info ? JSON.parse(info.textContent) : null;
  var dialogDrop = document.querySelector("[data-tc-drop-dialog]");
  var drag = null, hold = null;
  function catOf(id) { return data && data.cats[String(id)]; }
  function edgeOf(target, y) {  // "before", "after" (between siblings) or "on"
    var r = target.getBoundingClientRect(), edge = Math.min(10, r.height / 4);
    return y < r.top + edge ? "before" : y > r.bottom - edge ? "after" : "on";
  }
  function dropAt(x, y) {
    var el = document.elementFromPoint(x, y);
    var target = el && el.closest("[data-tc-drop-main], [data-tc-cat]");
    if (!target || target === drag.card) return null;
    if (target.hasAttribute("data-tc-drop-main")) return { el: target, how: "main" };
    var a = catOf(drag.id), b = catOf(target.getAttribute("data-tc-cat"));
    if (!a || !b) return null;
    var how = edgeOf(target, y);
    if (how !== "on" && (!window.tcDrop.siblings(a, b) || a.propose)) how = "on";  // reordering others': not as a proposal
    if (how === "on" && (b.anc || []).indexOf(a.id) >= 0) return null;  // not onto something below itself
    return { el: target, how: how };
  }
  function mark(over) {
    if (drag.over && drag.over.el) drag.over.el.classList.remove("is-drop", "is-before", "is-after");
    if (over) over.el.classList.add(over.how === "before" ? "is-before" : over.how === "after" ? "is-after" : "is-drop");
    drag.over = over;
  }
  function start() {
    drag.moved = true;
    drag.card.classList.add("is-dragged");
    document.body.classList.add("tc-dragging");
    if (dropMain && dropMain.tagName !== "g") dropMain.hidden = false;
    document.querySelectorAll("[data-tc-cat]").forEach(function (el) {  // where it cannot go: greyed
      var c = catOf(el.getAttribute("data-tc-cat"));
      if (c && el !== drag.card && (c.anc || []).indexOf(Number(drag.id)) >= 0) el.classList.add("is-off");
    });
  }
  function post(url, fields) {
    var body = new FormData();
    body.append("csrf_token", area.getAttribute("data-tc-csrf"));
    body.append("json", "1");
    Object.keys(fields).forEach(function (k) { body.append(k, fields[k]); });
    return fetch(url, { method: "POST", body: body, credentials: "same-origin" })
      .then(function () { location.reload(); }, function () { location.reload(); });  // the page says what happened
  }
  document.addEventListener("pointerdown", function (e) {
    var handle = e.target.closest && e.target.closest("[data-tc-grab]");
    if (!handle || !area || e.button > 0) return;
    drag = { id: handle.getAttribute("data-tc-grab"), url: handle.getAttribute("data-tc-move-url"),
             card: handle.closest("[data-tc-cat]"), x: e.clientX, y: e.clientY, moved: false, over: null, pointer: e.pointerId };
    if (e.pointerType === "touch" && handle.tagName === "g") {  // the map: hold a moment, else it scrolls
      hold = setTimeout(function () { if (drag && !drag.moved) { start(); if (navigator.vibrate) navigator.vibrate(10); } }, 400);
      return;
    }
    e.preventDefault();
    try { handle.setPointerCapture(e.pointerId); } catch (err) { /* an SVG element in an old browser */ }
  });
  document.addEventListener("touchmove", function (e) { if (drag && drag.moved) e.preventDefault(); }, { passive: false });
  document.addEventListener("pointermove", function (e) {
    if (!drag) return;
    if (!drag.moved) {
      if (Math.abs(e.clientX - drag.x) + Math.abs(e.clientY - drag.y) < 6) return;
      if (hold) { clearTimeout(hold); hold = null; drag = null; return; }  // a finger that scrolls the map
      start();
    }
    var over = dropAt(e.clientX, e.clientY);
    if (!over || !drag.over || over.el !== drag.over.el || over.how !== drag.over.how) mark(over);
  });
  function end(send) {
    if (hold) { clearTimeout(hold); hold = null; }
    if (!drag) return;
    var d = drag;
    drag = null;
    d.card.classList.remove("is-dragged");
    document.body.classList.remove("tc-dragging");
    document.querySelectorAll(".is-off").forEach(function (el) { el.classList.remove("is-off"); });
    if (dropMain && dropMain.tagName !== "g") dropMain.hidden = true;
    var over = d.over;
    if (over) over.el.classList.remove("is-drop", "is-before", "is-after");
    if (!send || !d.moved || !over) return;
    if (over.how === "main") { post(d.url, { parent: "" }); return; }
    var b = over.el.getAttribute("data-tc-cat");
    if (over.how === "before" || over.how === "after") {
      var next = over.how === "before" ? b : nextSibling(d.id, b);
      post(d.url.replace(/\/move$/, "/order"), { before: next || "" });
      return;
    }
    openDrop(d.id, b, d.url);
  }
  function nextSibling(id, b) {  // the sibling after b (not the dragged one), for "after b"
    var a = catOf(id), list = Object.keys(data.cats).map(function (k) { return data.cats[k]; })
      .filter(function (c) { return window.tcDrop.siblings(a, c) || c.id === a.id; });
    var nodes = Array.prototype.map.call(document.querySelectorAll("[data-tc-cat]"), function (el) { return Number(el.getAttribute("data-tc-cat")); });
    list.sort(function (x, y) { return nodes.indexOf(x.id) - nodes.indexOf(y.id); });
    var rest = list.filter(function (c) { return c.id !== a.id; }).map(function (c) { return c.id; });
    var at = rest.indexOf(Number(b));
    return at >= 0 && at + 1 < rest.length ? rest[at + 1] : "";
  }
  document.addEventListener("pointerup", function () { end(true); });
  document.addEventListener("pointercancel", function () { end(false); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") end(false); });

  // the dialog after a drop onto a category: both side by side, "unter B hängen", or merge and harmonise
  function openDrop(aid, bid, moveUrl) {
    var a = catOf(aid), b = catOf(bid);
    if (!dialogDrop || !a || !b) return;
    var f = dialogDrop.querySelector("[data-tc-drop-merge]"), hangForm = dialogDrop.querySelector("[data-tc-drop-hang]");
    dialogDrop.querySelector("[data-tc-drop-title]").textContent = de_en("„{a}“ auf „{b}“ abgelegt", "“{a}” dropped onto “{b}”").replace("{a}", a.name).replace("{b}", b.name);
    [["a", a], ["b", b]].forEach(function (pair) {
      var box = dialogDrop.querySelector("[data-tc-drop-" + pair[0] + "]");
      box.querySelector("[data-f=name]").textContent = pair[1].name;
      box.querySelector("[data-f=definition]").textContent = pair[1].definition || "–";
      box.querySelector("[data-f=delimitation]").textContent = pair[1].delimitation || "–";
      box.querySelector("[data-f=anchor]").textContent = pair[1].anchor ? "„" + pair[1].anchor + "“" : "–";
      box.querySelector("[data-f=n]").textContent = pair[1].n;
    });
    var h = window.tcDrop.hang(data, a, b), m = window.tcDrop.merge(data, a, b);
    hangForm.action = moveUrl || "";
    hangForm.elements.parent.value = b.id;
    hangForm.querySelector("button").textContent = (h.propose ? de_en("Unter „{b}“ hängen vorschlagen", "Propose to put under “{b}”")
                                                    : de_en("Unter „{b}“ hängen", "Put under “{b}”")).replace("{b}", b.name);
    hangForm.querySelector("button").disabled = !h.ok || !moveUrl;
    hangForm.querySelector("[data-why]").textContent = h.why;
    f.elements.source.value = a.id;
    f.elements.target.value = b.id;
    f.elements.back.value = location.pathname + location.search;
    f.elements.name.value = b.name;
    f.elements.definition.value = b.definition || "";
    f.elements.delimitation.value = b.delimitation || "";
    f.querySelector("input[name=anchor][value=target]").checked = true;
    f.querySelector("input[name=anchor][value=source]").disabled = !a.anchor;
    f.querySelectorAll("[data-take]").forEach(function (btn) { btn.setAttribute("data-from", a.id); });
    f.querySelector("[data-tc-drop-merge-title]").textContent = (m.propose ? de_en("Vorschlag: mit „{b}“ zusammenführen und vereinheitlichen", "Proposal: merge with “{b}” and harmonise")
                                                                  : de_en("Mit „{b}“ zusammenführen und vereinheitlichen", "Merge with “{b}” and harmonise")).replace("{b}", b.name);
    var mergeButton = f.querySelector("button:not([type=button])");
    if (mergeButton) mergeButton.textContent = m.propose ? de_en("Vorschlag senden", "Send proposal") : de_en("Zusammenführen", "Merge");
    f.querySelectorAll("input, textarea, button").forEach(function (el) { if (!el.hasAttribute("data-keep")) el.disabled = !m.ok; });
    f.querySelector("[data-why]").textContent = m.why;
    if (!a.anchor) f.querySelector("input[name=anchor][value=source]").disabled = true;
    if (dialogDrop.showModal && !dialogDrop.open) dialogDrop.showModal();
  }
  if (dialogDrop) {
    dialogDrop.addEventListener("click", function (e) {  // the buttons to take a field over: the field as the dragged one has it
      var btn = e.target.closest("[data-take]");
      if (!btn) return;
      var a = catOf(btn.getAttribute("data-from")), field = btn.getAttribute("data-take");
      dialogDrop.querySelector("[data-tc-drop-merge]").elements[field].value = a[field] || "";
    });
    var wanted = /[?&]merge=(\d+),(\d+)/.exec(location.search);  // from merging a blurring pair on the overview
    if (wanted && data) {
      var a0 = catOf(wanted[1]);
      openDrop(wanted[1], wanted[2], a0 && document.querySelector('[data-tc-grab="' + wanted[1] + '"]')
        ? document.querySelector('[data-tc-grab="' + wanted[1] + '"]').getAttribute("data-tc-move-url") : "");
    }
  }

  // the list of the category system: fold what stands below a category (data-tc-anc names the ids above each one)
  document.addEventListener("click", function (e) {
    var fold = e.target.closest("[data-tc-fold]");
    if (!fold) return;
    var open = fold.getAttribute("aria-expanded") !== "false";
    fold.setAttribute("aria-expanded", open ? "false" : "true");
    fold.classList.toggle("is-folded", open);
    document.querySelectorAll('[data-tc-anc~="' + fold.getAttribute("data-tc-fold") + '"]').forEach(function (el) { el.hidden = open; });
  });

  document.addEventListener("click", function (e) {
    var opener = e.target.closest("[data-tc-new]");
    if (!opener || !dialog) return;
    var kind = opener.getAttribute("data-kind");
    form.querySelector("[data-tc-kind]").value = kind;
    form.querySelector("[data-tc-parent]").value = opener.getAttribute("data-parent") || "";
    where.textContent = kind === "sub" ? de_en("Unterkategorie unter „", "Subcategory under “") + opener.getAttribute("data-parent-name") + de_en("“", "”")
      : kind === "candidate" ? de_en("Kandidat für eine neue Hauptkategorie: Die Forschenden entscheiden, ob er eine wird.", "Candidate for a new main category: the researchers decide whether it becomes one.")
      : de_en("Neue Hauptkategorie", "New main category");
    definition.required = kind !== "main";
    form.querySelectorAll("input[name=name], textarea").forEach(function (el) { el.value = ""; });
    reset();
    if (dialog.showModal && !dialog.open) dialog.showModal();
    name.focus();
  });

  function show(rows) {
    list.textContent = "";
    rows.forEach(function (r) {
      var li = document.createElement("li");
      var head = document.createElement("strong");
      head.textContent = r.name;
      li.appendChild(head);
      var meta = [];
      if (r.parent) meta.push(de_en("unter ", "under ") + r.parent);
      if (r.kind === "candidate") meta.push(de_en("Kandidat", "candidate"));
      if (r.author) meta.push(de_en("von ", "by ") + r.author);
      if (r.former && r.former.length) meta.push(de_en("früher: ", "formerly: ") + r.former.join(", "));
      if (meta.length) { var m = document.createElement("span"); m.className = "muted small"; m.textContent = meta.join(" · "); li.appendChild(m); }
      if (r.definition) { var d = document.createElement("div"); d.className = "small"; d.textContent = r.definition; li.appendChild(d); }
      var take = document.createElement("a");
      take.href = "#k" + r.id;
      take.className = "button button-small button-secondary";
      take.textContent = de_en("Diese nehmen", "Use this one");
      take.addEventListener("click", function () { if (dialog.open) dialog.close(); });
      li.appendChild(take);
      list.appendChild(li);
    });
    box.hidden = !rows.length;
    shown = rows.length > 0;
    submit.textContent = shown ? de_en("Trotzdem neu anlegen", "Create anyway") : de_en("Anlegen", "Create");
  }

  name.addEventListener("input", function () {
    clearTimeout(timer);
    timer = setTimeout(function () {
      var mine = ++asked, q = name.value.trim();
      if (q.length < 2) { show([]); return; }
      fetch(form.getAttribute("data-similar-url") + "?q=" + encodeURIComponent(q), { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
        .then(function (r) { return r.ok ? r.json() : []; })
        .then(function (rows) { if (mine === asked) show(rows || []); })
        .catch(function () { /* the server checks again when the form is sent */ });
    }, 250);
  });

  form.addEventListener("submit", function () {
    if (shown) anyway.value = "1";  // the similar ones were shown: the person chose to create a new one
  });
})();
