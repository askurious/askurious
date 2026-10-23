// AInstein (app/ainstein.py): the conversation and the canvas boxes without reloading the page. Without this script
// every form of the page works as an ordinary post.
(function () {
  "use strict";

  function csrf() { return (document.querySelector('meta[name="csrf-token"]') || {}).content || ""; }

  function post(url, data) {
    // aiFetch (app.js): a limit of the AI (429) opens the dialog with "Erneut versuchen" (app/ai_fetch.py)
    return (window.aiFetch || fetch)(url, { method: "POST", body: data, credentials: "same-origin",
                        headers: { "X-CSRFToken": csrf(), "X-Requested-With": "fetch" } })
      .then(function (r) {
        return r.json().catch(function () { return { error: de_en("Die Antwort des Servers war nicht lesbar.", "The server’s answer could not be read.") }; })
          .then(function (body) { if (!r.ok && !body.error) body.error = de_en("Das hat nicht geklappt (" + r.status + ").", "That did not work (" + r.status + ")."); return body; });
      }, function () { return { error: de_en("Keine Verbindung zum Server. Bitte erneut versuchen.", "No connection to the server. Please try again.") }; });
  }

  // forms that wait for the AI: the button says what happens and glints (app.js, .ai-busy), the part of the page it
  // works on shimmers in the colour of the AI (data-ainstein-area); not for a question answered with "no"
  document.querySelectorAll("form[data-ainstein-busy]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      var button = e.submitter || form.querySelector("button");
      setTimeout(function () {
        if (e.defaultPrevented && !e.aiCall) return;  // aiCall: sent by app.js (aiFetch), not declined
        if (button) {
          var label = button.querySelector(".ainstein-busy-label") || document.createElement("span");
          label.className = "ainstein-busy-label";
          label.textContent = form.getAttribute("data-ainstein-busy");
          Array.prototype.slice.call(button.childNodes).forEach(function (n) { if (n.nodeType === 3) n.remove(); });
          button.appendChild(label);
        }
        var area = form.getAttribute("data-ainstein-area") && document.querySelector(form.getAttribute("data-ainstein-area"));
        if (area) { area.classList.add("is-ai-working"); area.setAttribute("aria-busy", "true"); }
      }, 0);
    });
  });
  window.addEventListener("pageshow", function (e) {  // back in the browser history: idle again
    if (!e.persisted) return;
    document.querySelectorAll(".is-ai-working").forEach(function (a) { a.classList.remove("is-ai-working"); a.removeAttribute("aria-busy"); });
  });

  // step 2 the first time: AInstein fills the page as it opens (the panels shimmer meanwhile), then it loads anew
  var auto = document.querySelector("[data-auto-derive]");
  if (auto) {
    var areas = document.querySelectorAll("#plan, #konstrukte");
    areas.forEach(function (a) { a.classList.add("is-ai-working"); a.setAttribute("aria-busy", "true"); });
    post(auto.getAttribute("data-auto-derive"), new FormData()).then(function (body) {
      if (!body.error) { window.location.reload(); return; }
      areas.forEach(function (a) { a.classList.remove("is-ai-working"); a.removeAttribute("aria-busy"); });
      var label = document.querySelector("[data-auto-label]");
      if (label) {
        label.textContent = body.error;
        if (body.detail) label.title = body.detail;  // the technical reason on hover
      }
    });
  }

  // the idea of a new project: Ctrl+Enter (Cmd+Enter on a Mac) begins with AInstein, as the button does
  document.querySelectorAll("[data-submit-keys]").forEach(function (area) {
    area.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && area.form) { e.preventDefault(); area.form.requestSubmit(); }
    });
  });

  // the idea grows a tree (ainstein/index.html): the words counted, the parts of the tree from their number of
  // words on, and a friendly word on the length; over 100 words are ideal, more is fine
  var grow = document.querySelector("[data-grow]"), growField = document.querySelector("[data-grow-field]");
  if (grow && growField) {
    var parts = grow.querySelectorAll(".t-part"), growCount = grow.querySelector("[data-grow-count]"),
        growHint = grow.querySelector("[data-grow-hint]"), tree = grow.querySelector("[data-grow-tree]");
    // the five things a good idea names, guessed from words (German and English); a hint, not a check: AInstein
    // asks about what is still open in its first answer (KICKOFF)
    var ASPECTS = {
      who: /(menschen|personen|nutzer|nutzende|beschäftigt|mitarbeit|patient|ältere|senior|kinder|jugendlich|studierend|schüler|eltern|bürger|bevölkerung|kund|lehrkräft|pflegende|angehörige|expert|fachleute|laien|zielgruppe|frauen|männer|people|users|employees|patients|students|older adults|experts|public|customers|consumers)/i,
      aim: /(ziel(?!gruppe)|möchte|möchten|wollen|herausfinden|untersuch|verstehen|prüfen|testen|klären|erklären|beschreiben|vergleichen|forschungsfrage|hypothese|ob |\baim|\bgoal|investigat|understand|whether|examine|explore)/i,
      method: /(befragung|umfrage|fragebogen|experiment|interview|conjoint|cbc|maxdiff|vignette|skala|likert|längsschnitt|vorher|nachher|messen|messung|\biat\b|delphi|a\/b|bedingung|survey|questionnaire|scale|measure|longitudinal|pre-post)/i,
      sample: /(stichprobe|teilnehmend|teilnehmer|probanden|panel|prolific|rekrutier|\d+\s*(personen|leute|menschen|fälle|teilnehm|befragte)|n\s*=\s*\d|sample|participants|respondents|recruit)/i
    };
    var aspectsUpdate = function (words) {
      grow.querySelectorAll("[data-aspect]").forEach(function (li) {
        var key = li.getAttribute("data-aspect");
        var on = key === "topic" ? words >= 10 : ASPECTS[key].test(growField.value);
        li.classList.toggle("is-on", on);
        li.querySelector("[data-aspect-state]").textContent = on ? de_en(": genannt", ": mentioned") : de_en(": noch offen", ": still open");
      });
    };
    var growUpdate = function () {
      var words = (growField.value.match(/[^\s]+/g) || []).length;
      parts.forEach(function (p) {
        var from = +p.getAttribute("data-from"), until = p.getAttribute("data-until");
        p.classList.toggle("is-on", words >= from && (until === null || words <= +until));
      });
      if (tree) {  // every word a little taller: from 55 % with no word to full size at 150 words
        tree.style.transform = "scale(" + (0.55 + 0.45 * Math.min(words, 150) / 150).toFixed(3) + ")";
      }
      var least = +growField.getAttribute("data-min-words") || 0;  // below it the form is not sent (also on the server)
      growField.setCustomValidity(words < least ? de_en("Bitte mindestens " + least + " Wörter, ideal sind über 100.",
        "Please write at least " + least + " words; over 100 are ideal.") : "");
      growCount.textContent = words + (words === 1 ? de_en(" Wort", " word") : de_en(" Wörter", " words"));
      growHint.textContent = words === 0 ? de_en("ideal sind über 100", "over 100 are ideal") :
        words < least ? de_en("mindestens " + least + ", ideal sind über 100", "at least " + least + ", over 100 are ideal") :
        words < 50 ? de_en("erzählen Sie ruhig mehr", "feel free to tell more") :
        words < 100 ? de_en("schon gut, gern noch etwas mehr", "good already, a little more is welcome") :
        words <= 150 ? de_en("genau richtig", "just right") : de_en("sehr gut, mehr geht auch", "very good, more is fine too");
      aspectsUpdate(words);
      grow.setAttribute("data-stage", words < 50 ? "start" : words < 100 ? "near" : "done");
    };
    growField.addEventListener("input", growUpdate);
    growUpdate();
  }

  var root = document.querySelector("[data-ainstein]");
  if (!root) return;
  var messages = root.querySelector("[data-messages]");
  var sendForm = root.querySelector("[data-send]");
  var field = root.querySelector("[data-message]");

  function scrollDown() { messages.scrollTop = messages.scrollHeight; }
  scrollDown();
  window.addEventListener("load", scrollDown);  // after fonts and layout: the newest message in view

  function bubble(kind, text) {
    var empty = messages.querySelector("[data-empty]");
    if (empty) empty.remove();
    var div = document.createElement("div");
    div.className = "ainstein-msg ainstein-msg-" + kind;
    if (text !== undefined) { var p = document.createElement("p"); p.textContent = text; div.appendChild(p); }
    messages.appendChild(div);
    scrollDown();
    return div;
  }

  // the counter in the head of the chat: the server says how many messages the conversation has now
  var counter = root.querySelector("[data-count]");
  function count(n) {
    if (counter && typeof n === "number") counter.textContent = n + (n === 1 ? de_en(" Nachricht", " message") : de_en(" Nachrichten", " messages"));
  }

  function box(key) { return root.querySelector('[data-box="' + key + '"]'); }

  // AInstein writes into the canvas at once: the box gets the new text and lights up for a moment (app.css .is-updated)
  function updateBox(key, text) {
    var b = box(key);
    if (!b) return;
    var target = b.querySelector("[data-box-text]");  // as ainstein.box_html: "(offen:" in bold, the rest plain text
    target.textContent = "";
    String(text || "").split(/(?<=\()((?:offen|open)\s*:)/i).forEach(function (part, n) {
      if (n % 2) { var s = document.createElement("strong"); s.textContent = part; target.appendChild(s); }
      else if (part) target.appendChild(document.createTextNode(part));
    });
    b.classList.toggle("is-filled", !!(text && text.trim()));  // the frame stays (app.css)
    b.classList.remove("is-updated");
    void b.offsetWidth;  // start the animation anew when the same box changes twice in a row
    b.classList.add("is-updated");
  }

  function focusBox(key) {
    root.querySelectorAll(".ainstein-box.is-focus").forEach(function (b) { b.classList.remove("is-focus"); });
    var b = key && box(key);
    if (b) b.classList.add("is-focus");
  }

  // two quiet sounds, made in the browser (Web Audio: no file, no request): a short rising blip when a message goes
  // out, a soft two-note chime when AInstein answers. The speaker in the head of the chat switches them off; the
  // browser remembers it (a convenience only: without storage the sounds are simply on).
  var soundButton = root.querySelector("[data-sound]"), audio = null;
  function soundOn() { try { return localStorage.getItem("ainstein-sound") !== "off"; } catch (err) { return true; } }
  function showSound() {
    if (!soundButton) return;
    var on = soundOn();
    soundButton.setAttribute("aria-pressed", on ? "true" : "false");
    soundButton.title = on ? de_en("Töne aus", "Sounds off") : de_en("Töne an", "Sounds on");
    soundButton.querySelector("[data-sound-on]").hidden = !on;
    soundButton.querySelector("[data-sound-off]").hidden = on;
  }
  if (soundButton) {
    soundButton.addEventListener("click", function () {
      try { localStorage.setItem("ainstein-sound", soundOn() ? "off" : "on"); } catch (err) { /* stays on */ }
      showSound();
    });
    showSound();
  }
  function play(notes, type) {
    if (!soundOn() || !(window.AudioContext || window.webkitAudioContext)) return;
    try {
      audio = audio || new (window.AudioContext || window.webkitAudioContext)();  // first created on a click (send)
      if (audio.state === "suspended") audio.resume();
      var now = audio.currentTime;
      notes.forEach(function (n) {  // [frequency, start, length] in Hz and seconds
        var osc = audio.createOscillator(), gain = audio.createGain(), t = now + n[1];
        osc.type = type;
        osc.frequency.setValueAtTime(n[0], t);
        gain.gain.setValueAtTime(0.0001, t);
        gain.gain.exponentialRampToValueAtTime(0.05, t + 0.015);     // soft attack, quiet
        gain.gain.exponentialRampToValueAtTime(0.0001, t + n[2]);    // and gone
        osc.connect(gain).connect(audio.destination);
        osc.start(t);
        osc.stop(t + n[2] + 0.02);
      });
    } catch (err) { /* no sound, nothing else changes */ }
  }
  var SENT = [[660, 0, 0.08], [880, 0.06, 0.1]], RECEIVED = [[784, 0, 0.18], [1175, 0.11, 0.32]];

  function thinking(div) {
    div.innerHTML = '<span class="ainstein-thinking"><span class="ainstein-thinking-text">' + de_en("AInstein denkt nach", "AInstein is thinking") + '</span>' +
      '<span class="ainstein-dots" aria-hidden="true"><i></i><i></i><i></i></span></span>';
  }

  // the role "Ansehen" reads the chat only: the page has no form to send (G5); all below is about sending
  if (!sendForm || !field) return;
  var sending = false;  // one message at a time: Ctrl+Enter (requestSubmit) does not ask the disabled button (code review F13)

  sendForm.addEventListener("submit", function (e) {
    e.preventDefault();
    var text = field.value.trim();
    if (!text || sending) return;
    sending = true;
    var data = new FormData(sendForm);
    var button = sendForm.querySelector("button");
    bubble("user", text);
    play(SENT, "sine");
    field.value = "";
    button.disabled = true;
    var pending = bubble("ai");
    thinking(pending);
    pending.classList.add("is-pending");
    scrollDown();
    post(root.getAttribute("data-chat-url"), data).then(function (body) {
      sending = false;
      button.disabled = false;
      pending.classList.remove("is-pending");
      count(body.messages);
      if (body.error) {
        pending.classList.add("is-error");
        pending.textContent = body.error;
        if (body.detail) {  // the technical reason, on hover (AInstein is experimental)
          var more = document.createElement("abbr");
          more.className = "ai-detail";
          more.tabIndex = 0;
          more.title = body.detail;
          more.textContent = "Details";
          pending.appendChild(document.createTextNode(" "));
          pending.appendChild(more);
        }
        if (!field.value) field.value = text;  // to send again
        return;
      }
      pending.innerHTML = body.reply;  // HTML built and escaped by the server (ainstein.reply_html)
      play(RECEIVED, "sine");
      Object.keys(body.updates || {}).forEach(function (key) { updateBox(key, body.updates[key]); });
      focusBox(body.focus);
      scrollDown();
      field.focus();
    });
  });

  field.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); sendForm.requestSubmit(); }
  });

})();
