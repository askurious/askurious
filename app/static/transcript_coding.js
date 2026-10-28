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

  // "Zusammenführen" of a blurring pair (overview.html): the direction chosen sets what goes into what
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

  // the list of the category system: drag a category by its handle onto another (it then stands below it) or onto
  // "als Hauptkategorie"; merging stays its own action. The server checks everything (app/transcript_coding.py, move)
  var tree = document.querySelector("[data-tc-csrf]"), dropMain = document.querySelector("[data-tc-drop-main]");
  var drag = null;
  function dropAt(x, y, own) {
    var el = document.elementFromPoint(x, y);
    var target = el && el.closest("[data-tc-drop-main], [data-tc-cat]");
    if (!target || target === own) return null;
    if (target.hasAttribute("data-tc-cat") && (" " + target.getAttribute("data-tc-anc-of") + " ").indexOf(" " + drag.id + " ") >= 0) return null;
    return target;  // not onto something below itself
  }
  document.addEventListener("pointerdown", function (e) {
    var handle = e.target.closest && e.target.closest("[data-tc-grab]");
    if (!handle || !tree || e.button > 0) return;
    e.preventDefault();
    drag = { id: handle.getAttribute("data-tc-grab"), url: handle.getAttribute("data-tc-move-url"),
             card: handle.closest("[data-tc-cat]"), x: e.clientX, y: e.clientY, moved: false, over: null };
    handle.setPointerCapture(e.pointerId);
  });
  document.addEventListener("pointermove", function (e) {
    if (!drag) return;
    if (!drag.moved && Math.abs(e.clientX - drag.x) + Math.abs(e.clientY - drag.y) < 6) return;
    if (!drag.moved) {
      drag.moved = true;
      drag.card.classList.add("is-dragged");
      document.body.classList.add("tc-dragging");
      if (dropMain) dropMain.hidden = false;
    }
    var over = dropAt(e.clientX, e.clientY, drag.card);
    if (over !== drag.over) {
      if (drag.over) drag.over.classList.remove("is-drop");
      if (over) over.classList.add("is-drop");
      drag.over = over;
    }
  });
  function endDrag(send) {
    if (!drag) return;
    var d = drag;
    drag = null;
    d.card.classList.remove("is-dragged");
    document.body.classList.remove("tc-dragging");
    if (dropMain) dropMain.hidden = true;
    if (d.over) d.over.classList.remove("is-drop");
    if (!send || !d.moved || !d.over) return;
    var body = new FormData();
    body.append("csrf_token", tree.getAttribute("data-tc-csrf"));
    body.append("parent", d.over.hasAttribute("data-tc-drop-main") ? "" : d.over.getAttribute("data-tc-cat"));
    body.append("json", "1");
    fetch(d.url, { method: "POST", body: body, credentials: "same-origin" })
      .then(function () { location.reload(); })  // the page shows what happened (or why not)
      .catch(function () { location.reload(); });
  }
  document.addEventListener("pointerup", function () { endDrag(true); });
  document.addEventListener("pointercancel", function () { endDrag(false); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") endDrag(false); });

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
