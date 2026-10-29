// Voice message (items/voice.py): tap to start, tap to stop, a timer and a maximum length; the recording goes into
// the file field of its item, so it is sent with the page (the form becomes multipart). Without MediaRecorder or
// DataTransfer the recorder stays hidden and only the text field (if any) is there.
(function () {
  var boxes = document.querySelectorAll("[data-voice]");
  if (!boxes.length) return;
  var able = !!(window.MediaRecorder && navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.DataTransfer);
  var types = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"];
  var mimeType = able ? types.filter(function (m) { return MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(m); })[0] || "" : "";
  var running = [];  // recordings in progress: a page sent meanwhile waits for them to end
  function clock(s) { return Math.floor(s / 60) + ":" + ("0" + (s % 60)).slice(-2); }

  boxes.forEach(function (box) {
    var rec = box.querySelector("[data-voice-recorder]"), problem = box.querySelector("[data-voice-problem]");
    var text = box.querySelector(".p-voice-text .p-hint");
    if (!able) {
      if (text) text.textContent = uiT("Ihre Antwort:", "Your answer:");
      else { problem.textContent = uiT("Aufnahmen sind in diesem Browser nicht möglich.", "Recording is not possible in this browser."); problem.hidden = false; }
      return;
    }
    var form = box.closest("form");
    if (form) form.enctype = "multipart/form-data";
    rec.hidden = false;
    var toggle = box.querySelector("[data-voice-toggle]"), label = box.querySelector("[data-voice-label]");
    var time = box.querySelector("[data-voice-time]"), player = box.querySelector("[data-voice-player]");
    var remove = box.querySelector("[data-voice-remove]"), file = box.querySelector("[data-voice-file]");
    var seconds = box.querySelector("[data-voice-seconds]"), keep = box.querySelector("[data-voice-keep]");
    var status = box.querySelector("[data-voice-status]");
    function tell(text) { if (status) status.textContent = text; }
    var max = parseInt(box.getAttribute("data-max"), 10) || 60;
    var recorder = null, stream = null, chunks = [], started = 0, tick = null, url = null, done = null;
    if (keep.value === "1") label.textContent = uiT("Neu aufnehmen", "Record again");

    function elapsed() { return Math.min(max, Math.round((Date.now() - started) / 1000)); }
    function say(msg) { problem.textContent = msg || ""; problem.hidden = !msg; }
    function clear() {
      file.value = ""; seconds.value = ""; keep.value = "";
      if (url) { URL.revokeObjectURL(url); url = null; }
      player.removeAttribute("src"); player.hidden = true; remove.hidden = true; time.textContent = "";
      label.textContent = uiT("Aufnahme starten", "Start recording");
    }
    function stop() {
      if (!recorder || recorder.state === "inactive") return;
      recorder.stop();
    }
    function start() {
      say("");
      navigator.mediaDevices.getUserMedia({ audio: true }).then(function (s) {
        stream = s; chunks = [];
        var options = { audioBitsPerSecond: 48000 };  // plenty for speech; keeps five minutes under 2 MB
        if (mimeType) options.mimeType = mimeType;
        recorder = new MediaRecorder(stream, options);
        recorder.ondataavailable = function (e) { if (e.data && e.data.size) chunks.push(e.data); };
        recorder.onstop = function () {
          var type = (recorder.mimeType || mimeType || "audio/webm").split(";")[0], n = elapsed();
          var blob = new Blob(chunks, { type: type }), dt = new DataTransfer();
          dt.items.add(new File([blob], "aufnahme." + (type.indexOf("mp4") >= 0 ? "m4a" : type.split("/")[1]), { type: type }));
          file.files = dt.files;
          seconds.value = n; keep.value = "";
          if (url) URL.revokeObjectURL(url);
          url = URL.createObjectURL(blob); player.src = url; player.hidden = false; remove.hidden = false;
          stream.getTracks().forEach(function (t) { t.stop(); });
          clearInterval(tick); recorder = null; stream = null;
          box.classList.remove("is-recording");
          time.textContent = uiT("Aufnahme", "Recording") + " " + clock(n);
          label.textContent = uiT("Neu aufnehmen", "Record again");
          tell(uiT("Aufnahme beendet, {t}.", "Recording stopped, {t}.", { t: clock(n) }));
          running.splice(running.indexOf(stop), 1);
          if (done) { var then = done; done = null; then(); }
        };
        recorder.start();
        started = Date.now(); running.push(stop);
        box.classList.add("is-recording");
        label.textContent = uiT("Aufnahme beenden", "Stop recording");
        player.hidden = true; remove.hidden = true;
        time.textContent = "0:00 / " + clock(max);
        tell(uiT("Aufnahme läuft, höchstens {t}.", "Recording, at most {t}.", { t: clock(max) }));
        tick = setInterval(function () {
          var n = elapsed();
          time.textContent = clock(n) + " / " + clock(max);
          if (n >= max) stop();
        }, 250);
      }).catch(function () {
        if (stream) stream.getTracks().forEach(function (track) { track.stop(); });
        recorder = null; stream = null;
        say((box.hasAttribute("data-in-app")  // the diary app: its own settings, not a browser's (AND-09)
              ? uiT("Aufnahme fehlgeschlagen. Prüfen Sie Mikrofon und Mikrofonfreigabe in den Einstellungen der App und versuchen Sie es erneut.",
                    "Recording failed. Check your microphone and the microphone permission in the app’s settings and try again.")
              : uiT("Aufnahme fehlgeschlagen. Prüfen Sie Mikrofon und Browserfreigabe und versuchen Sie es erneut.",
                    "Recording failed. Check your microphone and browser permission and try again.")) +
            (text ? " " + uiT("Oder schreiben Sie Ihre Antwort:", "Or type your answer:") : ""));
      });
    }
    toggle.addEventListener("click", function () { if (recorder) stop(); else start(); });
    remove.addEventListener("click", function () { clear(); toggle.focus(); });
    box.voiceFinish = function (then) { if (recorder) { done = then; stop(); } else then(); };
  });

  // a page sent while a recording runs: end it first, then send the page with it
  document.addEventListener("submit", function (e) {
    if (!running.length) return;
    e.preventDefault(); e.stopPropagation();  // the page's own checks run on the send that follows
    var form = e.target, by = e.submitter, left = 0;
    boxes.forEach(function (box) {
      if (box.voiceFinish && box.classList.contains("is-recording")) {
        left++;
        box.voiceFinish(function () { if (--left === 0) { if (form.requestSubmit) form.requestSubmit(by || undefined); else form.submit(); } });
      }
    });
  }, true);
})();
