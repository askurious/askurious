// The recording page for a second device (interview/recorder.html, app/interview_remote.py): records like the
// interview view (a piece every five seconds, sent in order, a piece that did not get through sent again, kept in the
// browser's storage until the server has it, audiostore.js), reports its state every few seconds and stops when the
// interview view on the other device ends the interview. A pause ends the part, "Fortsetzen" begins a new one (I-11);
// each piece says how long ago its recording began, so the server places the part on the interview clock where it
// began and not where its first piece got through (I-6). An error of the recorder or a microphone that goes away is
// shown and reported (I-20). The screen is kept on while it records (Screen Wake Lock, where the browser has it).
(function () {
  // the page speaks the researcher's page language (<html lang>, app/lang.py)
  function de_en(de, en) { return document.documentElement.lang === "en" ? en : de; }
  var root = document.querySelector("[data-recorder]");
  if (!root) return;
  var toggle = root.querySelector("[data-rec-toggle]"), stopButton = root.querySelector("[data-rec-stop]");
  var bar = root.querySelector(".iv-recorder-buttons"), stateEl = root.querySelector("[data-rec-state]"), timeEl = root.querySelector("[data-rec-time]");
  var csrf = root.getAttribute("data-csrf"), audioUrl = root.getAttribute("data-audio-url"), stateUrl = root.getAttribute("data-state-url");
  // An ended interview (or one without consent) has no button; pieces an earlier visit kept on this device are sent
  // all the same (F11), so the store below is opened whether this page can record or not
  var canRecord = !!(toggle && window.MediaRecorder && navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
  if (toggle && !canRecord) {
    stateEl.textContent = de_en("Dieser Browser kann nicht aufnehmen.", "This browser cannot record.");
    toggle.disabled = true;
  }
  var part = parseInt(root.getAttribute("data-audio-part"), 10) || 1, queue = [], sending = false, refused = "", trouble = "", full = false;
  var recorder = null, stream = null, recMs = 0, recSince = null, lastData = 0, lock = null, ended = false;
  var store = { put: function () {}, drop: function () {} };
  // offline (Ralf 2026-10-22, F): the last request did not reach the server at all, or the browser says it has no
  // network; the state line says so, and the pieces go out at once when the connection is back. lowSpace (D): the
  // device has little storage left for the pieces that wait on it.
  var netDown = false, lowSpace = false, signedOut = false;  // signedOut (I): the sign-in ran out, 401
  function offline() { return netDown || navigator.onLine === false; }
  function seen(r) { netDown = false; signedOut = r.status === 401; }
  var saveButton = root.querySelector("[data-rec-save]"), batchOff = 0, saved = "", savedUntil = 0;
  var mic = !canRecord ? null : window.AskuriousMic ? AskuriousMic.attach(root) : { open: function () { return navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1 } }); }, show: function () {}, hide: function () {}, lock: function () {} };
  var mimeType = !canRecord ? "" : ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"].filter(function (m) {
    return MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(m); })[0] || "";
  function two(x) { return (x < 10 ? "0" : "") + x; }
  function recTime() { return recMs + (recSince ? Date.now() - recSince : 0); }
  function quiet() { return recSince && lastData ? Math.floor((Date.now() - lastData) / 1000) : 0; }
  function problem() { return trouble || (quiet() >= 15 ? de_en("seit " + quiet() + " s kein Ton vom Mikrofon", "no sound from the microphone for " + quiet() + " s") : ""); }
  function draw() {
    if (saveButton) saveButton.hidden = !(queue.length >= 2 && (offline() || signedOut || refused || full));
    var s = Math.floor(recTime() / 1000);
    if (timeEl) timeEl.textContent = (s >= 3600 ? Math.floor(s / 3600) + ":" : "") + two(Math.floor(s / 60) % 60) + ":" + two(s % 60);
    root.classList.toggle("is-trouble", !!(refused || problem()));
    if (!stateEl) return;
    if (refused) { stateEl.textContent = refused + (Date.now() < savedUntil ? " · " + saved : ""); return; }
    if (Date.now() < savedUntil) { stateEl.textContent = saved; return; }
    stateEl.textContent = (problem() ? "⚠ " + problem() + " · " : reconnecting ? de_en("Mikrofon wird wieder verbunden … · ", "Reconnecting the microphone … · ") : "")
      + (stream ? (recSince ? de_en("nimmt auf", "recording") : de_en("pausiert", "paused")) : (ended ? de_en("beendet", "ended") : ""))
      + (queue.length ? " · " + (queue.length === 1 ? de_en(queue.length + " Stück noch nicht übertragen", queue.length + " piece not sent yet")
                                                    : de_en(queue.length + " Stücke noch nicht übertragen", queue.length + " pieces not sent yet"))
                      : (ended ? de_en(", alles übertragen", ", everything sent") : ""))
      + (full ? " · " + de_en("Server-Speicher voll, die Stücke bleiben auf diesem Gerät", "server storage full, the pieces stay on this device") : "")
      + (offline() && (queue.length || stream) ? " · " + de_en("offline, die Stücke bleiben auf diesem Gerät und werden nachgeschickt", "offline, the pieces stay on this device and are sent later") : "")
      + (signedOut && !offline() && queue.length ? " · " + de_en("Anmeldung abgelaufen: den Interviewlink in einem neuen Tab öffnen, dann wird nachgeschickt", "sign-in expired: open the interviewer link in a new tab, then it is sent") : "")
      + (lowSpace ? " · " + de_en("Speicher auf diesem Gerät fast voll", "storage on this device almost full") : "");
  }
  function headers(type) { return { "Content-Type": type, "X-CSRFToken": csrf || "", "X-Requested-With": "fetch" }; }
  // the piece sent: pieces of an earlier visit may have been put before it meanwhile (queue = old.concat(queue)),
  // so it is taken out where it stands, not from the front (code review 2026-10-15, I8)
  function unqueue(piece) { var at = queue.indexOf(piece); if (at >= 0) queue.splice(at, 1); }
  function sendPiece() {
    if (sending || !queue.length || refused) return;
    sending = true;
    var list = Date.now() > batchOff && window.AskuriousAudioStore && AskuriousAudioStore.front ? AskuriousAudioStore.front(queue) : null;
    if (list) {  // offline J: what waited goes out a minute per request
      AskuriousAudioStore.sendBatch(audioUrl + "/batch", csrf, list, function (p) { return { part: p.rec.part, seq: p.seq, rec: p.rec.id, age: Math.max(0, Date.now() - p.rec.began) }; })
        .then(function (r) { seen(r); if (!r.ok) throw r.status; return r.json(); }, function () { netDown = true; throw 0; })
        .then(function () { full = false; list.forEach(function (p) { store.drop(p); unqueue(p); }); sending = false; draw(); sendPiece(); if (!queue.length && !stream) report(); })
        .catch(function (status) {
          sending = false; draw();
          if (status) { batchOff = Date.now() + 60000; sendPiece(); } else setTimeout(sendPiece, 3000);
        });
      return;
    }
    var piece = queue[0];
    fetch(audioUrl + "?part=" + piece.rec.part + "&seq=" + piece.seq + "&rec=" + piece.rec.id + "&age=" + Math.max(0, Date.now() - piece.rec.began),
          { method: "POST", body: piece.blob, credentials: "same-origin", headers: headers(piece.type) })
      .then(function (r) {
          seen(r);
          if (r.status === 409) return r.json().catch(function () { return {}; }).then(function (d) {
            // the part is taken by another recording (the other device): this recording moves to the next free part
            if (d && d.part) {
              piece.rec.part = d.part; part = Math.max(part, d.part + 1);
              queue.forEach(function (q) { if (q.rec === piece.rec) store.put(q); });
              throw 409;
            }
            // no other part to move to: more parts than allowed or a piece missing before this one; sending it again
            // cannot help, so the queue stops with the reason, the pieces stay in the browser (F15)
            throw { final: d && d.error === "parts" ? de_en("zu viele Aufnahmeteile für dieses Interview", "too many recording parts for this interview")
                                                    : de_en("ein Stück davor fehlt", "a piece before it is missing") };
          });
          if (r.status === 507) { full = true; throw 507; }  // the server's disk is full: kept here, sent later
          if (!r.ok) throw r.status; return r.json(); }, function () { netDown = true; throw 0; })
      .then(function () { full = false; store.drop(piece); unqueue(piece); sending = false; draw(); sendPiece(); if (!queue.length && !stream) report(); })
      .catch(function (status) {
        sending = false;
        if (status === 403 || status === 413 || status === 415) { refused = de_en("Aufnahme abgelehnt (" + status + ")", "Recording refused (" + status + ")"); draw(); return; }
        if (status && status.final) { refused = de_en("Aufnahme abgelehnt: ", "Recording refused: ") + status.final; draw(); return; }
        draw();
        setTimeout(sendPiece, status === 507 ? 30000 : status === 401 ? 10000 : 3000);
      });
  }
  function state() {
    if (stream) return recSince ? "recording" : "paused";
    return queue.length ? "paused" : "stopped";  // stopped only once every piece is there
  }
  var reporting = null;  // the report every few seconds; ends once the server confirmed "stopped" with nothing left (F18)
  function report() {
    if (!stream && !queue.length && !ended) return;  // nothing recorded yet: nothing to say
    var said = state();
    fetch(stateUrl, { method: "POST", credentials: "same-origin", headers: headers("application/json"),
                      body: JSON.stringify({ state: said, ms: recTime(), pending: queue.length, problem: problem() }) })
      .then(function (r) { seen(r); return r.ok ? r.json() : null; }, function () { netDown = true; return null; })
      .then(function (d) {
        if (d && d.stop && stream) finish();
        if (d && said === "stopped" && !stream && !queue.length && reporting) { clearInterval(reporting); reporting = null; }
      })
      .catch(function () { /* the next report */ });
  }
  function reportEvery() { if (!reporting) reporting = setInterval(report, 3000); }
  function wake(on) {
    if (on && navigator.wakeLock && !lock) navigator.wakeLock.request("screen").then(function (l) { lock = l; l.addEventListener("release", function () { lock = null; }); }).catch(function () { lock = null; });
    if (!on && lock) { lock.release(); lock = null; }
  }
  document.addEventListener("visibilitychange", function () { if (document.visibilityState === "visible" && stream) wake(true); });
  function record() {  // a new part on the open microphone
    var options = { audioBitsPerSecond: 32000 };  // Opus mono at 32 kbit/s is plenty for speech and Whisper (I-23)
    if (mimeType) options.mimeType = mimeType;
    var rec;
    try { rec = new MediaRecorder(stream, options); } catch (e) { rec = mimeType ? new MediaRecorder(stream, { mimeType: mimeType }) : new MediaRecorder(stream); }
    var type = (rec.mimeType || mimeType || "audio/webm").split(";")[0], seq = 0;
    var thisRec = { part: part, id: Math.random().toString(36).slice(2, 12), began: Date.now() };
    part += 1;
    rec.ondataavailable = function (e) {
      if (!e.data || !e.data.size) return;
      lastData = Date.now();
      var piece = { blob: e.data, rec: thisRec, seq: seq++, type: type, at: Date.now() };
      queue.push(piece); store.put(piece); sendPiece();
    };
    rec.onerror = function () { lost(de_en("Aufnahme unterbrochen (Fehler im Browser), bitte neu starten", "Recording interrupted (error in the browser), please start again")); };
    rec.start(5000);
    recorder = rec;
  }
  function watch(s) {  // the microphone itself: taken away (a call, a disconnected headset) or muted for a while
    s.getAudioTracks().forEach(function (t) {
      t.onended = function () { lost(de_en("Mikrofon getrennt, Aufnahme unterbrochen; bitte neu starten", "Microphone disconnected, recording interrupted; please start again")); };
      t.onmute = function () { trouble = de_en("Mikrofon stumm geschaltet (z. B. durch einen Anruf)", "Microphone muted (e.g. by a call)"); draw(); report(); };
      t.onunmute = function () { trouble = ""; draw(); report(); };
    });
  }
  // Ralf 2026-10-30: a recording that breaks off while it records (an error of the recorder, a microphone that goes
  // away for a moment) starts again by itself as a new part; only the seconds of the gap are missing, nothing is
  // recorded twice. Only after RESTART_MS tries in a row, or while paused, it ends as before with the reason shown.
  // What was recorded is kept and sent in any case.
  var RESTART_MS = [1000, 3000, 10000, 30000], restartTry = 0, restartTimer = null, reconnecting = false;
  function lost(reason) {
    if (!stream) { trouble = reason; draw(); report(); return; }
    if (recorder) { var old = recorder; recorder = null; old.onerror = null; if (old.state !== "inactive") try { old.stop(); } catch (e) { /* gone already */ } }
    if (!recSince || restartTry >= RESTART_MS.length) { finish(); trouble = reason; draw(); report(); return; }
    reconnecting = true; draw();
    if (!restartTimer) restartTimer = setTimeout(function () { restartTimer = null; reopen(reason); }, RESTART_MS[restartTry++]);
  }
  function reopen(reason) {  // the microphone anew (the permission holds, the browser does not ask again)
    if (!stream) { reconnecting = false; return; }  // stopped meanwhile
    mic.open().then(function (s) {
      if (!stream) { s.getTracks().forEach(function (t) { t.stop(); }); reconnecting = false; return; }
      stream.getTracks().forEach(function (t) { t.onended = null; t.stop(); });
      stream = s;
      watch(s); mic.show(s);
      if (recSince) { record(); lastData = Date.now(); }  // paused meanwhile: "Fortsetzen" records on the new microphone
      reconnecting = false; restartTry = 0; trouble = ""; draw(); report();
    }).catch(function () { lost(reason); });
  }
  function begin() {
    mic.open().then(function (s) {
      stream = s; trouble = ""; ended = false;
      watch(s);
      record();
      mic.show(stream); mic.lock(true);
      recMs = 0; recSince = lastData = Date.now();
      bar.classList.add("is-recording");
      toggle.textContent = "Ⅱ " + de_en("Pause", "Pause");
      stopButton.hidden = false;
      wake(true); reportEvery(); report(); draw();
      if (window.AskuriousAudioStore && AskuriousAudioStore.persist) AskuriousAudioStore.persist();
    }).catch(function () { refused = de_en("kein Zugriff auf das Mikrofon", "no access to the microphone"); draw(); });
  }
  function finish() {
    if (!stream) return;
    if (restartTimer) { clearTimeout(restartTimer); restartTimer = null; }
    reconnecting = false; restartTry = 0;
    if (recSince) { recMs += Date.now() - recSince; recSince = null; }
    if (recorder && recorder.state !== "inactive") recorder.stop();
    stream.getTracks().forEach(function (t) { t.stop(); });
    mic.hide(); mic.lock(false);
    recorder = null; stream = null; ended = true;
    bar.classList.remove("is-recording");
    toggle.textContent = "● " + de_en("Neue Aufnahme", "New recording");
    stopButton.hidden = true;
    wake(false); draw();
    setTimeout(report, 800);  // the last piece arrives a moment after stop()
  }
  if (canRecord) toggle.addEventListener("click", function () {
    if (!stream) { begin(); return; }
    if (recSince) {  // pause: the part ends here
      if (recorder && recorder.state !== "inactive") recorder.stop();
      recorder = null; recMs += Date.now() - recSince; recSince = null;
      bar.classList.remove("is-recording"); toggle.textContent = "● " + de_en("Fortsetzen", "Continue");
    } else {  // a new part
      if (reconnecting) return;  // the microphone comes back first, then "Fortsetzen" works again
      record(); recSince = lastData = Date.now();
      bar.classList.add("is-recording"); toggle.textContent = "Ⅱ " + de_en("Pause", "Pause");
    }
    report(); draw();
  });
  if (canRecord) stopButton.addEventListener("click", finish);
  if (window.AskuriousAudioStore) AskuriousAudioStore.open(root.getAttribute("data-audio-store")).then(function (s) {
    store = s;
    s.left().then(function (old) {  // pieces of an earlier visit that never reached the server: sent first
      old = old.filter(function (p) { return !queue.some(function (q) { return q.rec.id === p.rec.id; }); });
      if (!old.length) return;
      old.forEach(function (p) { part = Math.max(part, p.rec.part + 1); });
      queue = old.concat(queue); ended = ended || !stream;
      reportEvery(); draw(); sendPiece(); report();
    });
  });
  setInterval(draw, 1000);
  setInterval(function () {  // the device's storage, now and then while pieces are made or wait
    if (!(stream || queue.length) || !window.AskuriousAudioStore || !AskuriousAudioStore.low) return;
    AskuriousAudioStore.low().then(function (l) { lowSpace = l; draw(); });
  }, 60000);
  if (saveButton) saveButton.addEventListener("click", function () {  // offline L: the waiting pieces as a file
    var stamp = new Date().toISOString().slice(0, 16).replace(/[-:T]/g, "");
    AskuriousAudioStore.save(queue.slice(), parseInt(root.getAttribute("data-interview-id"), 10) || null, "handy-tonstuecke-" + stamp + ".askurious-audio.json",
                             function (p, now) { return { part: p.rec.part, seq: p.seq, rec: p.rec.id, age: Math.max(0, now - p.rec.began) }; })
      .then(function (n) { savedUntil = Date.now() + 30000; saved = de_en(n + " Stücke als Datei gesichert; auf der Seite „Aufnahme hochladen“ des Interviews wieder einlesen",
                                                                   n + " pieces saved as a file; read them in on the interview's page “Upload recording”"); draw(); })
      .catch(function () { savedUntil = Date.now() + 30000; saved = de_en("Sichern nicht möglich", "Saving not possible"); draw(); });
  });
  window.addEventListener("online", function () { netDown = false; draw(); sendPiece(); report(); });
  window.addEventListener("offline", draw);
  reportEvery();
  window.addEventListener("beforeunload", function (e) { if (stream || queue.length) { e.preventDefault(); e.returnValue = ""; } });
})();
