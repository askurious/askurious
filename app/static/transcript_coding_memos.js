// Memos of an analysis (app/transcript_coding_memos.py, templates/transcript_coding/_memos.html): a button with
// data-memo-open (data-kind, data-rid, data-coding, data-cat, data-title) opens the dialog with the memos of that
// place; the author and the team change or delete theirs, a deleted one comes back with "Rückgängig". After a
// change the page loads anew when the dialog closes, so that counts and lists are current.
(function () {
  var dialog = document.querySelector("[data-memo-dialog]");
  var dataEl = document.getElementById("tc-memos");
  if (!dialog || !dataEl) return;
  var memos = JSON.parse(dataEl.textContent || "[]");
  var csrf = dialog.getAttribute("data-csrf"), base = dialog.getAttribute("data-base-url");
  var list = dialog.querySelector("[data-memo-list]"), text = dialog.querySelector("[data-memo-text]");
  var err = dialog.querySelector("[data-memo-error]"), titleEl = dialog.querySelector("[data-memo-title]");
  var target = null, changed = false;

  function post(url, body) {
    return fetch(url, { method: "POST", credentials: "same-origin",
                        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf, "X-Requested-With": "fetch" },
                        body: JSON.stringify(body || {}) })
      .then(function (r) { return r.json().catch(function () { return { ok: false }; }); },
            function () { return { ok: false, error: de_en("Nicht gespeichert: Verbindung unterbrochen. Bitte erneut versuchen.",
                                                            "Not saved: the connection broke off. Please try again.") }; });
  }
  function fail(d) { err.textContent = (d && d.error) || de_en("Nicht gespeichert.", "Not saved."); err.hidden = false; }
  function mine(m) {
    if (m.kind !== target.kind) return false;
    if (m.kind === "passage") return m.coding === target.coding;
    if (m.kind === "category") return String(m.cat) === String(target.cat);
    return String(m.rid) === String(target.rid);
  }
  function when(at) { return at || ""; }  // already in local time from the server

  function draw() {
    list.textContent = "";
    var here = memos.filter(mine);
    if (!here.length) {
      var none = document.createElement("li");
      none.className = "muted small";
      none.textContent = de_en("Noch kein Memo.", "No memo yet.");
      list.appendChild(none);
    }
    here.forEach(function (m) {
      var li = document.createElement("li");
      var head = document.createElement("p");
      head.className = "muted small";
      head.textContent = m.by + " · " + when(m.at) + (m.edited ? " · " + de_en("geändert", "edited") : "");
      var body = document.createElement("p");
      body.className = "tc-memo-text";
      body.textContent = m.text;
      li.appendChild(head);
      li.appendChild(body);
      if (m.mine) {
        var actions = document.createElement("div");
        actions.className = "tc-info-actions";
        var edit = document.createElement("button");
        edit.type = "button";
        edit.className = "button button-small button-secondary";
        edit.textContent = de_en("Ändern", "Edit");
        edit.addEventListener("click", function () {
          var area = document.createElement("textarea");
          area.rows = 4;
          area.maxLength = 4000;
          area.value = m.text;
          body.replaceWith(area);
          edit.textContent = de_en("Speichern", "Save");
          edit.onclick = function () {
            post(base + "/" + m.id, { text: area.value }).then(function (d) {
              if (!d.ok) return fail(d);
              m.text = d.memo.text; m.at = d.memo.at; m.edited = d.memo.edited; changed = true; draw();
            });
          };
          area.focus();
        }, { once: true });
        var del = document.createElement("button");
        del.type = "button";
        del.className = "button button-small button-secondary";
        del.textContent = de_en("Löschen", "Delete");
        del.addEventListener("click", function () {
          post(base + "/" + m.id + "/delete").then(function (d) {
            if (!d.ok) return fail(d);
            changed = true;
            li.textContent = "";
            var gone = document.createElement("span");
            gone.className = "muted small";
            gone.textContent = de_en("Gelöscht. ", "Deleted. ");
            var back = document.createElement("button");
            back.type = "button";
            back.className = "button button-small button-secondary";
            back.textContent = de_en("Rückgängig", "Undo");
            back.addEventListener("click", function () {
              post(base + "/" + m.id + "/restore").then(function (r) { if (r.ok) draw(); else fail(r); });
            });
            li.appendChild(gone);
            li.appendChild(back);
            memos = memos.filter(function (x) { return x.id !== m.id; });
            back.addEventListener("click", function () { memos.push(m); }, { once: true });
          });
        });
        actions.appendChild(edit);
        actions.appendChild(del);
        li.appendChild(actions);
      }
      list.appendChild(li);
    });
  }

  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-memo-open]");
    if (!b) return;
    e.preventDefault();
    e.stopPropagation();
    target = { kind: b.getAttribute("data-kind"), rid: b.getAttribute("data-rid"), coding: b.getAttribute("data-coding"),
               cat: b.getAttribute("data-cat") };
    titleEl.textContent = b.getAttribute("data-title") || "";
    text.value = "";
    err.hidden = true;
    draw();
    if (dialog.showModal && !dialog.open) dialog.showModal();
    text.focus();
  });
  dialog.querySelector("[data-memo-save]").addEventListener("click", function () {
    if (!target) return;
    var body = { kind: target.kind, text: text.value };
    if (target.rid) body.rid = Number(target.rid);
    if (target.coding) body.coding = target.coding;
    if (target.cat) body.cat = Number(target.cat);
    post(base, body).then(function (d) {
      if (!d.ok) return fail(d);
      memos.push(d.memo);
      changed = true;
      text.value = "";
      err.hidden = true;
      draw();
    });
  });
  dialog.addEventListener("close", function () { if (changed) location.reload(); });
})();
