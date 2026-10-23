// Saved audio pieces read in again (offline L, Ralf 2026-10-22): the file "Tonstücke sichern" wrote in the interview
// view or on the phone (audiostore.js save) holds the pieces that did not reach the server, each with its part, seq,
// recording and start (or age); here they are sent one by one as the page that recorded them would have sent them
// (app/interview_audio.upload). A piece already there is skipped by the server; a part taken by another recording
// moves to the next free one, as on the recording page.
(function () {
  "use strict";
  var form = document.querySelector("[data-audio-rescue]");
  if (!form) return;
  var input = form.querySelector("input[type=file]"), stateEl = form.querySelector("[data-rescue-state]"),
      button = form.querySelector("button"), url = form.getAttribute("data-url"), csrf = form.getAttribute("data-csrf");
  function say(text, bad) { stateEl.textContent = text; stateEl.classList.toggle("danger-text", !!bad); }
  function bytes(b64) {
    var raw = atob(b64), out = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }
  function send(p, moved) {
    var q = "?part=" + (moved[p.rec + "|" + p.part] || p.part) + "&seq=" + p.seq + "&rec=" + encodeURIComponent(p.rec || "")
      + (p.start !== undefined && p.start !== null ? "&start=" + p.start : "&age=" + (p.age || 0));
    return fetch(url + q, { method: "POST", body: new Blob([bytes(p.data)], { type: p.type }), credentials: "same-origin",
                            headers: { "Content-Type": p.type, "X-CSRFToken": csrf || "", "X-Requested-With": "fetch" } })
      .then(function (r) {
        if (r.ok) return true;
        if (r.status === 409) return r.json().catch(function () { return {}; }).then(function (d) {
          if (d && d.part) { moved[p.rec + "|" + p.part] = d.part; return send(p, moved); }  // the part belongs to another recording
          throw new Error(d && d.error === "parts" ? de_en("zu viele Aufnahmeteile", "too many recording parts")
                                                   : de_en("ein Stück davor fehlt", "a piece before it is missing"));
        });
        throw new Error(r.status === 401 ? de_en("nicht angemeldet", "not signed in") : String(r.status));
      });
  }
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var file = input.files && input.files[0];
    if (!file) return;
    button.disabled = true;
    file.text().then(function (text) {
      var data = JSON.parse(text);
      if (!data || data.format !== "askurious-audio-pieces" || !Array.isArray(data.pieces)) throw new Error(de_en("keine Datei mit gesicherten Tonstücken", "not a file of saved audio pieces"));
      if (data.interview && String(data.interview) !== form.getAttribute("data-interview")) throw new Error(de_en("die Datei gehört zu einem anderen Interview", "the file belongs to another interview"));
      var moved = {}, done = 0, all = data.pieces.length;
      return data.pieces.reduce(function (chain, p) {
        return chain.then(function () {
          return send(p, moved).then(function () { done += 1; say(de_en(done + " von " + all + " Stücken übertragen …", done + " of " + all + " pieces sent …")); });
        });
      }, Promise.resolve()).then(function () { say(de_en("Fertig: " + all + " Stücke sind in der Aufnahme. Die Datei kann gelöscht werden.", "Done: " + all + " pieces are in the recording. The file can be deleted.")); });
    }).catch(function (err) {
      say(de_en("Einlesen nicht möglich: ", "Reading in not possible: ") + (err && err.message ? err.message : err), true);
    }).then(function () { button.disabled = false; });
  });
})();
