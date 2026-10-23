// A recording made elsewhere (Zoom, Teams, Webex, BBB, a voice recorder) added to an interview
// (templates/interview/audio_upload.html, app/interview_audio.py upload_page): each file is cut into pieces and sent
// like the pieces of a recording in the interview view, one part per file. Only sound: a file with a picture is
// refused here and again on the server (concept konzept-online-interviews-2026-10-15.md, 3a).
(function () {
  "use strict";
  var form = document.querySelector("[data-audio-upload]");
  if (!form) return;
  var input = form.querySelector("input[type=file]"), list = form.querySelector("[data-file-list]"),
      box = form.querySelector("[data-files]"), row = form.querySelector("template[data-file-row]"),
      stateEl = form.querySelector("[data-upload-state]"), bar = form.querySelector("[data-upload-bar]"),
      button = form.querySelector("button"), piece = +form.dataset.piece, left = +form.dataset.left,
      partsLeft = +form.dataset.partsLeft, busy = false;
  var TYPES = { m4a: "audio/mp4", aac: "audio/mp4", mp3: "audio/mpeg", wav: "audio/wav",
                webm: "audio/webm", ogg: "audio/ogg", oga: "audio/ogg", opus: "audio/ogg" };
  var VIDEO = /\.(mp4|mov|m4v|mkv|avi|wmv|flv)$/i;
  function VIDEO_TEXT() { return de_en("Videodateien werden nicht angenommen. Bitte nur den Ton hochladen (bei Zoom die M4A-Datei).", "Video files are not accepted. Please upload the sound only (with Zoom the M4A file)."); }

  function say(text, bad) { stateEl.textContent = text; stateEl.classList.toggle("danger-text", !!bad); }
  function ext(file) { var m = /\.([a-z0-9]+)$/i.exec(file.name); return m ? m[1].toLowerCase() : ""; }
  function mb(bytes) { return (bytes / 1048576).toFixed(1).replace(".", document.documentElement.lang === "en" ? "." : ","); }

  // Whether the browser sees a picture in the file (a video track); files it cannot open count as sound.
  function picture(file) {
    return new Promise(function (resolve) {
      var v = document.createElement("video"), url = URL.createObjectURL(file), done = false;
      function end(found) { if (done) return; done = true; URL.revokeObjectURL(url); v.removeAttribute("src"); resolve(found); }
      v.preload = "metadata"; v.muted = true;
      v.onloadedmetadata = function () { end(v.videoWidth > 0); };
      v.onerror = function () { end(false); };
      setTimeout(function () { end(false); }, 5000);
      v.src = url;
    });
  }

  input.addEventListener("change", function () {
    list.textContent = "";
    Array.prototype.forEach.call(input.files, function (file) {
      var li = row.content.firstElementChild.cloneNode(true);
      li.querySelector("[data-name]").textContent = file.name + " (" + mb(file.size) + " MB)";
      list.appendChild(li);
    });
    box.hidden = !input.files.length;
    say("");
  });

  function send(url, body, type) {
    return fetch(url, { method: "POST", body: body, credentials: "same-origin",
                        headers: { "Content-Type": type, "X-CSRFToken": form.dataset.csrf, "X-Requested-With": "fetch" } })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { j.status = r.status; return j; }); });
  }

  function reason(answer) {
    if (answer.error === "video") return VIDEO_TEXT();
    if (answer.error === "consent") return de_en("Bitte bestätigen, dass alle Sprechenden eingewilligt haben.", "Please confirm that everyone who speaks has consented.");
    if (answer.error === "full" || answer.status === 507) return de_en("Der Speicher des Servers ist voll. Bitte später erneut versuchen.", "The server storage is full. Please try again later.");
    if (answer.status === 413) return de_en("Die Aufnahmen dieses Interviews wären zu groß.", "The recordings of this interview would be too large.");
    if (answer.status === 415) return de_en("Dieser Dateityp wird nicht angenommen.", "This file type is not accepted.");
    return de_en("Das Hochladen ist fehlgeschlagen ({s}).", "The upload failed ({s}).").replace("{s}", answer.status);
  }

  // One file as one part: pieces in order; a part another recording took meanwhile moves to the next free one.
  function upload(file, speaker, part, sent, total) {
    var rec = Math.random().toString(36).slice(2, 12), type = TYPES[ext(file)] || file.type;
    var base = form.dataset.url + "?source=upload&rec=" + rec + "&start=0&origin=" + encodeURIComponent(form.origin.value)
             + "&speaker=" + encodeURIComponent(speaker) + (form.all && form.all.checked ? "&all=1" : "");
    function next(seq) {
      var from = seq * piece;
      if (from >= file.size) return Promise.resolve(part);
      return send(base + "&part=" + part + "&seq=" + seq, file.slice(from, from + piece), type).then(function (answer) {
        if (answer.status === 409 && seq === 0 && answer.part) { part = answer.part; return next(0); }
        if (!answer.ok) throw new Error(reason(answer));
        sent.bytes += Math.min(piece, file.size - from);
        bar.value = sent.bytes / total;
        return next(seq + 1);
      });
    }
    return next(0);
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    if (busy) return;
    var files = Array.prototype.slice.call(input.files), speakers = list.querySelectorAll("[data-speaker]");
    if (!files.length) return;
    var total = files.reduce(function (s, f) { return s + f.size; }, 0);
    if (files.length > partsLeft) return say(de_en("Zu viele Dateien für dieses Interview.", "Too many files for this interview."), true);
    if (total > left) return say(de_en("Die Dateien sind zusammen zu groß (noch {mb} MB frei).", "The files are too large together ({mb} MB left).").replace("{mb}", mb(left)), true);
    var wrong = files.filter(function (f) { return VIDEO.test(f.name) || !TYPES[ext(f)]; })[0];
    if (wrong) return say(wrong.name + ": " + (VIDEO.test(wrong.name) ? VIDEO_TEXT() : de_en("Dieser Dateityp wird nicht angenommen.", "This file type is not accepted.")), true);
    busy = true; button.disabled = true;
    say(de_en("Dateien werden geprüft …", "Checking the files …"));
    Promise.all(files.map(picture)).then(function (pictures) {
      var shown = files.filter(function (f, i) { return pictures[i]; })[0];
      if (shown) throw new Error(shown.name + ": " + VIDEO_TEXT());
      var sent = { bytes: 0 }, part = +form.dataset.part;
      bar.hidden = false; bar.value = 0;
      say(de_en("Wird hochgeladen …", "Uploading …"));
      return files.reduce(function (chain, file, i) {
        return chain.then(function () {
          return upload(file, speakers[i] ? speakers[i].value : "", part, sent, total).then(function (used) { part = used + 1; });
        });
      }, Promise.resolve());
    }).then(function () {
      say(files.length === 1 ? de_en("Die Datei ist hochgeladen.", "The file has been uploaded.")
                             : de_en("{n} Dateien sind hochgeladen.", "{n} files have been uploaded.").replace("{n}", files.length));
      window.location.href = form.dataset.back;
    }).catch(function (error) {
      busy = false; button.disabled = false;
      say(error.message, true);
    });
  });
})();
