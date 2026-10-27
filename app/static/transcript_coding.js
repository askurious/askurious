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
