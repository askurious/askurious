// "Aus Coscine wählen" (templates/coscine/_pick.html, app/coscine.py; experimental, Coscine-Ausbau K6): first the
// resources the person's token sees, then the files of the chosen one that fit; a table goes into its text field
// (which then saves itself like after "CSV-Datei laden"), an image goes to the server by the small form.
(function () {
  if (window.coscinePickReady) return;
  window.coscinePickReady = true;

  function load(url, done, note) {
    note.textContent = de_en("Coscine wird gefragt …", "Asking Coscine …");
    fetch(url, { credentials: "same-origin", headers: { "Accept": "application/json" } })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) { note.textContent = data.error; return; }
        note.textContent = "";
        done(data);
      })
      .catch(function () { note.textContent = de_en("Coscine antwortet gerade nicht.", "Coscine is not responding right now."); });
  }

  function fill(select, rows, empty) {
    select.textContent = "";
    if (!rows.length) {
      var none = document.createElement("option");
      none.value = ""; none.textContent = empty;
      select.appendChild(none);
      return;
    }
    rows.forEach(function (row) {
      var o = document.createElement("option");
      o.value = row.value; o.textContent = row.label;
      select.appendChild(o);
    });
  }

  function files(box) {
    var target = box.querySelector("[data-coscine-target]"), file = box.querySelector("[data-coscine-file]");
    var note = box.querySelector("[data-coscine-note]");
    if (!target.value) return;
    load(box.dataset.list + "&ziel=" + encodeURIComponent(target.value), function (data) {
      fill(file, data.files.map(function (f) { return { value: f.path, label: f.path }; }),
           de_en("keine passende Datei", "no matching file"));
    }, note);
  }

  document.addEventListener("click", function (e) {
    var open = e.target.closest && e.target.closest("[data-coscine-open]");
    if (open) {
      var box = open.closest("[data-coscine-pick]"), panel = box.querySelector("[data-coscine-panel]");
      panel.hidden = !panel.hidden;
      if (!panel.hidden && !box.dataset.loaded) {
        box.dataset.loaded = "1";
        load(box.dataset.list, function (data) {
          fill(box.querySelector("[data-coscine-target]"), data.targets,
               de_en("keine Ressource in Coscine", "no resource in Coscine"));
          files(box);
        }, box.querySelector("[data-coscine-note]"));
      }
      return;
    }
    var take = e.target.closest && e.target.closest("[data-coscine-take]");
    if (!take) return;
    var pick = take.closest("[data-coscine-pick]");
    var ziel = pick.querySelector("[data-coscine-target]").value, pfad = pick.querySelector("[data-coscine-file]").value;
    if (!ziel || !pfad) { e.preventDefault(); return; }
    if (pick.dataset.coscinePick === "image" || pick.dataset.coscinePick === "survey") {
      var form = take.form, prefix = pick.dataset.coscinePick === "survey" ? "coscine_" : "";
      form.elements[prefix + "ziel"].value = ziel;
      form.elements[prefix + "pfad"].value = pfad;
      return;  // the form goes to the server
    }
    var note = pick.querySelector("[data-coscine-note]");
    if (pick.dataset.coscinePick === "media") { media(pick, ziel, pfad, note); return; }
    load(pick.dataset.text + "?ziel=" + encodeURIComponent(ziel) + "&pfad=" + encodeURIComponent(pfad), function (data) {
      var field = document.getElementById(pick.dataset.target);
      field.value = data.text;
      field.dispatchEvent(new Event("change", { bubbles: true }));
      note.textContent = de_en("Übernommen.", "Taken.");
    }, note);
  });

  // video or audio: the server loads it in the background; asked every 3 seconds until it is there, then the page
  // shows it (reload)
  function media(pick, ziel, pfad, note) {
    var body = new FormData();
    body.append("ziel", ziel); body.append("pfad", pfad); body.append("key", pick.dataset.key || "");
    note.textContent = de_en("Wird aus Coscine geladen …", "Loading from Coscine …");
    fetch(pick.dataset.post, { method: "POST", body: body, credentials: "same-origin",
                               headers: { "X-CSRFToken": csrfToken(), "Accept": "application/json" } })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) { note.textContent = data.error; return; }
        var ask = function () {
          fetch(pick.dataset.state, { credentials: "same-origin", headers: { "Accept": "application/json" } })
            .then(function (r) { return r.json(); })
            .then(function (s) {
              var st = (s.states || {})[pick.dataset.key || ""] || {};
              if (st.state === "running") { setTimeout(ask, 3000); return; }
              if (st.state === "error") { note.textContent = st.error || de_en("Fehlgeschlagen.", "Failed."); return; }
              window.location.reload();
            })
            .catch(function () { setTimeout(ask, 3000); });
        };
        ask();
      })
      .catch(function () { note.textContent = de_en("Coscine antwortet gerade nicht.", "Coscine is not responding right now."); });
  }

  document.addEventListener("change", function (e) {
    if (e.target.matches && e.target.matches("[data-coscine-target]")) files(e.target.closest("[data-coscine-pick]"));
  });
})();
