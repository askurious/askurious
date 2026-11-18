// The image of a feedback (feedback/_form.html in the header's dialog and on /feedback/new; app/feedback_shots.py;
// concept "Bildschirmfotos an Rückmeldungen", E1-E4). One image: taken of this tab ("Seite aufnehmen", the browser's
// own screen capture, one frame, the dialog hidden for it), chosen ("Bild wählen"), pasted (Ctrl+V while the dialog is
// open) or dropped onto the dialog. The dialog to mark it opens at once with "Abdecken" chosen; the marks go into the
// pixels before anything is sent (comment_shot.js, as for the images on comments). The finished file goes into the
// form's own file field, so the form is sent as without JavaScript. Without JavaScript that field is all there is.
//
// The dialog to mark is built here on first use, beside the feedback dialog (not inside its form), with the tools of
// participant/_comments.html: so its words are not on every page of the researchers.
(function () {
  var CS = window.CommentShot, editor = null;

  function icon(sprite, name) {
    return '<svg class="icon" width="16" height="16" aria-hidden="true"><use href="' + sprite + "#" + name + '"/></svg> ';
  }
  // the page may take a picture of itself (Permissions-Policy, app/__init__.py: not on interview pages)
  function allowed() {
    var policy = document.permissionsPolicy || document.featurePolicy;
    try { return !policy || policy.allowsFeature("display-capture"); } catch (e) { return true; }
  }
  function buildEditor(sprite) {
    if (editor) return editor;
    editor = document.createElement("dialog");
    editor.className = "shot-editor";
    editor.setAttribute("data-fb-shot-editor", "");
    editor.setAttribute("aria-labelledby", "fb-shot-editor-title");
    var tools = [["cover", "cover", de_en("Abdecken", "Cover")], ["rect", "frame", de_en("Rahmen", "Frame")],
                 ["arrow", "arrow-up-right", de_en("Pfeil", "Arrow")]];
    editor.innerHTML = '<h2 id="fb-shot-editor-title"></h2><div class="shot-tools" role="toolbar"></div>' +
      '<div class="shot-stage"><canvas data-shot-canvas></canvas></div><p class="muted small"></p>' +
      '<div class="confirm-actions"><button type="button" class="button button-secondary" data-shot-cancel></button>' +
      '<button type="button" class="button" data-shot-ok></button></div>';
    editor.querySelector("h2").textContent = de_en("Bild markieren", "Mark the image");
    var bar = editor.querySelector(".shot-tools");
    bar.setAttribute("aria-label", de_en("Werkzeuge", "Tools"));
    tools.forEach(function (t) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "button button-small button-secondary";
      b.dataset.tool = t[0];
      b.setAttribute("aria-pressed", t[0] === "cover" ? "true" : "false");
      b.innerHTML = icon(sprite, t[1]);
      b.appendChild(document.createTextNode(t[2]));
      bar.appendChild(b);
    });
    var undo = document.createElement("button");
    undo.type = "button";
    undo.className = "button button-small button-secondary";
    undo.setAttribute("data-shot-undo", "");
    undo.innerHTML = icon(sprite, "undo");
    undo.appendChild(document.createTextNode(de_en("Rückgängig", "Undo")));
    bar.appendChild(undo);
    editor.querySelector("canvas").setAttribute("aria-label", de_en("Bild der Seite zum Markieren", "Image of the page to mark"));
    editor.querySelector("p").textContent = de_en("Mit der Maus ziehen. „Abdecken“ schwärzt, was niemand sehen soll, etwa Angaben von Teilnehmenden.",
                                                  "Drag with the mouse. “Cover” blacks out what nobody should see, such as participant data.");
    editor.querySelector("[data-shot-cancel]").textContent = de_en("Abbrechen", "Cancel");
    editor.querySelector("[data-shot-ok]").textContent = de_en("Übernehmen", "Use image");
    document.body.appendChild(editor);
    return editor;
  }
  document.querySelectorAll("[data-feedback-form]").forEach(function (form) {
    var box = form.querySelector("[data-fb-shot]");
    if (!box || !CS || !window.DataTransfer || !window.HTMLDialogElement) return;  // the plain file field stays
    var sprite = box.dataset.icons || "";
    var field = box.querySelector("[data-fb-shot-file]"), plain = box.querySelector("[data-fb-shot-plain]");
    var note = form.querySelector("[data-fb-note]");
    // the buttons, the thumbnail and the note for an image, built here (the page carries only the plain field)
    function button(cls, name, text, label) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = cls;
      b.innerHTML = icon(sprite, name);
      if (text) b.appendChild(document.createTextNode(text));
      if (label) { b.setAttribute("aria-label", label); b.title = label; }
      return b;
    }
    var buttons = document.createElement("div");
    buttons.className = "feedback-shot-buttons";
    var capture = button("button button-small button-secondary", "camera", de_en("Seite aufnehmen", "Capture page"));
    capture.hidden = true;
    capture.setAttribute("data-fb-shot-capture", "");
    var pick = button("button button-small button-secondary", "image", de_en("Bild wählen", "Choose image"));
    pick.setAttribute("data-fb-shot-pick", "");
    var picker = document.createElement("input");
    picker.type = "file";
    picker.accept = "image/*";
    picker.hidden = true;
    buttons.append(capture, pick, picker);
    var thumb = document.createElement("div");
    thumb.className = "feedback-shot-thumb";
    thumb.hidden = true;
    thumb.setAttribute("data-fb-shot-thumb", "");
    var img = document.createElement("img"), info = document.createElement("span");
    img.alt = de_en("Angehängtes Bild", "Attached image");
    img.title = de_en("Markieren", "Mark");
    info.className = "muted small";
    img.setAttribute("data-fb-shot-img", "");
    var remove = button("icon-btn feedback-shot-remove", "x", "", de_en("Bild entfernen", "Remove image"));
    remove.setAttribute("data-fb-shot-remove", "");
    thumb.append(img, info, remove);
    var error = document.createElement("p");
    error.className = "feedback-shot-error";
    error.hidden = true;
    box.append(buttons, thumb, error);
    var noteShot = note.cloneNode(true);
    noteShot.hidden = true;
    noteShot.removeAttribute("data-fb-note");
    var code = noteShot.querySelector("code");
    noteShot.textContent = de_en("Die Seite, auf der Sie gerade sind, und das Bild werden mitgeschickt", "The page you are on and the image are sent along");
    if (code) noteShot.append(" (", code, ")");
    noteShot.append(". " + de_en("Bitte Angaben von Teilnehmenden abdecken.", "Please cover any participant data."));
    note.after(noteShot);
    // the dialog around the form (header), or none (the page /feedback/new): what is hidden for the picture
    var dialog = form.closest("dialog");
    var shot = null, url = null;
    plain.hidden = true;  // the field inside stays in the form: it carries the finished file

    function fail(text) { error.textContent = text; error.hidden = false; }
    function size(bytes) {
      var mb = bytes >= 1048576, n = mb ? bytes / 1048576 : Math.max(1, Math.round(bytes / 1024));
      return n.toLocaleString(document.documentElement.lang || "de", { maximumFractionDigits: 1 }) + (mb ? " MB" : " KB");
    }
    function set(s) {
      if (url) URL.revokeObjectURL(url);
      shot = s;
      url = s ? URL.createObjectURL(s.blob) : null;
      var files = new DataTransfer();
      if (s) files.items.add(new File([s.blob], s.blob.type === "image/png" ? "bild.png" : "bild.jpg", { type: s.blob.type }));
      field.files = files.files;
      thumb.hidden = !s;
      buttons.hidden = !!s;
      note.hidden = !!s;
      noteShot.hidden = !s;
      if (s) {
        img.src = url;
        info.textContent = s.w + " × " + s.h + " · " + size(s.blob.size);
      }
    }
    // marked first (cover chosen), then encoded; base and marks kept to mark it again
    function mark(base, marks) {
      return CS.edit(buildEditor(sprite), base, marks, "cover").then(function (done) {
        if (done === null) return;
        return CS.encode(CS.render(base, done)).then(function (blob) {
          set({ blob: blob, w: base.width, h: base.height, base: base, marks: done });
        });
      });
    }
    function take(f) {
      if (!f || !/^image\//.test(f.type)) { fail(de_en("Das ist kein Bild, das sich anhängen lässt.", "That is not an image that can be attached.")); return; }
      error.hidden = true;
      CS.load(f).then(function (base) { return mark(base, []); })
        .catch(function () { fail(de_en("Das ist kein Bild, das sich anhängen lässt.", "That is not an image that can be attached.")); });
    }
    if (CS.supported() && allowed()) {
      capture.hidden = false;
      capture.addEventListener("click", function () {
        error.hidden = true;
        CS.capture(dialog || box, "", CS.MAX_SIDE)
          .then(function (r) { return mark(r.base, []); })
          .catch(function (e) {
            // declined in the browser's question: nothing to say
            if (!e || (e.name !== "NotAllowedError" && e.name !== "AbortError")) fail(de_en("Die Seite konnte nicht aufgenommen werden.", "The page could not be captured."));
          });
      });
    }
    pick.addEventListener("click", function () { picker.click(); });
    picker.addEventListener("change", function () { take(picker.files[0]); picker.value = ""; });
    img.addEventListener("click", function () { if (shot) mark(shot.base, shot.marks); });
    remove.addEventListener("click", function () {
      set(null);
      pick.focus();
    });
    // pasted: only while this form can be seen (the dialog open, or the page of its own), and only images
    document.addEventListener("paste", function (e) {
      if (dialog ? !dialog.open : document.querySelector("[data-feedback-dialog][open]")) return;
      if (editor && editor.open) return;
      var files = e.clipboardData ? e.clipboardData.files : null;
      if (!files || !files.length || !/^image\//.test(files[0].type)) return;
      e.preventDefault();
      take(files[0]);
    });
    var drop = dialog || form;
    drop.addEventListener("dragover", function (e) {
      if (!e.dataTransfer || Array.prototype.indexOf.call(e.dataTransfer.types || [], "Files") < 0) return;
      e.preventDefault();
      drop.classList.add("is-drop");
    });
    drop.addEventListener("dragleave", function (e) { if (e.target === drop) drop.classList.remove("is-drop"); });
    drop.addEventListener("drop", function (e) {
      if (!e.dataTransfer || !e.dataTransfer.files.length) return;
      e.preventDefault();
      drop.classList.remove("is-drop");
      take(e.dataTransfer.files[0]);
    });
  });
})();
