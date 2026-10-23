// Comments in the preview: send, list and delete own comments without reloading the page (answers stay). The texts
// come in the language of the page (participant/_comments.html, data-t).
// An image of the page may go along: taken of the page itself (comment_shot.js), chosen, pasted (Ctrl+V) or dropped
// onto the box, then marked in a dialog (frame, arrow, cover). It is made smaller here (at most MAX_SIDE pixels, PNG
// or, if that is large, JPEG) and only sent with the comment; the server reads it again and writes it anew without
// private metadata (app/comment_shots.py). Attribution entered separately stays with the edited image;
// the original file is never uploaded alongside a redacted image. Every comment also carries short labels of where
// it was written: device, system, browser, window size, language, light or dark (env_* fields).
(function () {
  var MAX_SIDE = 2400, PNG_LIMIT = 1500000;

  // --- where the comment is written ------------------------------------------------------------------------------
  function environment(box) {
    var ua = navigator.userAgent, out = {}, m;
    var touchMac = /Macintosh/.test(ua) && navigator.maxTouchPoints > 1;  // an iPad that says it is a Mac
    if (/iPhone|iPod/.test(ua)) out.os = "iOS";
    else if (/iPad/.test(ua) || touchMac) out.os = "iPadOS";
    else if (/Android/.test(ua)) out.os = "Android";
    else if (/CrOS/.test(ua)) out.os = "ChromeOS";
    else if (/Windows/.test(ua)) out.os = "Windows";
    else if (/Mac OS X|Macintosh/.test(ua)) out.os = "macOS";
    else if (/Linux/.test(ua)) out.os = "Linux";
    var browsers = [["Samsung Internet", /SamsungBrowser\/(\d+)/], ["Edge", /Edg(?:A|iOS)?\/(\d+)/], ["Opera", /OPR\/(\d+)/],
                    ["Firefox", /(?:Firefox|FxiOS)\/(\d+)/], ["Chrome", /(?:CriOS|Chrome)\/(\d+)/], ["Safari", /Version\/(\d+)[\d.]* (?:Mobile\/\S+ )?Safari/]];
    for (var i = 0; i < browsers.length; i++) {
      m = ua.match(browsers[i][1]);
      if (m) { out.browser = browsers[i][0]; out.version = m[1]; break; }
    }
    var frame = box.classList.contains("is-stage") ? document.querySelector("[data-device-frame]") : null;
    var doc = document;
    if (frame) {
      out.device = "Mobilansicht";
      out.size = frame.clientWidth + "x" + frame.clientHeight;
      try { doc = frame.contentDocument || document; } catch (e) { doc = document; }
    } else {
      out.device = out.os === "iOS" || (out.os === "Android" && /Mobile/.test(ua)) ? "Smartphone"
        : out.os === "iPadOS" || out.os === "Android" ? "Tablet" : "Computer";
      out.size = window.innerWidth + "x" + window.innerHeight;
    }
    out.lang = (doc.documentElement.lang || "").slice(0, 10);
    // light or dark as the page shows it (the survey's theme, the preview's switch or the system's setting)
    var bg = doc.body ? getComputedStyle(doc.body).backgroundColor.match(/\d+(\.\d+)?/g) : null;
    if (bg && bg.length >= 3 && (bg.length < 4 || Number(bg[3]) > 0)) {
      out.scheme = 0.2126 * bg[0] + 0.7152 * bg[1] + 0.0722 * bg[2] < 128 ? "dunkel" : "hell";
    }
    return out;
  }

  // --- the image: made smaller in the browser before it is sent ---------------------------------------------------
  function load(file) {
    return new Promise(function (resolve, reject) {
      var url = URL.createObjectURL(file), img = new Image();
      img.onload = function () {
        var scale = Math.min(1, MAX_SIDE / Math.max(img.naturalWidth, img.naturalHeight));
        var canvas = document.createElement("canvas");
        canvas.width = Math.max(1, Math.round(img.naturalWidth * scale));
        canvas.height = Math.max(1, Math.round(img.naturalHeight * scale));
        var ctx = canvas.getContext("2d");
        ctx.fillStyle = "#fff";
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
        URL.revokeObjectURL(url);
        resolve(canvas);
      };
      img.onerror = function () { URL.revokeObjectURL(url); reject(new Error("decode")); };
      img.src = url;
    });
  }
  function encode(canvas) {
    return new Promise(function (resolve, reject) {
      canvas.toBlob(function (png) {
        if (png && png.size <= PNG_LIMIT) { resolve(png); return; }
        canvas.toBlob(function (jpg) { if (jpg) resolve(jpg); else reject(new Error("encode")); }, "image/jpeg", 0.88);
      }, "image/png");
    });
  }

  document.querySelectorAll("[data-comments]").forEach(function (box) {
    var T = {};
    try { T = JSON.parse(box.dataset.t || "{}"); } catch (e) { T = {}; }
    var url = box.dataset.url, form = box.querySelector("[data-comment-form]"), list = box.querySelector("[data-comment-list]");
    var error = box.querySelector("[data-comment-error]"), toggle = box.querySelector("[data-comments-toggle]");
    var token = form.querySelector('input[name="csrf_token"]').value;
    var shotBox = box.querySelector("[data-shot]"), file = box.querySelector("[data-shot-file]");
    var thumb = box.querySelector("[data-shot-thumb]"), buttons = box.querySelector("[data-shot-buttons]");
    var shot = null, shotUrl = null, editor = box.querySelector("[data-shot-editor]"), CS = window.CommentShot;
    var rightsPanel = box.querySelector("[data-shot-rights-panel]"), rightsFields = box.querySelector("[data-shot-rights]");
    var capture = box.querySelector("[data-shot-capture]");
    function open() {
      if (!toggle || box.classList.contains("is-open")) return;
      box.classList.add("is-open");
      toggle.setAttribute("aria-expanded", "true");
    }
    // the button in the preview bar (narrow screens, V51): opens this panel, or shows it where it stands beside the phone
    document.querySelectorAll("[data-comments-bar]").forEach(function (bar) {
      bar.addEventListener("click", function () {
        if (toggle) toggle.click();
        else box.scrollIntoView({ block: "start" });
      });
    });
    if (toggle) toggle.addEventListener("click", function () {
      var open = box.classList.toggle("is-open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      if (open) form.querySelector("textarea").focus();
    });
    function fail(text) { error.textContent = text; error.hidden = false; }

    // a phone has no Ctrl+V; there the hint says to take a screenshot first
    var paste = box.querySelector("[data-shot-paste]");
    if (window.matchMedia && window.matchMedia("(pointer: coarse)").matches) {
      paste.hidden = true;
      box.querySelector("[data-shot-phone]").hidden = false;
    } else if (/Mac/.test(navigator.platform || navigator.userAgent) && paste.dataset.mac) {
      paste.textContent = paste.dataset.mac;  // a Mac pastes with ⌘V, not Ctrl+V (V42)
    }
    function setShot(s) {
      // A different picture starts with its own attribution. Reopening its marking dialog keeps the fields.
      if (rightsFields) {
        if (!s || !shot || s.base !== shot.base) {
          rightsFields.querySelectorAll("input").forEach(function (field) { field.value = ""; });
        }
        rightsFields.disabled = !s;
      }
      if (rightsPanel) rightsPanel.hidden = !s;
      if (shotUrl) URL.revokeObjectURL(shotUrl);
      shot = s;
      shotUrl = s ? URL.createObjectURL(s.blob) : null;
      thumb.hidden = !s;
      buttons.hidden = !!s;
      box.querySelector("[data-shot-privacy]").hidden = !s;
      if (s) {
        box.querySelector("[data-shot-img]").src = shotUrl;
        box.querySelector("[data-shot-info]").textContent = s.w + " × " + s.h;
      }
    }
    // marked in the dialog (if the browser has one), then encoded; base and marks are kept to mark it again
    function mark(base, marks) {
      var edited = CS && editor && editor.showModal ? CS.edit(editor, base, marks) : Promise.resolve(marks);
      return edited.then(function (done) {
        if (done === null) return;
        return encode(CS ? CS.render(base, done) : base).then(function (blob) {
          setShot({ blob: blob, w: base.width, h: base.height, base: base, marks: done });
        });
      });
    }
    function take(f) {
      if (!f || !/^image\//.test(f.type)) { fail(T.no_image || "Das ist kein Bild, das sich anhängen lässt."); return; }
      error.hidden = true;
      open();
      load(f).then(function (base) { return mark(base, []); })
        .catch(function () { fail(T.no_image || "Das ist kein Bild, das sich anhängen lässt."); });
    }
    if (capture && CS && CS.supported()) {
      capture.hidden = false;
      capture.addEventListener("click", function () {
        error.hidden = true;
        var item = form.querySelector('select[name="item_id"]');
        CS.capture(box, item ? item.value : "", MAX_SIDE)
          .then(function (r) { return mark(r.base, r.marks); })
          .catch(function (e) {
            // declined in the browser's question: nothing to say
            if (!e || (e.name !== "NotAllowedError" && e.name !== "AbortError")) fail(T.capture_failed || "Die Seite konnte nicht aufgenommen werden.");
          });
      });
    }
    box.querySelector("[data-shot-img]").addEventListener("click", function () {
      if (shot && shot.base) mark(shot.base, shot.marks);
    });
    box.querySelector("[data-shot-pick]").addEventListener("click", function () { file.click(); });
    file.addEventListener("change", function () { take(file.files[0]); file.value = ""; });
    box.querySelector("[data-shot-remove]").addEventListener("click", function () {
      setShot(null);
      box.querySelector("[data-shot-pick]").focus();
    });
    // pasted anywhere on the page: only images (text pasted into a field of the questionnaire stays where it is)
    document.addEventListener("paste", function (e) {
      var files = e.clipboardData ? e.clipboardData.files : null;
      if (!files || !files.length || !/^image\//.test(files[0].type)) return;
      e.preventDefault();
      take(files[0]);
    });
    shotBox.addEventListener("dragover", function (e) { e.preventDefault(); shotBox.classList.add("is-drop"); });
    shotBox.addEventListener("dragleave", function () { shotBox.classList.remove("is-drop"); });
    shotBox.addEventListener("drop", function (e) {
      e.preventDefault();
      shotBox.classList.remove("is-drop");
      if (e.dataTransfer && e.dataTransfer.files.length) take(e.dataTransfer.files[0]);
    });

    function row(c) {
      var li = document.createElement("li");
      li.dataset.commentId = c.id;
      li.innerHTML = '<span class="p-comments-target"></span><span class="p-comments-text"></span>' +
        '<span class="p-comments-meta"><span></span> · <button type="button" class="p-comments-del" data-comment-delete></button></span>';
      li.querySelector(".p-comments-target").textContent = c.target;
      li.querySelector(".p-comments-text").textContent = c.text;
      li.querySelector("[data-comment-delete]").textContent = T.delete || "löschen";
      li.querySelector(".p-comments-meta span").textContent = c.author + " · " + c.created;
      if (c.shot) {
        var a = document.createElement("a"), img = document.createElement("img");
        a.className = "p-comments-img";
        a.href = url + "/" + c.id + "/shot";
        a.target = "_blank";
        a.rel = "noopener";
        img.src = a.href;
        img.alt = T.image_alt || "";
        a.appendChild(img);
        li.insertBefore(a, li.querySelector(".p-comments-meta"));
      }
      return li;
    }
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      error.hidden = true;
      var button = form.querySelector(".p-comments-send");
      button.disabled = true;
      var body = new FormData(form), env = environment(box);
      Object.keys(env).forEach(function (k) { body.append("env_" + k, env[k]); });
      if (shot) body.append("shot", shot.blob, shot.blob.type === "image/png" ? "bild.png" : "bild.jpg");
      fetch(url, { method: "POST", body: body, headers: { "X-Requested-With": "fetch" } })
        .then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) { return { ok: r.ok, d: d }; }); })
        .then(function (res) {
          if (!res.ok) { fail(res.d.error || T.failed || "Konnte nicht gespeichert werden."); return; }
          list.insertBefore(row(res.d), list.firstChild);
          form.querySelector("textarea").value = "";
          setShot(null);
        })
        .catch(function () { fail(T.offline || "Keine Verbindung. Bitte erneut versuchen."); })
        .then(function () { button.disabled = false; });
    });
    list.addEventListener("click", function (e) {
      var del = e.target.closest("[data-comment-delete]");
      if (!del) return;
      var li = del.closest("li"), body = new FormData();
      body.append("csrf_token", token);
      fetch(url + "/" + li.dataset.commentId + "/delete", { method: "POST", body: body, headers: { "X-Requested-With": "fetch" } })
        .then(function (r) { if (!r.ok) throw new Error(r.status); li.remove(); error.hidden = true; })
        .catch(function () { fail(T.not_deleted || "Konnte nicht gelöscht werden. Bitte erneut versuchen."); });
    });
  });
})();
