// Slides from a PDF file into a live vote (app/live/, for everyone since 2026-10-17; concept
// konzept-live-feedback-praesentation-2026-10-12.md, variant C): the PDF is read and drawn in this browser with pdf.js
// (app/static/vendor/pdfjs, Mozilla, Apache 2.0), page by page, and only the pictures go to the server, one request each:
// the server never reads a PDF. A slide show from PowerPoint is saved as PDF first (File > Export > PDF).
(function () {
  var box = document.querySelector("[data-slides-pdf]");
  if (!box) return;
  var UI_LANG = document.documentElement.lang;
  function de_en(de, en) { return UI_LANG === "en" ? en : de; }
  var input = box.querySelector('input[type="file"]'), go = box.querySelector("[data-slides-go]");
  var bar = box.querySelector("progress"), status = box.querySelector("[data-slides-status]");
  var replace = box.querySelector('input[name="replace"]');
  var WIDTH = 2560, MAX = parseInt(box.dataset.max, 10) || 150;
  function say(text, error) { status.textContent = text; status.classList.toggle("is-error", !!error); status.hidden = !text; }
  function post(url, body) {
    body.append("csrf_token", box.dataset.csrf);
    return fetch(url, { method: "POST", body: body, credentials: "same-origin" }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok || !d.ok) throw new Error(d.error || de_en("Fehler {s}", "Error {s}").replace("{s}", r.status));
        return d;
      });
    });
  }
  function picture(page) {  // a page as a picture: 2560 px on its longer side (sharp on a large projector), WebP where the browser writes it, else PNG
    var first = page.getViewport({ scale: 1 });
    var viewport = page.getViewport({ scale: WIDTH / Math.max(first.width, first.height) });
    var canvas = document.createElement("canvas");
    canvas.width = Math.round(viewport.width);
    canvas.height = Math.round(viewport.height);
    var context = canvas.getContext("2d");
    context.fillStyle = "#ffffff";  // pages without a background stay white, not transparent
    context.fillRect(0, 0, canvas.width, canvas.height);
    return page.render({ canvasContext: context, viewport: viewport }).promise.then(function () {
      return new Promise(function (done) { canvas.toBlob(done, "image/webp", 0.85); });
    });
  }
  function run(file) {
    go.disabled = input.disabled = true;
    say(de_en("PDF wird gelesen …", "Reading the PDF …"));
    var lib = null, total = 0;
    return import(box.dataset.lib).then(function (pdfjs) {
      lib = pdfjs;
      lib.GlobalWorkerOptions.workerSrc = box.dataset.worker;
      return file.arrayBuffer();
    }).then(function (data) {
      return lib.getDocument({ data: data, isEvalSupported: false }).promise;
    }).then(function (pdf) {
      total = pdf.numPages;
      if (total > MAX) throw new Error(de_en("Die Datei hat {n} Seiten; höchstens {m} gehen auf einmal.", "The file has {n} pages; at most {m} at a time.").replace("{n}", total).replace("{m}", MAX));
      var first = replace && replace.checked ? post(box.dataset.clear, new FormData()) : Promise.resolve();
      bar.max = total; bar.value = 0; bar.hidden = false;
      return first.then(function () {
        var chain = Promise.resolve();
        for (var n = 1; n <= total; n++) (function (n) {
          chain = chain.then(function () { return pdf.getPage(n); }).then(picture).then(function (blob) {
            if (!blob) throw new Error(de_en("Seite {n} ließ sich nicht als Bild speichern.", "Page {n} could not be saved as a picture.").replace("{n}", n));
            var body = new FormData();
            body.append("file", blob, "folie-" + n + (blob.type === "image/webp" ? ".webp" : ".png"));
            body.append("n", n);
            body.append("name", file.name.slice(0, 120));
            return post(box.dataset.page, body);
          }).then(function () {
            bar.value = n;
            say(de_en("Folie {n} von {m} übernommen …", "Slide {n} of {m} added …").replace("{n}", n).replace("{m}", total));
          });
        })(n);
        return chain;
      });
    }).then(function () {
      say(de_en("{n} Folien übernommen.", "{n} slides added.").replace("{n}", total));
      location.href = box.dataset.done;
    }).catch(function (e) {
      var text = e && e.name === "PasswordException" ? de_en("Die PDF-Datei ist mit einem Passwort geschützt. Bitte ohne Passwort speichern.", "The PDF file is protected by a password. Please save it without one.")
        : e && e.name === "InvalidPDFException" ? de_en("Das ist keine lesbare PDF-Datei.", "This is not a readable PDF file.")
        : (e && e.message) || de_en("Das hat nicht geklappt.", "That did not work.");
      say(text + " " + de_en("Bereits übernommene Folien bleiben in der Liste.", "Slides already added stay in the list."), true);
      go.disabled = input.disabled = false;
    });
  }
  input.addEventListener("change", function () { go.disabled = !input.files.length; say(""); });
  go.addEventListener("click", function () { if (input.files.length) run(input.files[0]); });
})();
