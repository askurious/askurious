// Live votes (app/live/): the page of a participant follows the question the presenter shows; the screen shows
// that question and its results. Both ask the server every few seconds (the hosting has no push).
(function () {
  // the texts the screen and the slides show the audience, in the language of the vote (data-t, app/live/)
  var T = {};
  try { T = JSON.parse((document.querySelector("[data-t]") || { dataset: {} }).dataset.t || "{}"); } catch (e) { T = {}; }
  function t(key) { return T[key] || ""; }
  // the presenter's controls in the language of the presenter's pages (app/lang.py), not the vote's: on the screen
  // and the slides <html lang> is the vote's, so they name the presenter's in data-ui-lang; elsewhere <html lang>
  var UI_LANG = (document.querySelector("[data-ui-lang]") || { dataset: {} }).dataset.uiLang || document.documentElement.lang;
  function de_en(de, en) { return UI_LANG === "en" ? en : de; }
  // the brand mark of the domain the page came from (data-brand on <html>, app/brand.py)
  var BRAND_MARK = (document.documentElement.getAttribute("data-brand") || "Askurious") + ".de";
  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }

  // --- participant ---------------------------------------------------------------------------------------------
  // The question comes drawn with the page (app/live/, join): the scripts of sliders, ranking and the 2D field
  // (participant.js) bind on page load, so a new question loads the page again. Notes (waiting, thanks, closed,
  // "look at the screen") are drawn here.
  var stage = document.querySelector("[data-live]");
  if (stage) {
    var shown = stage.dataset.key || null, every = (parseInt(stage.dataset.poll, 10) || 2) * 1000, sending = false;
    var hold = 0;  // until then a note stays on the phone before the next question comes (a late answer)
    // the device id (audit 2026-10-05, T-17): sent with every answer, so a phone whose cookie got lost (or whose first
    // answer never came back) stays one participation; kept in localStorage, else the one of the session
    // The id is issued and signed by the server (audit 2026-10-18, BIZ-02): one made here would not count, so an id
    // in localStorage is only kept in the form of a signed one (46 characters); else the page's id replaces it.
    var device = "", DEVICE = /^[A-Za-z0-9_-]{46}$/;
    try { device = localStorage.getItem("askurious-live-device") || localStorage.getItem("queryous-live-device") || ""; } catch (e) { device = ""; }
    if (!DEVICE.test(device)) {
      device = stage.dataset.device || "";
      try { if (DEVICE.test(device)) localStorage.setItem("askurious-live-device", device); } catch (e) { /* the session's id stays */ }
    }
    function note(text) { stage.innerHTML = '<p class="live-wait">' + esc(text) + "</p>"; }
    // said for a few seconds, then the phone follows the screen again
    function held(text) { note(text); shown = "held"; hold = Date.now() + 4000; }
    function texts(key) {
      if (key === "done") return stage.dataset.done;  // flow In Blöcken: the block is answered, the next one may follow
      if (key.indexOf("closed-") === 0) return stage.dataset.closed;
      if (key.indexOf("full-") === 0) return stage.dataset.full;  // a word cloud: the answers allowed per device are sent
      if (key.indexOf("look-") === 0) return stage.dataset.look;
      return stage.dataset.wait;
    }
    // the page anew, without "?aendern=1"; "weiter": the phone's own load keeps its place in the block (a visit
    // without it starts the running block from its first page again, live.py _rejoin)
    function fresh() { location.replace(location.pathname + "?weiter=1"); }
    if (/[?&]weiter=1/.test(location.search)) history.replaceState(null, "", location.pathname);  // a reload is a rejoin
    // answered: thanks, and a button to change the answer while voting on the question is open (decision L7)
    function answered() {
      stage.innerHTML = '<p class="live-wait">' + esc(stage.dataset.thanks) + '</p><p><button type="button" class="p-button p-button-secondary" data-change>' +
        esc(stage.dataset.change) + "</button></p>";
      stage.querySelector("[data-change]").addEventListener("click", function () { location.replace(location.pathname + "?aendern=1"); });
    }
    function draw(key) {
      shown = key;
      if (key.indexOf("answered-") === 0) { answered(); return; }
      if (key === "end" || key === "ended") {  // the closing text of the live vote (Einstellungen); "ended": the vote was ended
        var end = document.querySelector("[data-live-end]");
        if (end) stage.innerHTML = end.innerHTML; else note(stage.dataset.thanks);
        return;
      }
      note(texts(key));
    }
    var form = stage.querySelector("[data-live-form]");
    // "Antworten mit einem Tippen senden": once every question of the page has its choice, the page is sent
    // only after a click or tap: the arrow keys in a radio group change the choice at every step and would send the
    // first one reached (WCAG 3.2.2; as the automatic "Weiter" of the questionnaire, UI/UX audit 2026-10-16, 79)
    var pointer = false;
    if (form) form.addEventListener("pointerdown", function () { pointer = true; });
    if (form) form.addEventListener("keydown", function () { pointer = false; });
    if (form && form.hasAttribute("data-instant")) form.addEventListener("change", function () {
      if (!pointer) return;
      var names = {};
      form.querySelectorAll('input[type="radio"]').forEach(function (r) { names[r.name] = names[r.name] || r.checked; });
      var done = Object.keys(names).length && Object.keys(names).every(function (n) { return names[n]; });
      if (done) setTimeout(function () { if (form.requestSubmit) form.requestSubmit(); }, 250);  // while sending: sent after it
    });
    // a page with several questions (flow In Blöcken): Enter in a text field goes to the next question, on the last one to
    // the button, as on the pages of a questionnaire; it never sends a page with questions still open
    if (form) (function () {
      function fields(item) {
        return Array.prototype.filter.call(item.querySelectorAll("input:not([type=hidden]), textarea, select"),
          function (el) { return !el.disabled && el.offsetParent !== null; });
      }
      var items = Array.prototype.filter.call(form.querySelectorAll(".p-item"), function (item) { return fields(item).length > 0; });
      if (items.length < 2) return;
      form.addEventListener("keydown", function (e) {
        var el = e.target;
        if (e.key !== "Enter" || e.defaultPrevented || el.tagName !== "INPUT") return;
        if (["text", "email", "number", "tel", "search", "url"].indexOf(el.type) < 0) return;
        var here = items.indexOf(el.closest(".p-item"));
        if (here < 0) return;
        e.preventDefault();
        var next = items[here + 1] ? fields(items[here + 1])[0] : form.querySelector('.p-nav button[type="submit"]');
        if (next) next.focus();
      });
    })();
    if (form) {
      var again = false;  // a send asked for while one is on its way (a corrected tap, T-21): made after it
      function values() { return new URLSearchParams(new FormData(form)).toString(); }
      function send() {
        sending = true;
        again = false;
        var sent = values(), body = new FormData(form);
        if (device) body.append("device", device);
        // the nickname as the field shows it goes with every answer: typed without the tick it was lost with the
        // next load of the page (a new question, "Antwort ändern"); the leaderboard named a random one
        var nickForm = document.querySelector("[data-live-nick]");
        if (nickForm) {
          body.append("live_nick", nickForm.elements.namedItem("name").value);
          if (nickForm.elements.namedItem("team")) body.append("live_team", nickForm.elements.namedItem("team").value);
        }
        var error = form.querySelector("[data-live-error]");
        if (error) error.hidden = true;
        form.querySelectorAll(".p-item.has-error").forEach(function (el) { el.classList.remove("has-error"); });
        form.querySelectorAll("[data-live-item-error]").forEach(function (el) { el.remove(); });
        fetch(form.action, { method: "POST", body: body, credentials: "same-origin" })
          // a refusal that is not JSON (an expired token, an error under load): not sent, said as without a connection (F12)
          .then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) { return { ok: r.ok, d: d || {} }; }); })
          .then(function (res) {
            sending = false;
            // the choice changed while this one was on its way: the changed one goes too (the same one twice would be
            // a second word with "Mehrere Antworten pro Gerät")
            if (again && values() !== sent && res.d.error !== "closed") { send(); return; }
            if (res.d.error === "closed") { held(stage.dataset.late); return; }  // voting on it was over (T-16)
            if (res.d.error === "moved") { fresh(); return; }  // the page changed under the phone (flow In Blöcken)
            if (res.ok && res.d.late) { held(stage.dataset.lateQuiz); return; }  // a quiz question after its countdown
            // a word cloud with several answers: the field empties and keeps the cursor for the next word, without
            // loading the page anew (Ralf, 2026-10-16); the key of the next word, so the phone's asking does not reload
            if (res.ok && form.hasAttribute("data-multi") && res.d.key && res.d.key.indexOf("q-") === 0) { more(res.d); return; }
            if (res.ok || res.d.error === "stale") { shown = null; tick(); return; }  // stale: a double tap, the other one was stored
            if (res.d.error === "full") { fresh(); return; }  // the list of own words stays on the page
            Object.keys(res.d.errors || {}).forEach(function (id) {  // the message under the question
              var box = form.querySelector("#i" + id);
              if (!box) return;
              box.classList.add("has-error");
              var p = document.createElement("p");
              p.className = "p-error"; p.setAttribute("data-live-item-error", ""); p.textContent = res.d.errors[id];
              box.appendChild(p);
            });
            if (!res.d.errors && res.d.error !== "empty") failed(res.d.error);
          })
          .catch(function () { sending = false; failed(""); });
      }
      function more(d) {
        shown = d.key;
        var field = form.querySelector('input[type="text"], textarea');
        if (field) { field.value = ""; field.focus(); }
        var left = form.querySelector("[data-live-left]");
        if (left && d.left != null) left.textContent = fill(left.dataset.liveLeft, { n: d.left });
        var list = form.querySelector("[data-live-words]");
        if (list && d.word) {
          var li = document.createElement("li");
          li.innerHTML = '<span dir="auto"></span><button type="button" class="live-withdraw">×</button>';
          li.firstChild.textContent = d.word.text;
          var x = li.lastChild;
          x.dataset.word = d.word.id; x.title = list.dataset.label; x.setAttribute("aria-label", list.dataset.label + ": " + d.word.text);
          list.appendChild(li);
          list.hidden = false;
        }
        var ok = form.querySelector("[data-live-sent]");
        if (ok) { ok.hidden = false; clearTimeout(more.timer); more.timer = setTimeout(function () { ok.hidden = true; }, 2000); }
      }
      // the answer did not arrive: said under the form (the server's reason, else "not sent, reload")
      function failed(reason) {
        var error = form.querySelector("[data-live-error]");
        if (!error) return;
        error.textContent = reason || stage.dataset.fail || "";
        error.hidden = !error.textContent;
      }
      // × beside one of the device's own words takes it back; the field is free for another one
      form.addEventListener("click", function (e) {
        var x = e.target.closest(".live-withdraw"), list = form.querySelector("[data-live-words]");
        if (!x || !list || x.disabled) return;
        x.disabled = true;
        var body = new FormData();
        body.append("csrf_token", form.elements.namedItem("csrf_token").value);
        body.append("ref", form.elements.namedItem("ref").value);
        body.append("word", x.dataset.word);
        if (device) body.append("device", device);
        fetch(list.dataset.liveWords, { method: "POST", body: body, credentials: "same-origin" })
          .then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) { return { ok: r.ok, d: d || {} }; }); })
          .then(function (res) {
            if (!res.ok) { x.disabled = false; if (res.d.error === "closed") { shown = null; tick(); } else if (res.d.error === "gone") fresh(); else failed(""); return; }
            if (shown && shown.indexOf("full-") === 0) { fresh(); return; }  // the field comes back
            x.closest("li").remove();
            list.hidden = !list.children.length;
            shown = res.d.key;
            var left = form.querySelector("[data-live-left]");
            if (left && res.d.left != null) left.textContent = fill(left.dataset.liveLeft, { n: res.d.left });
          })
          .catch(function () { x.disabled = false; failed(""); });
      });
      form.addEventListener("submit", function (e) {
        e.preventDefault();
        if (sending) { again = true; return; }
        send();
      });
    } else if (shown) {
      draw(shown);
    }
    var leftBox = document.querySelector("[data-live-countdown]");
    function fill(text, values) { Object.keys(values).forEach(function (k) { text = text.split("{" + k + "}").join(values[k]); }); return text; }
    function quizNote(q) {  // a closed quiz question: right or not, the points, the place
      var first = !q.answered ? stage.dataset.none : q.right ? fill(stage.dataset.right, { p: q.points }) : stage.dataset.wrong;
      var place = q.place ? fill(stage.dataset.place, { n: q.place, m: q.of, p: q.total }) : "";
      stage.innerHTML = '<p class="live-wait live-quiz-result' + (q.right ? " is-right" : "") + '">' + esc(first) + "</p>" +
        (place ? '<p class="live-quiz-place">' + esc(place) + "</p>" : "");
    }
    // every request numbered (F17): an answer older than the last one asked for is dropped; the timer asks only
    // while none is on its way (at most 10 s), so a slow server does not pile them up; after an answer it asks at once
    var ticked = 0, tickedAt = 0;
    function tick(now) {
      if (now === false && tickedAt && Date.now() - tickedAt < 10000) return;
      var mine = ++ticked;
      tickedAt = Date.now();
      fetch(stage.dataset.state, { credentials: "same-origin", cache: "no-store" }).then(function (r) { return r.json(); }).then(function (s) {
        if (mine !== ticked) return;
        if (leftBox) { leftBox.hidden = s.left == null; if (s.left != null) leftBox.textContent = fill(stage.dataset.seconds, { s: s.left }); }
        if (Date.now() < hold) return;  // a note that stays a few seconds
        if (s.quiz && s.key !== shown) { shown = s.key; quizNote(s.quiz); return; }
        if (s.key === shown) return;  // also while an answer is being changed (shown: "answered-…")
        if (/^(q|p|slide|full)-/.test(s.key)) { if (!sending) fresh(); return; }  // a new question or page, or the form again for one more word
        draw(s.key);
        if (location.search) history.replaceState(null, "", location.pathname);
      }).catch(function () { /* offline for a moment: ask again */ })
        .then(function () { if (mine === ticked) tickedAt = 0; });
    }
    // a phone with a question to answer asks every POLL_SECONDS; one that waits, has answered, or looks at the
    // screen every second time only (half the requests in a full room; the next question comes at most 2 s later)
    var quiet = 0;
    setInterval(function () {
      if (document.hidden) return;
      var calm = shown && (shown === "wait" || shown === "end" || shown === "ended" || shown === "done" || /^(answered|closed|full|look|slide)-/.test(shown));
      if (calm && (quiet = (quiet + 1) % 2)) return;
      tick(false);
    }, every);
  }

  // --- quiz nickname on the phone (voluntary, only for the leaderboard) ------------------------------------------
  var nick = document.querySelector("[data-live-nick]");
  if (nick) (function () {
    var timer = null, saved = new URLSearchParams(new FormData(nick)).toString();
    // saved, or said that it was not (e.g. the page was open so long that its token ran out)
    function done(ok) { nick.querySelector("[data-nick-note]").hidden = !ok; nick.querySelector("[data-nick-fail]").hidden = ok; }
    function save() {
      clearTimeout(timer);
      var now = new URLSearchParams(new FormData(nick)).toString();
      if (now === saved) return;
      saved = now;
      var body = new FormData(nick);
      if (device) body.append("device", device);
      body.append("csrf_token", nick.dataset.csrf);
      // keepalive: a page that loads anew right after typing (a new question) still sends it
      fetch(nick.dataset.liveNick, { method: "POST", body: body, credentials: "same-origin", keepalive: true })
        .then(function (r) { done(r.ok); if (!r.ok) saved = ""; }).catch(function () { done(false); saved = ""; });
    }
    // saved as it is typed (a pause, leaving the field, a team chosen), the tick only confirms: a name typed without
    // the tick was lost with the next load of the page
    nick.addEventListener("submit", function (e) { e.preventDefault(); saved = ""; save(); });
    nick.addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(save, 800); });
    nick.addEventListener("change", save);
  })();

  // --- Q&A on the phone: ask, vote up (once per device), the list asked for every few seconds ------------------
  var qa = document.querySelector("[data-live-qa]");
  if (qa) {
    var qaList = qa.querySelector("[data-qa-list]"), qaForm = qa.querySelector("[data-qa-form]"), qaNote = qa.querySelector("[data-qa-note]");
    var qaShown = "";
    function qaPost(url, body) {
      body.append("csrf_token", qa.dataset.csrf);
      if (device) body.append("device", device);
      // a refusal that is not JSON (an expired token answers with a page): not sent, as without a connection
      return fetch(url, { method: "POST", body: body, credentials: "same-origin" }).then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (d) { return { ok: r.ok, d: d }; });
      });
    }
    function qaDraw(d) {
      qa.hidden = !d.enabled;
      if (!d.enabled) return;
      var html = d.questions.map(function (q) {
        return '<li class="live-qa-item' + (q.status === "answered" ? " is-answered" : "") + '"><button type="button" class="live-qa-vote' + (q.voted ? " is-voted" : "") +
          '" data-qa-vote="' + q.id + '" aria-pressed="' + (q.voted ? "true" : "false") + '">▲ ' + q.votes + '</button><span>' + esc(q.text) + "</span></li>";
      }).join("");
      if (html !== qaShown) { qaList.innerHTML = html; qaShown = html; }
    }
    function qaAsk() {
      fetch(qa.dataset.liveQa, { credentials: "same-origin", cache: "no-store" }).then(function (r) { return r.json(); }).then(qaDraw).catch(function () {});
    }
    qaForm.addEventListener("submit", function (e) {
      e.preventDefault();
      var body = new FormData(qaForm);
      qaPost(qa.dataset.ask, body).then(function (res) {
        qaNote.hidden = false;
        qaNote.textContent = res.ok ? (res.d.pending ? qa.dataset.waits : qa.dataset.sent) : (res.d.error === "many" ? qa.dataset.many : res.d.error ? "" : qa.dataset.fail);  // empty, or the Q&A just closed
        if (res.ok) qaForm.reset();
        qaAsk();
      }).catch(function () { qaNote.hidden = false; qaNote.textContent = qa.dataset.fail; });
    });
    qaList.addEventListener("click", function (e) {
      var b = e.target.closest("[data-qa-vote]");
      if (b) qaPost(qa.dataset.vote.replace(/\/0\/vote$/, "/" + b.dataset.qaVote + "/vote"), new FormData()).then(qaAsk).catch(function () {});
    });
    qaAsk();
    setInterval(function () { if (!document.hidden) qaAsk(); }, 6000);
  }

  // --- Q&A on Präsentieren: the questions with their status; answered, hidden, approved; the screen on or off --
  var qaBox = document.querySelector("[data-qa]");
  if (qaBox) {
    var qbList = qaBox.querySelector("[data-qa-list]"), qbScreen = qaBox.querySelector("[data-qa-screen]"), qbShown = "";
    function qbSend(action, extra) {
      var body = new FormData();
      body.append("csrf_token", qaBox.dataset.csrf);
      body.append("action", action);
      Object.keys(extra || {}).forEach(function (k) { body.append(k, extra[k]); });
      fetch(qaBox.dataset.control, { method: "POST", body: body, credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
        .then(qbAsk).catch(function () {});
    }
    function qbDraw(d) {
      var html = JSON.stringify(d);
      if (html === qbShown) return;
      qbShown = html;
      if (qaBox.hasAttribute("data-hide-empty")) qaBox.hidden = !d.enabled;  // the presenter view: only while Q&A is on
      qaBox.querySelector("[data-qa-off]").hidden = d.enabled;
      qaBox.querySelector("[data-qa-body]").hidden = !d.enabled;
      if (qbScreen) {
        qbScreen.textContent = d.screen ? de_en("Leinwand: zurück zur Frage", "Screen: back to the question")
          : de_en("Leinwand: Fragen aus dem Publikum zeigen", "Screen: show questions from the audience");
        qbScreen.dataset.state = d.screen ? "on" : "off";
      }
      var ro = qaBox.hasAttribute("data-readonly");
      qbList.innerHTML = d.questions.length ? d.questions.map(function (q) {
        var buttons = ro ? "" : (q.status === "open" ? '<button type="button" class="button button-small" data-qa-act="qa_answer">' + de_en("Beantwortet", "Answered") + "</button>" +
          '<button type="button" class="button button-small button-secondary" data-qa-act="qa_hide">' + de_en("Ausblenden", "Hide") + "</button>"
          : '<button type="button" class="button button-small button-secondary" data-qa-act="qa_open">' + (q.status === "pending" ? de_en("Freigeben", "Approve") : de_en("Wieder offen", "Reopen")) + "</button>");
        return '<li class="live-mod is-' + q.status + '" data-qa-id="' + q.id + '"><span class="live-mod-text">' + esc(q.text) + '</span><span class="live-mod-status">▲ ' +
          q.votes + " · " + esc(q.label) + "</span>" + buttons + "</li>";
      }).join("") : '<li class="muted small">' + de_en("Noch keine Fragen.", "No questions yet.") + "</li>";
    }
    function qbAsk() {
      fetch(qaBox.dataset.qa, { credentials: "same-origin", cache: "no-store" }).then(function (r) { return r.json(); }).then(qbDraw).catch(function () {});
    }
    qbList.addEventListener("click", function (e) {
      var b = e.target.closest("[data-qa-act]");
      if (b) qbSend(b.dataset.qaAct, { qa: b.closest("[data-qa-id]").dataset.qaId });
    });
    if (qbScreen) qbScreen.addEventListener("click", function () { qbSend(qbScreen.dataset.state === "on" ? "qa_screen_off" : "qa_screen_on"); });
    qbAsk();
    setInterval(function () { if (!document.hidden) qbAsk(); }, 3000);
  }

  // --- moderation (Präsentieren): the short texts of the question on the screen, shown, waiting, hidden ----------
  var mod = document.querySelector("[data-moderation]");
  if (mod) {
    var modList = mod.querySelector("[data-moderation-list]"), modBody = mod.querySelector("[data-moderation-body]");
    var modEmpty = mod.querySelector("[data-moderation-empty]"), modItem = null, modShown = "";
    var STATUS = { shown: de_en("sichtbar", "visible"), hidden: de_en("ausgeblendet", "hidden"), pending: de_en("wartet auf Freigabe", "awaiting approval"),
                   filtered: de_en("Schimpfwort-Filter", "swear-word filter") };
    function modSend(action, extra) {
      var body = new FormData();
      body.append("csrf_token", mod.dataset.csrf);
      body.append("action", action);
      Object.keys(extra || {}).forEach(function (k) { body.append(k, extra[k]); });
      fetch(mod.dataset.control, { method: "POST", body: body, credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
        .then(modAsk).catch(function () {});
    }
    function modDraw(d) {
      var html = JSON.stringify(d);
      if (html === modShown) return;
      modShown = html;
      if (mod.hasAttribute("data-hide-empty")) mod.hidden = !d.item;  // the presenter view: only with short texts on the screen
      modEmpty.hidden = !!d.item; modBody.hidden = !d.item;
      if (!d.item) return;
      modItem = d.item;
      var ro = mod.hasAttribute("data-readonly");
      modList.innerHTML = d.entries.length ? d.entries.map(function (e) {
        var buttons = ro ? "" : (e.status === "shown" ? '<button type="button" class="button button-small button-secondary" data-mod="hide">' + de_en("Ausblenden", "Hide") + "</button>"
          : '<button type="button" class="button button-small" data-mod="approve">' + (e.status === "pending" ? de_en("Freigeben", "Approve") : de_en("Zeigen", "Show")) + "</button>");
        return '<li class="live-mod is-' + e.status + '" data-key="' + esc(e.key) + '"><span class="live-mod-text">' + esc(e.text) +
          (e.count > 1 ? ' <span class="muted">×' + e.count + "</span>" : "") + '</span><span class="live-mod-status">' + STATUS[e.status] + "</span>" + buttons + "</li>";
      }).join("") : '<li class="muted small">' + de_en("Noch keine Antworten.", "No answers yet.") + "</li>";
    }
    function modAsk() {
      fetch(mod.dataset.moderation, { credentials: "same-origin", cache: "no-store" }).then(function (r) { return r.json(); }).then(modDraw).catch(function () {});
    }
    modList.addEventListener("click", function (e) {
      var b = e.target.closest("[data-mod]");
      if (b) modSend(b.dataset.mod, { item: modItem, key: b.closest("[data-key]").dataset.key });
    });
    modAsk();
    setInterval(function () { if (!document.hidden) modAsk(); }, 3000);
  }

  // --- reactions on the phone (package G): one tap, one emoji on the screen ------------------------------------
  var react = document.querySelector("[data-live-react]");
  // more emojis than fit beside each other: they wrap into further rows (live.css; Ralf, 2026-10-16)
  // quick clicks (at most 250 ms apart) go together in one request, each click an emoji of its own on the screen
  var burst = [], burstTimer = null;
  function sendBurst() {
    var body = new FormData();
    body.append("csrf_token", react.dataset.csrf);
    burst.forEach(function (e) { body.append("e", e); });
    burst = [];
    if (device) body.append("device", device);
    fetch(react.dataset.liveReact, { method: "POST", body: body, credentials: "same-origin" }).catch(function () {});
  }
  if (react) react.addEventListener("click", function (e) {
    var b = e.target.closest("[data-e]");
    if (!b || b.disabled) return;
    b.classList.remove("is-sent"); void b.offsetWidth; b.classList.add("is-sent");  // the pop again on every click
    setTimeout(function () { b.classList.remove("is-sent"); }, 600);
    if (burst.length < 8) burst.push(b.dataset.e);
    clearTimeout(burstTimer);
    burstTimer = setTimeout(sendBurst, 250);
  });

  // --- the views of a question's results: the screen and the printable slides share them ------------------
  function label(r) {  // a face or stars of a rating scale as the participants saw them, else the text
    var x = r[2] || {};
    if (x.face) {
      return '<span class="p-face live-face" style="--h: ' + x.face.hue + '"><svg viewBox="0 0 40 40" aria-label="' + esc(r[0]) +
        '"><circle cx="20" cy="20" r="17"/><circle class="eye" cx="14" cy="16" r="2.2"/><circle class="eye" cx="26" cy="16" r="2.2"/>' +
        '<path d="M12.5 25 Q20 ' + x.face.ctrl_y + ' 27.5 25"/></svg></span>';
    }
    if (x.stars) return '<span class="live-stars" aria-label="' + esc(r[0]) + '">' + new Array(x.stars + 1).join("★") + "</span>";
    return esc(r[0]);
  }
  function bars(rows) {
    var max = Math.max.apply(null, rows.map(function (r) { return r[1]; }).concat([1]));
    var narrow = rows.some(function (r) { return r[2] && (r[2].face || r[2].stars); });  // faces or stars: a narrow label column
    return '<div class="live-bars' + (narrow ? " live-bars-icons" : "") + '">' + rows.map(function (r) {
      return '<div class="live-bar' + ((r[2] || {}).right ? " is-right" : "") + '"><span class="live-bar-label">' + ((r[2] || {}).right ? "✓ " : "") + label(r) + '</span><span class="live-bar-track"><span style="width:' +
        (r[1] / max * 100).toFixed(1) + '%"></span></span><span class="live-bar-n">' + r[1] + "</span></div>";
    }).join("") + "</div>";
  }
  function words(rows) {  // a click on a word hides it (moderation, package D)
    var max = Math.max.apply(null, rows.map(function (r) { return r[1]; }).concat([1]));
    var min = Math.min.apply(null, rows.map(function (r) { return r[1]; }).concat([max]));
    var order = rows.map(function (r) { return r[1]; }).sort(function (a, b) { return b - a; });
    var fifth = order.length >= 5 ? order[4] : 0;
    // the most frequent first: layoutClouds() places them in the middle; ties by the word, so nothing jumps
    var sorted = rows.slice().sort(function (a, b) { return b[1] - a[1] || String(a[0]).localeCompare(String(b[0])); });
    return '<div class="live-cloud">' + sorted.map(function (r) {
      var n = r[1] > fifth ? 0 : 5;  // among the five most frequent (ties at the fifth place left out)
      // the size grows with the square root of the count, so the area of a word follows its count (UI/UX audit
      // 2026-10-05, V63); the five most frequent words carry their count
      var top5 = n < 5 && r[1] > 1;
      var key = (r[2] || {}).key;  // without a key (the results page) nothing to hide
      // all equally often (e.g. every text once): one middle size, not all at the largest (Ralf, 2026-10-16)
      var size = min === max && rows.length > 1 ? 1.4 : 1 + 2.6 * Math.sqrt(r[1] / max);
      return '<span style="font-size:' + size.toFixed(2) + 'em"' + (key ? ' data-hide="' + esc(key) +
        '" title="' + de_en("Klicken zum Ausblenden", "Click to hide") + '"' : "") + ">" + esc(r[0]) + (top5 ? ' <small class="live-cloud-n">' + r[1] + "</small>" : "") + "</span>";
    }).join(" ") + "</div>";
  }
  // A word cloud without lines (Ralf, 2026-10-08): the words are placed one after the other on a spiral from the
  // middle outwards (the most frequent first), each where it touches no word placed before; wider than high, as a
  // screen is. Without room (hidden, printing) they stay in the plain centred flow of participant.css.
  function layoutCloud(cloud) {
    var items = Array.prototype.slice.call(cloud.children);
    cloud.classList.remove("is-placed");
    cloud.style.height = cloud.style.width = "";
    items.forEach(function (el) { el.style.left = el.style.top = el.style.transform = ""; });
    var width = cloud.clientWidth;
    if (!width || !items.length) return;
    var pad = 6, boxes = [], minY = 0, maxY = 0, row = null, rows = [];
    // each word a little tilted, so the cloud looks less like a table (Ralf, 2026-10-16): at most TILT degrees, the
    // angle taken from the word itself (it keeps it when the cloud grows), the most frequent word straight; the
    // room a word takes is the box around it tilted
    var angles = items.map(function (el, i) { return i ? tilt(el.textContent) : 0; });
    var sizes = items.map(function (el, i) {
      var a = Math.abs(angles[i]) * Math.PI / 180, w = el.offsetWidth, h = el.offsetHeight;
      // the room between words grows with the word: about half a letter beside it, a little above and below, so two
      // single words never read as one phrase (Ralf, 2026-10-16: 6 px looked like "zusammengehörig")
      var gapX = Math.max(16, h * 0.45), gapY = Math.max(6, h * 0.18);
      return { w: w * Math.cos(a) + h * Math.sin(a) + gapX, h: w * Math.sin(a) + h * Math.cos(a) + gapY, ow: w, oh: h };
    });
    function free(b) {
      if (b.x < -width / 2 || b.x + b.w > width / 2) return false;
      return boxes.every(function (o) { return b.x + b.w <= o.x || o.x + o.w <= b.x || b.y + b.h <= o.y || o.y + o.h <= b.y; });
    }
    sizes.forEach(function (s, i) {
      var spot = null;
      // Archimedean spiral, much wider than high; only while its turns still fit the width, beyond that it would
      // only find room straight above and below the middle and stack the words one per line (Ralf, 2026-10-16)
      for (var t = 0; t < 600 && 12 * t < width / 2 + s.w && !spot; t += 0.08) {
        var b = { x: 4 * t * Math.cos(t) * 3 - s.w / 2, y: t * Math.sin(t) * 3 - s.h / 2, w: s.w, h: s.h };
        if (free(b)) spot = b;
      }
      if (!spot) {  // no room on the spiral: below the others, side by side in rows (not one word per line)
        if (!row || row.x + s.w > width / 2) rows.push(row = { x: -width / 2, y: row ? row.y + row.h + pad : maxY + pad, h: 0, spots: [] });
        spot = { x: row.x, y: row.y, w: s.w, h: s.h };
        row.x += s.w; row.h = Math.max(row.h, s.h); row.spots.push(spot);
      }
      boxes.push(spot);
      minY = i ? Math.min(minY, spot.y) : spot.y;
      maxY = i ? Math.max(maxY, spot.y + spot.h) : spot.y + spot.h;
    });
    rows.forEach(function (r) {  // each row centred
      var shift = (width / 2 - r.x) / 2;
      r.spots.forEach(function (b) { b.x += shift; });
    });
    cloud.classList.add("is-placed");
    // the width it had in the flow: a parent that fits its content would otherwise shrink to nothing
    cloud.style.height = Math.ceil(maxY - minY) + "px";
    cloud.style.width = width + "px";
    items.forEach(function (el, i) {  // the word's middle in the middle of its box, turned about it
      el.style.left = Math.round(width / 2 + boxes[i].x + (boxes[i].w - sizes[i].ow) / 2) + "px";
      el.style.top = Math.round(boxes[i].y - minY + (boxes[i].h - sizes[i].oh) / 2) + "px";
      if (angles[i]) el.style.transform = "rotate(" + angles[i] + "deg)";
    });
  }
  var TILT = 8;
  function tilt(text) {  // one of -8, -5, -2, 0, 2, 5, 8 degrees, the same for the same word every time
    var h = 0;
    for (var k = 0; k < text.length; k++) h = (h * 31 + text.charCodeAt(k)) | 0;
    return [-TILT, -5, -2, 0, 2, 5, TILT][Math.abs(h) % 7];
  }
  function layoutClouds(root) {
    (root || document).querySelectorAll(".live-cloud").forEach(layoutCloud);
  }
  var cloudTimer = null, refit = null;  // refit: the projector lays out and fits its slide itself (fit below)
  window.addEventListener("resize", function () {
    clearTimeout(cloudTimer);
    cloudTimer = setTimeout(function () { if (refit) refit(); else layoutClouds(); }, 150);
  });
  function boardView(rows, teams) {  // the leaderboard of the quiz; with teams: the teams first
    if (!rows.length) return '<p class="live-qa-empty">' + esc(t("nopoints")) + "</p>";
    var teamHtml = (teams || []).length ? '<ol class="live-board-list live-board-teams">' + teams.map(function (r, n) {
      return '<li><span class="live-board-place">' + (r.place || n + 1) + '.</span><span class="live-board-name">' + esc(r.name) +
        ' <span class="live-board-members">(' + r.members + ')</span></span><span class="live-board-points">Ø ' + r.points + "</span></li>";
    }).join("") + "</ol>" : "";
    return teamHtml + '<ol class="live-board-list">' + rows.map(function (r, n) {
      return '<li><span class="live-board-place">' + (r.place || n + 1) + '.</span><span class="live-board-name">' + esc(r.name) +
        '</span><span class="live-board-points">' + r.points + "</span></li>";
    }).join("") + "</ol>";
  }
  function qaView(rows) {  // the questions from the audience; a click marks one as answered
    if (!rows.length) return '<p class="live-qa-empty">' + esc(t("noquestions")) + "</p>";
    return '<ol class="live-qa-screen">' + rows.map(function (q) {
      return '<li class="' + (q.status === "answered" ? "is-answered" : "") + '" data-qa-done="' + q.id + '" title="' + de_en("Klicken: beantwortet", "Click: answered") + '">' +
        '<span class="live-qa-votes">▲ ' + q.votes + "</span><span>" + esc(q.text) + "</span></li>";
    }).join("") + "</ol>";
  }
  // the review at the end of a talk: per slide its emojis, counted, and its questions (app/live/, review); who steers can
  // sort it, jump back to a slide (a click on its head) and mark a question answered
  var steers = !!document.querySelector("[data-live-screen]:not([data-readonly])");
  function emojiCounts(rows) {  // [[emoji, n]] as "👍 12 · 😕 3"
    return rows.map(function (e) { return '<span class="live-emoji-count">' + esc(e[0]) + " " + e[1] + "</span>"; }).join(" · ");
  }
  function reviewView(c) {
    if (!c.rows.length) return '<p class="live-review-none">' + esc(c.empty) + "</p>";
    var sort = '<p class="live-review-sort">' + [["slides", de_en("nach Folien", "by slide")], ["unclear", de_en("meiste Reaktionen zuerst", "most reactions first")]].map(function (o) {
      return '<button type="button" data-review-sort="' + o[0] + '" aria-pressed="' + (c.sort === o[0] ? "true" : "false") + '">' + esc(o[1]) + "</button>";
    }).join("") + "</p>";
    return (steers ? sort : "") + '<ol class="live-review">' + c.rows.map(function (r) {
      var head = (r.image ? '<img class="live-review-thumb" src="' + esc(r.image) + '" alt="">' : "") +
        '<span class="live-review-title">' + (r.pos && r.named ? '<span class="live-review-pos">' + r.pos + "</span>" : "") + esc(r.title) + "</span>" +
        (r.emojis && r.emojis.length ? '<span class="live-review-emojis">' + emojiCounts(r.emojis) + "</span>" : "");
      var pick = r.id && steers ? ' data-review-pick="' + r.id + '" title="' + de_en("Klick: diese Folie auf die Leinwand", "Click: this slide on the screen") + '"' : "";
      return '<li class="live-review-row"><div class="live-review-head"' + pick + ">" + head + "</div>" +
        (r.questions.length ? '<ul class="live-review-questions">' + r.questions.map(function (q) {
          return '<li class="' + (q.status === "answered" ? "is-answered" : "") + '"' + (steers ? ' data-qa-done="' + q.id + '"' : "") +
            '><span class="live-qa-votes">▲ ' + q.votes + "</span><span>" + esc(q.text) + "</span></li>";
        }).join("") + "</ul>" : "") + "</li>";
    }).join("") + "</ol>";
  }
  function wall(rows) {  // the posts, the most often sent first; a click hides one
    return '<div class="live-wall">' + rows.map(function (r) {
      return '<p class="live-post" data-hide="' + esc((r[2] || {}).key || "") + '" title="' + de_en("Klicken zum Ausblenden", "Click to hide") + '">' + esc(r[0]) +
        (r[1] > 1 ? ' <span class="live-post-n">×' + r[1] + "</span>" : "") + "</p>";
    }).join("") + "</div>";
  }
  function num(v) {  // always one place (app/live/ rounds the means), so 3,0 stands beside 2,7
    if (v == null) return "–";
    var text = Number(v).toFixed(1), lang = document.documentElement.lang || "de";
    // a decimal comma in every language but those that write numbers like English (units.writes_like_english; fr
    // and pl had a point; Mathe-Audit 2026-10-06, Z-8)
    return lang === "en" || lang === "zh-Hans" || lang === "ar" ? text : text.replace(".", ",");
  }
  // means on a scale (statements of a matrix, points per option, several sliders): a dot at the mean on an axis
  // from the low to the high end, no bar whose length would read as "twice as much" (UI/UX audit 2026-10-05, V53);
  // the ends of the scale labelled under the rows
  function dotRow(labelHtml, at, value) {
    return '<div class="live-bar"><span class="live-bar-label">' + labelHtml + '</span><span class="live-dot-track">' +
      (at == null ? "" : '<span class="live-mean-dot" style="left:' + (Math.max(0, Math.min(1, at)) * 100).toFixed(1) + '%"></span>') +
      '</span><span class="live-bar-n">' + value + "</span></div>";
  }
  function means(c) {
    var span = (c.hi - c.lo) || 1;
    var html = '<div class="live-bars live-means">' + c.rows.map(function (r) {
      return dotRow(esc(r[0]), r[1] == null ? null : (r[1] - c.lo) / span, num(r[1]));
    }).join("") + "</div>";
    var ends = c.ends || [];
    html += '<p class="live-ends"><span>' + esc(ends[0] || num(c.lo)) + "</span><span>" + esc(ends[1] || num(c.hi)) + "</span></p>";
    return html;
  }
  // semantic differential: per pair the left pole, a vertical bar per point (its share of the answers to this pair),
  // the right pole; a mark under the bars at the mean
  function semdiff(c) {
    return '<div class="live-sd">' + c.rows.map(function (r) {
      var total = r.counts.reduce(function (a, b) { return a + b; }, 0) || 1;
      var max = Math.max.apply(null, r.counts.concat([1]));
      var bars = r.counts.map(function (k) {
        return '<span class="live-sd-col" title="' + k + '"><span class="live-sd-bar" style="height:' + (k / max * 100).toFixed(1) + '%"></span>' +
          '<span class="live-sd-share">' + (k ? Math.round(k / total * 100) + "%" : "") + "</span></span>";
      }).join("");
      var at = r.mean == null ? null : (r.mean - 1) / ((c.points - 1) || 1) * 100;
      return '<div class="live-sd-row"><span class="live-sd-pole">' + esc(r.left) + '</span><span class="live-sd-mid"><span class="live-sd-bars">' + bars +
        '</span>' + (at == null ? "" : '<span class="live-sd-mean" style="left:' + at.toFixed(1) + '%" title="' + esc(t("mean")) + " " + num(r.mean) + '"></span>') +
        '</span><span class="live-sd-pole live-sd-right">' + esc(r.right) + "</span></div>";
    }).join("") + "</div>";
  }
  // ranking: the best (lowest mean rank) first, a dot at its mean rank on an axis from 1 (top) to k (V53)
  function ranks(c) {
    var k = c.hi || 1;
    return '<div class="live-bars live-means">' + c.rows.map(function (r, n) {
      return dotRow((n + 1) + ". " + esc(r[0]), r[1] == null ? null : (r[1] - 1) / ((k - 1) || 1), "Ø " + num(r[1]));
    }).join("") + '</div><p class="live-ends"><span>' + esc(t("rank1")) + "</span><span>" + esc(t("rankk")) + " " + k + "</span></p>";
  }
  // the 2x2 field: each term at its mean place, the axes' words at the edges
  function points(c) {
    var a = c.axes || {};
    var dots = (c.points || []).map(function (p) {
      var x = 40 + p.x * 5.2, y = 560 - p.y * 5.2;
      return '<g class="live-point"><circle cx="' + x.toFixed(1) + '" cy="' + y.toFixed(1) + '" r="9"/><text x="' + (x + 14).toFixed(1) +
        '" y="' + (y + 6).toFixed(1) + '">' + esc(p.label) + "</text></g>";
    }).join("");
    return '<svg class="live-field" viewBox="0 0 640 620" role="img" aria-label="' + esc((a.x_label || "") + " × " + (a.y_label || "")) + '">' +
      '<rect x="40" y="40" width="520" height="520"/><line x1="300" y1="40" x2="300" y2="560"/><line x1="40" y1="300" x2="560" y2="300"/>' +
      '<text class="live-axis-end" x="40" y="590">' + esc(a.x_low || "") + '</text><text class="live-axis-end" x="560" y="590" text-anchor="end">' + esc(a.x_high || "") + "</text>" +
      '<text class="live-axis-name" x="300" y="612" text-anchor="middle">' + esc(a.x_label || "") + "</text>" +
      '<text class="live-axis-end" x="30" y="560" text-anchor="end" transform="rotate(-90 30 560)">' + esc(a.y_low || "") + "</text>" +
      '<text class="live-axis-end" x="30" y="40" transform="rotate(-90 30 40)" text-anchor="start">' + esc(a.y_high || "") + "</text>" +
      '<text class="live-axis-name" x="12" y="300" text-anchor="middle" transform="rotate(-90 12 300)">' + esc(a.y_label || "") + "</text>" +
      dots + "</svg>";
  }
  // image_mark: the image with every mark over it. Spots as a heat map (Philipp, 2026-10-07: salient also with few
  // answers): soft blobs add up on a canvas, the density is coloured from the item's colour (one tap: the middle of
  // the ramp) to red (where most tapped); up to CORES taps keep a small core with a dark ring; the image dims a little
  var CORES = 30, HEAT = 400;
  function hex(color) {
    var m = /^#?([0-9a-f]{6})$/i.exec(color || "");
    var v = parseInt(m ? m[1] : "ffd400", 16);
    return [v >> 16 & 255, v >> 8 & 255, v & 255];
  }
  function heatmap(list, color) {  // a data URL of the coloured density, or "" where there is no canvas
    var canvas = document.createElement("canvas"), g = canvas.getContext && canvas.getContext("2d");
    if (!g) return "";
    canvas.width = canvas.height = HEAT;
    var r = HEAT * (list.length < 10 ? 0.07 : 0.055);
    list.forEach(function (m) {
      var x = m[0] * HEAT, y = m[1] * HEAT, blob = g.createRadialGradient(x, y, 0, x, y, r);
      blob.addColorStop(0, "rgba(0,0,0,0.35)");
      blob.addColorStop(1, "rgba(0,0,0,0)");
      g.fillStyle = blob;
      g.fillRect(x - r, y - r, 2 * r, 2 * r);
    });
    var img = g.getImageData(0, 0, HEAT, HEAT), d = img.data, top = 0, i;
    for (i = 3; i < d.length; i += 4) if (d[i] > top) top = d[i];
    var scale = Math.max(top, 0.7 * 255), low = hex(color), high = [208, 0, 0];
    for (i = 0; i < d.length; i += 4) {
      var v = d[i + 3] / scale, w = Math.max(0, (v - 0.5) * 2);  // up to the middle: the colour; above: towards red
      d[i] = low[0] + (high[0] - low[0]) * w;
      d[i + 1] = low[1] + (high[1] - low[1]) * w;
      d[i + 2] = low[2] + (high[2] - low[2]) * w;
      d[i + 3] = Math.min(1, v * 2.5) * 0.85 * 255;
    }
    g.putImageData(img, 0, 0);
    return canvas.toDataURL();
  }
  function marks(c) {
    if (!c.image) return '<p class="live-qa-empty">' + esc(t("noimage")) + "</p>";
    var color = c.color || "#ffd400", list = c.marks || [], n = list.length, shapes = "", heat = "";
    if (c.mode === "lines") {
      var alpha = Math.max(0.25, Math.min(0.9, 2 / Math.sqrt(n + 3))).toFixed(2);
      shapes = list.map(function (m) {
        var pts = m.map(function (p) { return (p[0] * 1000).toFixed(1) + "," + (p[1] * 1000).toFixed(1); }).join(" ");
        return '<polyline class="live-mark-under" points="' + pts + '"/><polyline points="' + pts + '" style="stroke:' + esc(color) + ";opacity:" + alpha + '"/>';
      }).join("");
    } else {
      heat = n ? heatmap(list, color) : "";
      if (heat) shapes = '<image href="' + heat + '" x="0" y="0" width="1000" height="1000" preserveAspectRatio="none"/>';
      if (n <= CORES || !heat) shapes += list.map(function (m) {
        return '<circle class="live-mark-core" cx="' + (m[0] * 1000).toFixed(1) + '" cy="' + (m[1] * 1000).toFixed(1) + '" r="7" style="fill:' + esc(color) + '"/>';
      }).join("");
    }
    return '<div class="live-marks' + (n ? " has-marks" : "") + '"><img src="' + esc(c.image) + '" alt=""><svg viewBox="0 0 1000 1000" preserveAspectRatio="none">' +
      shapes + "</svg></div>";
  }
  function view(c) {
    if (c.kind === "words") return words(c.rows);
    if (c.kind === "wall") return wall(c.rows);
    if (c.kind === "qa") return qaView(c.rows);
    if (c.kind === "board") return boardView(c.rows, c.teams);
    if (c.kind === "review") return reviewView(c);
    if (c.kind === "means") return means(c);
    if (c.kind === "semdiff") return semdiff(c);
    if (c.kind === "ranks") return ranks(c);
    if (c.kind === "points") return points(c);
    if (c.kind === "marks") return marks(c);
    // a choice with "Anderes, und zwar …": what was typed, as posts under the bars (Ralf, 2026-10-16)
    return bars(c.rows) + (c.notes && c.notes.length ? wall(c.notes) : "");
  }
  // --- the printable slides (Präsentieren → Folien mit Ergebnissen): one page per question ---------------------
  var slides = document.querySelector("[data-live-slides]");
  if (slides) {
    var pages = JSON.parse(slides.textContent);
    var kicker = (document.querySelector(".live-slides-head .live-title") || {}).textContent || "";  // the vote's title on every slide
    var names = [];
    try { names = JSON.parse(slides.dataset.names || "[]"); } catch (e) { names = []; }
    slides.innerHTML = pages.map(function (c) {
      if (c.kind === "slide") {  // a presentation slide: its picture whole, the pen notes over it (live_ink.js)
        return '<section class="live-slide live-slide-pdf">' + (c.image ? '<div class="live-slide-full" data-ink-item="' + c.item +
          '" data-ink-strokes="' + esc(JSON.stringify(c.ink || [])) + '"><img src="' + esc(c.image) + '" alt="' + esc(c.alt || "") + '"></div>'
          : '<p class="live-q">' + esc(c.alt || "") + "</p>") + "</section>";
      }
      var body = c.kind === "content" ? '<div class="live-content">' + c.html + "</div>" : view(c);
      if (c.compare && c.kind !== "content") {  // two runs side by side, each with its name and number of answers
        body = '<div class="live-compare"><div><p class="live-compare-name">' + esc(names[0] || "") + " · " + c.n + "</p>" + body +
          '</div><div><p class="live-compare-name">' + esc(names[1] || "") + " · " + c.compare.n + "</p>" + view(c.compare) + "</div></div>";
      }
      return '<section class="live-slide">' + (kicker ? '<p class="live-slide-kicker">' + esc(kicker) + "</p>" : "") +
        '<h2 class="live-q">' + esc(c.question || "") + '</h2>' +
        (c.n != null && c.kind !== "content" ? '<p class="live-n">' + c.n + " " + (c.n === 1 ? t("answer") : t("answers")) +
          (c.mean != null ? " · " + t("mean") + " " + num(c.mean) : "") +
          (c.median != null ? " · " + t("median") + " " + num(c.median) : "") + "</p>" : "") +
        '<div class="live-result"><div class="live-fit">' + body + "</div></div></section>";
    }).join("");
    // every slide has the size of the printed page (live.css); on the screen they are scaled down as a whole to the
    // width there is, and a result larger than its slide is scaled down inside it (Ralf, 2026-10-16)
    // A result too high for its slide is laid out wider and then scaled down, so that it fills the width and the
    // height: a word cloud gets a wider spiral instead of rows below, many bars stay long (the widths tried: WIDER).
    var SLIDE_W = 1032, FIT_MIN_SLIDE = 0.3, WIDER = [1, 1.25, 1.5, 1.75, 2, 2.5, 3];
    var fitSlides = function () {
      slides.style.setProperty("--slide-zoom", Math.min(1, slides.clientWidth / SLIDE_W).toFixed(4));
      slides.querySelectorAll(".live-slide").forEach(function (slide) {
        var box = slide.querySelector(".live-result"), inner = slide.querySelector(".live-fit");
        if (!box || !inner) return;  // a presentation slide sizes itself (live.css)
        inner.style.zoom = inner.style.width = "";
        layoutClouds(inner);
        var room = box.clientHeight, width = box.clientWidth;
        if (!room || !width || inner.scrollHeight <= room) return;
        var best = null;
        WIDER.forEach(function (f) {
          inner.style.width = Math.round(width * f) + "px";
          layoutClouds(inner);
          var zoom = Math.min(1, 1 / f, room / inner.scrollHeight);
          if (!best || zoom > best.zoom + 0.005) best = { f: f, zoom: zoom };
        });
        // scaled down for its height: as wide as the slide again after scaling (bars as long as the slide)
        var f = best.zoom < 1 / best.f ? 1 / best.zoom : best.f;
        inner.style.width = Math.round(width * f) + "px";
        layoutClouds(inner);
        var zoom = Math.min(1, 1 / f, room / inner.scrollHeight);
        inner.style.zoom = Math.max(FIT_MIN_SLIDE, Math.floor(zoom * 1000) / 1000);
      });
    };
    fitSlides();
    refit = fitSlides;  // a new window width: the slides' scale anew (the resize handler above)
  }

  // --- Präsentieren on a second screen, like starting a slide show (Ralf, 2026-10-09) -------------------------
  // Chrome and Edge (Window Management API): one click starts from the beginning, this window goes full screen on
  // the other screen with the audience view in a frame, and the presenter view opens as a window on this screen
  // (the browser lets a window that goes full screen on another screen open one companion window with the same
  // click). The browser asks once for the right to place windows on all screens. Esc ends the full screen and the
  // frame goes; the presenter view stays open. Other browsers and a single screen: the buttons beside, with a hint.
  var two = document.querySelector("[data-two-screens]"), twoHint = document.querySelector("[data-two-screens-hint]");
  if (two) {
    var twoNote = function (text) { if (twoHint) { twoHint.textContent = text; twoHint.hidden = !text; } };
    var twoSupported = "getScreenDetails" in window && "isExtended" in window.screen && !!document.documentElement.requestFullscreen;
    var twoRefresh = function () {
      two.hidden = !window.screen.isExtended;
      twoNote(window.screen.isExtended ? "" : de_en("Mit einem zweiten Bildschirm (erweitert, nicht gespiegelt) erscheint hier „Auf zweitem Bildschirm präsentieren“: die Ansicht fürs Publikum im Vollbild auf dem Beamer, die Ansicht zum Vortragen auf diesem Bildschirm.",
        "With a second screen (extended, not mirrored) “Present on a second screen” appears here: the audience view in full screen on the projector, the presenter view on this screen."));
    };
    if (!twoSupported) {
      twoNote(de_en("Zwei Bildschirme mit einem Klick gehen nur in Chrome und Edge. Hier: „Ansicht fürs Publikum“ öffnen, das Fenster auf den Beamer ziehen und dort F drücken.",
        "Two screens with one click only work in Chrome and Edge. Here: open “Audience view”, drag the window to the projector and press F there."));
    } else {
      twoRefresh();
      if (window.screen.addEventListener) window.screen.addEventListener("change", twoRefresh);
      var frame = null;
      document.addEventListener("fullscreenchange", function () {
        if (!document.fullscreenElement && frame) { frame.remove(); frame = null; }
      });
      var twoGo = function (details) {
        var here = details.currentScreen, others = details.screens.filter(function (s) { return s !== here; });
        var target = others.filter(function (s) { return !s.isInternal; })[0] || others[0];
        if (!target) { twoRefresh(); return; }
        if (navigator.userActivation && !navigator.userActivation.isActive) {  // the browser's question took the click
          twoNote(de_en("Erlaubt. Bitte noch einmal auf „Auf zweitem Bildschirm präsentieren“ klicken.", "Allowed. Please click “Present on a second screen” once more."));
          return;
        }
        frame = document.createElement("iframe");
        frame.className = "live-two-frame";
        frame.title = de_en("Ansicht fürs Publikum", "Audience view");
        frame.setAttribute("allow", "fullscreen");
        frame.src = two.dataset.screen;
        document.body.appendChild(frame);
        frame.requestFullscreen({ screen: target }).catch(function () {
          if (frame) { frame.remove(); frame = null; }
          twoNote(de_en("Der Browser hat das Vollbild abgelehnt. Bitte noch einmal klicken oder „Ansicht fürs Publikum“ öffnen und dort F drücken.",
            "The browser refused full screen. Please click again, or open “Audience view” and press F there."));
        });
        // the companion window, in the same click: the presenter view filling this screen
        var w = window.open(two.dataset.speaker, two.dataset.name, "popup,left=" + here.availLeft + ",top=" + here.availTop +
          ",width=" + here.availWidth + ",height=" + here.availHeight);
        if (w) w.focus();
        twoNote("");
      };
      two.addEventListener("click", function () {
        var body = new FormData();
        body.append("csrf_token", two.dataset.csrf);
        body.append("action", "start");
        two.disabled = true;
        // from the beginning first (both views follow the state anyway), then the screens; the click stays valid a
        // few seconds, the browser's question about screens may outlast it (twoGo says so)
        fetch(two.dataset.control, { method: "POST", body: body, credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
          .then(function (r) { if (!r.ok) throw new Error(r.status); return window.getScreenDetails(); })
          .then(twoGo)
          .catch(function () {
            twoNote(de_en("Ohne die Erlaubnis, Fenster auf allen Bildschirmen zu platzieren, geht es nicht (Schloss-Symbol in der Adresszeile). Sonst: „Ansicht zum Vortragen“ (bzw. „Von vorn beginnen“) und „Ansicht fürs Publikum“ wie bisher.",
              "Without the permission to place windows on all screens this does not work (lock icon in the address bar). Otherwise: “Presenter view” and “Audience view” as before."));
          })
          .then(function () { two.disabled = false; });
      });
    }
  }

  // --- screen --------------------------------------------------------------------------------------------------
  var screen = document.querySelector("[data-live-screen]");
  if (screen) {
    var q = screen.querySelector("[data-q]"), n = screen.querySelector("[data-n]"), out = screen.querySelector("[data-result]");
    // results hidden: a cartoon ghost in the middle of the screen, the words under it; drawn like the POLLtergeist ghost
    // (app/templates/_ghost.html; Ralf 2026-10-09): one hand up as in a vote, the bars of the hidden result on its belly
    var GHOST_SVG = '<svg class="live-ghost" viewBox="0 0 132 140" aria-hidden="true"><g class="live-ghost-body">' +
      '<path d="M60 8C33 8 16 29 16 58v60c0 4 4 6 7 3l8-7 8 8c2 2 5 2 7 0l7-7 7 7c2 2 5 2 7 0l7-7 7 7c2 2 5 2 7 0l8-8 8 7' +
      'c3 3 7 1 7-3V58C104 29 87 8 60 8z"/><path d="M103 66c9-5 15-16 15-27 0-6-8-8-10-2-2 8-5 15-10 19"/>' +
      '<path d="M17 68c-8 0-12 6-9 10 3 4 8 3 10 0"/></g>' +
      '<ellipse class="live-ghost-eye" cx="46" cy="54" rx="6.5" ry="9"/><ellipse class="live-ghost-eye" cx="74" cy="54" rx="6.5" ry="9"/>' +
      '<g class="live-ghost-shine"><circle cx="48.3" cy="50.5" r="2.2"/><circle cx="76.3" cy="50.5" r="2.2"/></g>' +
      '<g class="live-ghost-cheeks"><ellipse cx="35" cy="68" rx="6" ry="3.4"/><ellipse cx="85" cy="68" rx="6" ry="3.4"/></g>' +
      '<path class="live-ghost-mouth" d="M54 70q6 5 12 0"/>' +
      '<g class="live-ghost-bars"><rect x="45" y="94" width="8" height="14" rx="2"/><rect x="56" y="84" width="8" height="24" rx="2"/>' +
      '<rect x="67" y="90" width="8" height="18" rx="2"/></g></svg>';
    // results hidden: the ghost in the middle (a click shows them); shown: it waits small in the corner (a click hides them)
    // the role "Ansehen" watches only (live.control needs "Bearbeiten", F16): no steering, the ghost is no button
    var readonly = screen.hasAttribute("data-readonly");
    var GHOST = readonly
      ? '<div class="live-hidden">' + GHOST_SVG + '<span class="live-hidden-text">' + esc(t("hidden")) + '</span><span class="live-hidden-brand p-brand">' + esc(BRAND_MARK) + '</span></div>'
      : '<button type="button" class="live-hidden" data-ghost="show" title="' + esc(t("reveal")) + '">' + GHOST_SVG +
      '<span class="live-hidden-text">' + esc(t("hidden")) + '</span><span class="live-hidden-hint">' + esc(t("reveal")) + '</span><span class="live-hidden-brand p-brand">' + esc(BRAND_MARK) + '</span></button>';
    var sectionLabel = screen.querySelector("[data-section]"), outline = screen.querySelector("[data-outline]");
    var corner = screen.querySelector("[data-ghost-corner]");
    if (corner) corner.innerHTML = '<span class="live-corner-brand p-brand">' + esc(BRAND_MARK) + '</span>' + GHOST_SVG;
    function put(html) {  // only when something changed: bars keep their transition, the ghost keeps blinking
      if (out.dataset.html !== html) { out.innerHTML = html; out.dataset.html = html; layoutClouds(out); }
    }
    // the ghost blinks now and then (every 3 to 10 s), not with "reduce motion"
    if (!(window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches)) {
      (function blink() {
        setTimeout(function () {
          screen.querySelectorAll(".live-ghost").forEach(function (ghost) {
            ghost.classList.add("is-blinking"); setTimeout(function () { ghost.classList.remove("is-blinking"); }, 180);
          });
          blink();
        }, 3000 + Math.random() * 7000);
      })();
    }
    var toggleResults = screen.querySelector('[data-toggle="results"]');
    var toggleVoting = screen.querySelector('[data-toggle="voting"]'), toggleWall = screen.querySelector('[data-toggle="wall"]');
    var toggleQa = screen.querySelector('[data-toggle="qa"]'), toggleBoard = screen.querySelector('[data-toggle="board"]');
    var toggleReview = screen.querySelector('[data-toggle="review"]'), emojiCount = screen.querySelector("[data-emoji-count]");
    var toggleCorner = screen.querySelector('[data-toggle="corner"]'), qrCorner = screen.querySelector("[data-qr-corner]");
    var nextBlock = screen.querySelector('[data-step="next_block"]');
    var closedHint = screen.querySelector("[data-closed-hint]"), lastFlash = null;
    function drawOutline(s) {  // the presenter's overview: a dot per question (filled: on the screen, dimmed: voting closed)
      if (!outline) return;
      if (s.blocks) {  // flow In Blöcken: a box per block, the running one marked, starten on the others; its dots pick the screen
        outline.innerHTML = s.blocks.map(function (b) {
          var onScreen = b.questions.some(function (q) { return q.screen; }) && !!s.show;  // its slide on the screen, visible
          return '<div class="live-sec' + (b.active ? " is-active" : "") + (onScreen ? " is-shown" : "") + '"' + (b.questions.length ? ' data-block-first="' + b.questions[0].id + '"' : "") +
            ' title="' + de_en("Klick: den Block von vorn auf der Leinwand zeigen", "Click: show the block from its start on the screen") + '"><span class="live-sec-title"><span class="live-sec-name">' + esc(b.title) + "</span>" +
            (b.active ? '<span class="live-sec-state">' + de_en("Antworten offen", "Answers open") + "</span>"
              : (b.answerable ? '<span class="live-sec-state">' + de_en("geschlossen", "closed") + '</span><button type="button" class="live-release" data-release="' + b.id +
                '" title="' + de_en("Abfrage dieses Blocks starten; der laufende Block schließt", "Start voting on this block; the running block closes") + '">' + de_en("starten", "start") + "</button>" : "")) +
            '</span><span class="live-dots">' + b.questions.map(function (q) {
              return '<button type="button" class="live-dot' + (q.screen ? " is-screen" : "") + '" data-pick="' + q.id +
                '" title="' + de_en("Auf die Leinwand: ", "On the screen: ") + esc(q.title) + '" aria-label="' + de_en("Auf die Leinwand: ", "On the screen: ") + esc(q.title) + '"></button>';
            }).join("") + "</span></div>";
        }).join("");
      } else {
        outline.innerHTML = '<span class="live-dots">' + (s.outline || []).map(function (q) {
          return '<button type="button" class="live-dot' + (q.screen ? " is-screen" : "") + (q.closed ? " is-closed" : "") +
            '" data-pick="' + q.id + '" title="' + de_en("Auf die Leinwand: ", "On the screen: ") + esc(q.title) + '" aria-label="' + de_en("Auf die Leinwand: ", "On the screen: ") + esc(q.title) + '"></button>';
        }).join("") + "</span>";
      }
      if (nextBlock) nextBlock.hidden = !s.blocks;
      // the slide on the screen is in a block not running yet: say so (presenter view), the next-block button blinks
      var shownBlock = (s.blocks || []).filter(function (b) { return b.questions.some(function (q) { return q.screen; }); })[0];
      var notStarted = !!(shownBlock && !shownBlock.active && shownBlock.answerable);
      if (closedHint) closedHint.hidden = !notStarted;
      var flashKey = notStarted ? shownBlock.id + ":" + (s.current ? s.current.id : "") : null;
      if (nextBlock && flashKey && flashKey !== lastFlash) {
        nextBlock.classList.remove("is-flashing"); void nextBlock.offsetWidth; nextBlock.classList.add("is-flashing");
      }
      lastFlash = flashKey;
      if (toggleResults) {  // the ghost, small, in front of the words
        var label = s.show ? de_en("Ergebnis verbergen", "Hide result") : de_en("Ergebnis zeigen", "Show result");
        if (toggleResults.dataset.label !== label) {
          toggleResults.innerHTML = '<span class="live-ghost-mini">' + GHOST_SVG + "</span>" + label;
          toggleResults.dataset.label = label;
        }
        toggleResults.dataset.state = s.show ? "on" : "off";
      }
      if (toggleBoard) {
        var onBoard = !!(s.current && s.current.kind === "board");
        toggleBoard.hidden = !s.quiz;
        toggleBoard.textContent = onBoard ? de_en("Zurück zur Frage", "Back to the question") : de_en("Rangliste", "Leaderboard");
        toggleBoard.dataset.state = onBoard ? "on" : "off";
      }
      var onSlide = !!(s.current && s.current.kind === "slide");  // a presentation slide: over the whole screen
      if (layout) layout.classList.toggle("is-slide", onSlide);
      if (qrCorner) qrCorner.hidden = !(onSlide && s.corner && !s.qr);
      if (toggleCorner) {
        toggleCorner.hidden = !onSlide;
        toggleCorner.textContent = s.corner ? de_en("QR aus", "QR off") : de_en("QR an", "QR on");
        toggleCorner.dataset.state = s.corner ? "on" : "off";
      }
      if (toggleReview) {
        var onReview = !!(s.current && s.current.kind === "review");
        toggleReview.hidden = !(s.qa || s.emojis_on);
        toggleReview.textContent = onReview ? de_en("Zurück zur Folie", "Back to the slide") : de_en("Rückblick", "Review");
        toggleReview.dataset.state = onReview ? "on" : "off";
      }
      if (emojiCount) {  // the presenter view: the emojis sent while this slide is on the screen, counted
        var counted = !!(s.current && ["review", "qa", "board"].indexOf(s.current.kind) < 0);
        emojiCount.hidden = !s.emojis_on || !counted;
        emojiCount.innerHTML = esc(de_en("Reaktionen auf dieser Folie: ", "Reactions on this slide: ")) +
          (s.emojis && s.emojis.length ? emojiCounts(s.emojis) : esc(de_en("keine", "none")));
        emojiCount.classList.toggle("is-some", !!(s.emojis && s.emojis.length));
      }
      if (toggleQa) {
        var onQa = !!(s.current && s.current.kind === "qa");
        toggleQa.hidden = !s.qa;
        toggleQa.textContent = onQa ? de_en("Zurück zur Frage", "Back to the question") : de_en("Fragen aus dem Publikum", "Questions from the audience");
        toggleQa.dataset.state = onQa ? "on" : "off";
      }
      if (toggleWall) {
        toggleWall.hidden = !s.text || !!(s.current && s.current.kind === "qa");
        toggleWall.textContent = s.wall ? de_en("Als Wortwolke", "As a word cloud") : de_en("Als Pinnwand", "As a wall");
        toggleWall.dataset.state = s.wall ? "on" : "off";
      }
      if (toggleVoting) {
        // flow In Blöcken: the running block takes the answers; a text, image or presentation slide takes none
        toggleVoting.hidden = !s.current || !!s.blocks || s.current.kind === "slide" || s.current.kind === "content";
        toggleVoting.textContent = s.open ? de_en("Antworten schließen", "Close answers") : de_en("Antworten wieder öffnen", "Reopen answers");
        toggleVoting.dataset.state = s.open ? "on" : "off";
      }
    }
    // a click on the QR code or the address: both large in the middle of the window, for scanning from the back
    // rows; another click (or Esc) puts them back
    var qrBox = screen.querySelector(".live-qr"), qrUrl = screen.querySelector(".live-joinurl"), zoom = null;
    // the presenter view has no QR code of its own, only the address and this button (Ralf, 2026-10-23)
    var qrToggle = screen.querySelector("[data-qr-toggle]");
    function qrToggled(on) {
      if (!qrToggle) return;
      qrToggle.setAttribute("aria-pressed", on ? "true" : "false");
      qrToggle.textContent = on ? de_en("Großen QR-Code schließen", "Close the large QR code") : de_en("QR-Code groß zeigen", "Show QR code large");
    }
    function unzoom() { if (zoom) { zoom.remove(); zoom = null; } qrToggled(false); }
    function zoomQr() {
      if (zoom) { unzoom(); return; }
      qrToggled(true);
      zoom = document.createElement("div");
      zoom.className = "live-qr-zoom";
      zoom.setAttribute("role", "dialog");
      zoom.setAttribute("aria-label", de_en("QR-Code groß", "QR code large"));
      zoom.innerHTML = '<div class="live-qr-zoom-code">' + (qrBox ? qrBox.innerHTML : "") + '</div><p class="live-qr-zoom-url"><code>' + esc(qrUrl ? qrUrl.textContent : "") + "</code></p>";
      zoom.addEventListener("click", function () { wantQr(false); });
      document.body.appendChild(zoom);
    }
    // the state is the server's (state "qr"): a click in the presenter view shows it large on the projector too
    function wantQr(on) { step(on ? "qr_on" : "qr_off"); }
    if (!readonly) [qrBox, qrUrl].forEach(function (el) { if (el) { el.classList.add("live-zoomable"); el.title = de_en("Klick: groß in der Mitte, auch auf dem Beamer", "Click: large in the middle, on the projector too"); el.addEventListener("click", function () { wantQr(!zoom); }); } });
    // the title shows only on hovering: a small line under the address says it to everyone who steers
    var qrLast = qrUrl || qrBox;
    if (qrToggle) qrToggle.addEventListener("click", function () { wantQr(!zoom); });
    else if (!readonly && qrLast) {
      var qrHint = document.createElement("p");
      qrHint.className = "live-qr-hint";
      qrHint.textContent = de_en("Klick auf den QR-Code vergrößert ihn.", "Click the QR code to enlarge it.");
      qrHint.addEventListener("click", function () { wantQr(!zoom); });
      qrLast.insertAdjacentElement("afterend", qrHint);
    }
    document.addEventListener("keydown", function (e) { if (e.key === "Escape" && zoom) wantQr(false); });
    var floats = screen.querySelector("[data-floats]"), seenReaction = null;
    function float(reactions) {  // each new emoji rises once over the screen (not with "reduce motion": a short pop)
      if (!floats || !reactions) return;
      if (seenReaction === null) { seenReaction = reactions.length ? reactions[reactions.length - 1][0] : 0; return; }
      reactions.forEach(function (r) {
        if (r[0] <= seenReaction) return;
        seenReaction = r[0];
        var el = document.createElement("span");
        el.className = "live-float";
        el.textContent = r[1];
        el.style.left = (10 + Math.random() * 80).toFixed(1) + "%";
        el.style.animationDelay = (Math.random() * 1.2).toFixed(2) + "s";
        floats.appendChild(el);
        setTimeout(function () { el.remove(); }, 5000);
      });
    }
    // the steering on the projector: hidden, the key C shows it; the browser remembers the choice
    var steering = screen.querySelector("[data-steering]");
    function showSteering(on) {
      if (!steering) return;
      steering.hidden = !on;
      try { localStorage.setItem("live-steering", on ? "1" : ""); } catch (e) { /* no storage */ }
    }
    if (steering) {
      var remembered = false;
      try { remembered = localStorage.getItem("live-steering") === "1"; } catch (e) { /* no storage */ }
      showSteering(remembered);
      document.addEventListener("keydown", function (e) {
        if (!e.altKey && !e.ctrlKey && !e.metaKey && (e.key === "c" || e.key === "C")) showSteering(steering.hidden);
      });
    }
    // full screen for the projector: the corner button or F (not while typing); the browser's Esc leaves it; not in
    // the frame of „Auf zweitem Bildschirm präsentieren“ (Präsentieren), which is full screen already
    var full = screen.querySelector("[data-fullscreen]");
    if (full && document.documentElement.requestFullscreen && window.self === window.top) {
      full.hidden = false;
      var sep = screen.querySelector(".live-fullscreen-sep");
      if (sep) sep.hidden = false;
      var toggleFull = function () {
        if (document.fullscreenElement) { document.exitFullscreen(); return; }
        // refused or left waiting (a browser setting, an embedded view): the browser's own full screen still works
        var refused = function () {
          if (document.fullscreenElement) return;
          full.title = de_en("Vollbild hier nicht erlaubt: F11 (Mac: Ctrl+Cmd+F) drücken", "Full screen not allowed here: press F11 (Mac: Ctrl+Cmd+F)");
          full.classList.add("is-refused");
          setTimeout(function () { full.classList.remove("is-refused"); }, 4000);
        };
        document.documentElement.requestFullscreen().catch(refused);
        setTimeout(refused, 1500);
      };
      full.addEventListener("click", toggleFull);
      document.addEventListener("fullscreenchange", function () {
        full.setAttribute("aria-pressed", document.fullscreenElement ? "true" : "false");
        var label = full.querySelector("[data-fullscreen-label]");
        if (label) label.textContent = document.fullscreenElement ? de_en("Vollbild beenden", "Exit full screen") : de_en("Vollbild", "Full screen");
      });
      document.addEventListener("keydown", function (e) {
        if (e.altKey || e.ctrlKey || e.metaKey || (e.key !== "f" && e.key !== "F")) return;
        if (e.target.closest && e.target.closest("input, textarea, select, [contenteditable]")) return;
        e.preventDefault(); toggleFull();
      });
    }
    var nextBox = screen.querySelector("[data-next]"), notesBox = screen.querySelector("[data-notes]");
    var pos = screen.querySelector("[data-position]");
    // "Als Nächstes" (concept live-naechste-folie-konzept.md): the next presentation slide as its picture, a question as
    // its text, type and first answers, grey (pink means live); the title folds it, remembered in the browser (E5)
    var nextWrap = screen.querySelector("[data-next-box]"), nextToggle = screen.querySelector("[data-next-toggle]"), nextHtml = null;
    var NEXT_KEY = "live-next-folded";
    function nextFolded() { try { return localStorage.getItem(NEXT_KEY) === "1"; } catch (e) { return false; } }
    function nextFold(folded) {
      if (nextBox) nextBox.hidden = folded;
      if (nextToggle) nextToggle.setAttribute("aria-expanded", folded ? "false" : "true");
      if (nextWrap) nextWrap.classList.toggle("is-folded", folded);
    }
    if (nextToggle) {
      nextFold(nextFolded());
      nextToggle.addEventListener("click", function () {
        var folded = !nextFolded();
        try { localStorage.setItem(NEXT_KEY, folded ? "1" : "0"); } catch (e) { /* only for this page */ }
        nextFold(folded);
      });
    }
    function nextCard(x) {
      if (!x) return "";
      if (x.kind === "end") return '<p class="live-next-end">' + de_en("Ende der Folien", "End of the slides") + "</p>";
      var html = '<p class="live-next-type">' + esc(x.type || "") + (x.later_block ? " · " + de_en("gehört zum nächsten Block", "belongs to the next block") : "") + "</p>";
      if (x.image) html += '<img class="live-next-img" src="' + esc(x.image) + '" alt="' + esc(x.alt || "") + '">';
      else if (x.kind === "slide") html += '<p class="live-next-q">' + esc(x.alt || de_en("Folie ohne Bild", "Slide without a picture")) + "</p>";
      if (x.kind !== "slide" && x.question) html += '<p class="live-next-q">' + esc(x.question) + "</p>";
      if (x.options && x.options.length) {
        html += '<ul class="live-next-opts">' + x.options.map(function (o) { return "<li>" + esc(o) + "</li>"; }).join("") +
          (x.more ? '<li class="live-none">' + de_en("+ {n} weitere", "+ {n} more").replace("{n}", x.more) + "</li>" : "") + "</ul>";
      }
      return html;
    }
    // the projector loads the picture of a next presentation slide ahead, unseen, so the change comes without a gap (E8)
    var preloaded = "";
    function preload(src) { if (src && src !== preloaded) { preloaded = src; (new Image()).src = src; } }
    function presenterInfo(s) {  // only on the presenter view (the elements are not on the projector's screen)
      preload(s.preload || (s.next && s.next.kind === "slide" ? s.next.image : ""));
      if (nextWrap) nextWrap.hidden = !s.next;
      var card = nextCard(s.next);
      if (nextBox && card !== nextHtml) { nextBox.innerHTML = card; nextHtml = card; }
      if (notesBox) notesBox.innerHTML = s.notes ? esc(s.notes).replace(/\n/g, "<br>") : '<span class="live-none">' + de_en("keine", "none") + "</span>";
      if (pos && s.position) pos.textContent = s.position[0] ? de_en("Folie ", "Slide ") + s.position[0] + de_en(" von ", " of ") + s.position[1] : "";
    }
    // the presenter view: the border between the left column (the preview) and the slide is dragged (Ralf, 2026-10-23);
    // the width goes into --side-w, the browser keeps it, a double click goes back to the standard
    var grip = screen.querySelector("[data-resize]"), gridBox = screen.querySelector(".live-screen"), joinBox = screen.querySelector(".live-joinbox");
    if (grip && gridBox && joinBox) {
      var SIDE_KEY = "live-side-width", SIDE_MIN = 220;
      var sideMax = function () { return Math.max(SIDE_MIN, Math.round(gridBox.clientWidth * 0.6)); };
      var placeGrip = function () {
        var g = gridBox.getBoundingClientRect(), j = joinBox.getBoundingClientRect();
        grip.hidden = innerWidth <= 800;  // the columns stack: nothing to drag
        grip.style.left = (j.right - g.left + 20 - 6) + "px";  // in the middle of the 40 px gap, 12 px wide
      };
      var setSide = function (w, keep) {
        if (w) gridBox.style.setProperty("--side-w", Math.min(sideMax(), Math.max(SIDE_MIN, Math.round(w))) + "px");
        else gridBox.style.removeProperty("--side-w");
        if (keep) { try { if (w) localStorage.setItem(SIDE_KEY, String(Math.round(w))); else localStorage.removeItem(SIDE_KEY); } catch (e) { /* this page only */ } }
        placeGrip(); if (refit) refit();  // word clouds laid out anew for the new width
      };
      try { setSide(parseInt(localStorage.getItem(SIDE_KEY), 10) || 0, false); } catch (e) { placeGrip(); }
      grip.addEventListener("pointerdown", function (e) {
        if (e.button !== 0) return;
        e.preventDefault();
        grip.setPointerCapture(e.pointerId);
        var left = joinBox.getBoundingClientRect().left;
        grip.classList.add("is-dragging");
        var move = function (ev) { setSide(ev.clientX - left - 20, false); };
        var up = function () {
          grip.classList.remove("is-dragging");
          grip.removeEventListener("pointermove", move); grip.removeEventListener("pointerup", up); grip.removeEventListener("pointercancel", up);
          setSide(joinBox.getBoundingClientRect().width, true);
        };
        grip.addEventListener("pointermove", move); grip.addEventListener("pointerup", up); grip.addEventListener("pointercancel", up);
      });
      grip.addEventListener("dblclick", function () { setSide(0, true); });
      grip.addEventListener("keydown", function (e) {  // the arrows move it by 20 px, without passing on to the steering
        if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
        e.preventDefault(); e.stopPropagation();
        setSide(joinBox.getBoundingClientRect().width + (e.key === "ArrowRight" ? 20 : -20), true);
      });
      addEventListener("resize", placeGrip);
      if (window.ResizeObserver) new ResizeObserver(placeGrip).observe(gridBox);
    }
    var qaSide = screen.querySelector("[data-qa-side]"), qaSideList = screen.querySelector("[data-qa-side-list]"), qaSideHtml = "";
    var layout = screen.querySelector(".live-screen");
    // the presenter view: the questions from the audience below the slide (the standard) or beside it; below, the border
    // above them is dragged; the browser keeps both, a double click on the border goes back to the standard height
    var qaPlace = screen.querySelector("[data-qa-place]"), qaGrip = screen.querySelector("[data-qa-grip]");
    if (qaPlace && layout && qaSide) {
      var PLACE_KEY = "live-qa-place", HEIGHT_KEY = "live-qa-height", QA_MIN = 90;
      var below = true;
      try { below = localStorage.getItem(PLACE_KEY) !== "right"; } catch (e) { /* this page only */ }
      var setHeight = function (h, keep) {
        if (h) layout.style.setProperty("--qa-h", Math.min(Math.round(innerHeight * 0.7), Math.max(QA_MIN, Math.round(h))) + "px");
        else layout.style.removeProperty("--qa-h");
        if (keep) { try { if (h) localStorage.setItem(HEIGHT_KEY, String(Math.round(h))); else localStorage.removeItem(HEIGHT_KEY); } catch (e) { /* this page only */ } }
        if (refit) refit();
      };
      var setPlace = function (b, keep) {
        below = b;
        layout.classList.toggle("qa-below", b);
        qaPlace.textContent = b ? de_en("Fragen rechts", "Questions right") : de_en("Fragen unten", "Questions below");
        if (qaGrip) qaGrip.hidden = !b;
        if (keep) { try { localStorage.setItem(PLACE_KEY, b ? "below" : "right"); } catch (e) { /* this page only */ } }
        if (refit) refit();
      };
      try { setHeight(parseInt(localStorage.getItem(HEIGHT_KEY), 10) || 0, false); } catch (e) { /* this page only */ }
      setPlace(below, false);
      qaPlace.addEventListener("click", function () { setPlace(!below, true); });
      if (qaGrip) {
        qaGrip.addEventListener("pointerdown", function (e) {
          if (e.button !== 0) return;
          e.preventDefault();
          qaGrip.setPointerCapture(e.pointerId);
          qaGrip.classList.add("is-dragging");
          var bottom = qaSide.getBoundingClientRect().bottom;
          var move = function (ev) { setHeight(bottom - ev.clientY, false); };
          var up = function () {
            qaGrip.classList.remove("is-dragging");
            qaGrip.removeEventListener("pointermove", move); qaGrip.removeEventListener("pointerup", up); qaGrip.removeEventListener("pointercancel", up);
            setHeight(qaSide.getBoundingClientRect().height, true);
          };
          qaGrip.addEventListener("pointermove", move); qaGrip.addEventListener("pointerup", up); qaGrip.addEventListener("pointercancel", up);
        });
        qaGrip.addEventListener("dblclick", function () { setHeight(0, true); });
        qaGrip.addEventListener("keydown", function (e) {  // the arrows move it by 20 px, without passing on to the steering
          if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return;
          e.preventDefault(); e.stopPropagation();
          setHeight(qaSide.getBoundingClientRect().height + (e.key === "ArrowUp" ? 20 : -20), true);
        });
      }
    }
    function side(s) {  // the open questions from the audience beside the slide, while Q&A is on
      // the presenter view keeps them while they are on the screen too: its button switches the screen back
      var manage = !!(qaSide && qaSide.querySelector("[data-qa]"));
      var on = !!(s.qa && (manage || !(s.current && (s.current.kind === "qa" || s.current.kind === "review"))));
      if (qaSide) qaSide.hidden = !on;
      if (layout) layout.classList.toggle("has-qa", on);
      if (!on || !qaSideList) return;
      var html = (s.qa_side || []).length ? s.qa_side.map(function (q) {
        return (readonly ? "<li>" : '<li data-qa-done="' + q.id + '" tabindex="0" role="button" title="' + de_en("Klicken oder Enter: beantwortet", "Click or Enter: answered") + '">') +
          '<span class="live-qa-votes">▲ ' + q.votes + "</span><span>" + esc(q.text) + "</span></li>";
      }).join("") : '<li class="live-qa-none">' + esc(t("noquestions")) + "</li>";
      if (html !== qaSideHtml) { qaSideList.innerHTML = html; qaSideHtml = html; }
    }
    if (qaSide) qaSide.addEventListener("keydown", function (e) {
      var asked = e.target.closest("[data-qa-done]");
      if (asked && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); e.stopPropagation(); step("qa_answer", { qa: asked.dataset.qaDone }); }
    });
    if (qaSide) qaSide.addEventListener("click", function (e) {
      var asked = e.target.closest("[data-qa-done]");
      if (asked) step("qa_answer", { qa: asked.dataset.qaDone });
    });
    var shownNow = false;  // the results on the screen (for the key E; the ghost is the button)
    function draw(s) {
      if (s.qr && !zoom) zoomQr(); else if (!s.qr && zoom) unzoom();
      shownNow = !!s.show;
      presenterInfo(s);
      side(s);
      float(s.reactions);
      drawOutline(s);
      if (sectionLabel) sectionLabel.textContent = s.current && !s.open && s.current.kind !== "content" ? t("ended") : "";
      if (!s.current) { q.textContent = s.title; n.textContent = ""; put(""); if (corner) corner.hidden = true; return; }
      q.textContent = s.current.question;
      if (s.current.kind === "review") {  // the review at the end: its own counts per slide, no ghost
        n.textContent = ""; put(view(s.current));
        if (corner) corner.hidden = true;
        document.body.classList.remove("live-corner-on");
        return;
      }
      if (s.current.kind === "slide") {  // a page of a PDF: the picture alone, as large as the screen allows
        n.textContent = ""; put(s.current.image ? '<div class="live-slide-full"><img src="' + esc(s.current.image) + '" alt="' + esc(s.current.alt || "") + '"></div>'
          // no approved picture (e.g. a ZIP without its images): say so instead of a broken image (pre-test 2026-10-23)
          : '<div class="live-slide-full live-slide-missing"><p><strong>' + esc(s.current.alt || de_en("Folie", "Slide")) + "</strong></p><p>" +
            de_en("Das Bild dieser Folie fehlt. Bitte die PDF noch einmal hochladen.",
                  "The picture of this slide is missing. Please upload the PDF again.") + "</p></div>");
        if (corner) corner.hidden = true;
        return;
      }
      if (s.current.kind === "content") {  // a text or image slide: no answers, no ghost
        n.textContent = ""; put('<div class="live-content">' + s.current.html + "</div>"); if (corner) corner.hidden = true; return;
      }
      n.textContent = s.current.n + " " + (s.current.n === 1 ? t("answer") : t("answers")) +
        (s.current.mean != null ? " · " + t("mean") + " " + num(s.current.mean) : "") +
        (s.current.median != null ? " · " + t("median") + " " + num(s.current.median) : "") +
        (s.current.target != null && s.show ? " · " + t("right") + ": " + num(s.current.target) : "") +
        (s.left != null && s.open ? " · " + t("left").replace("{s}", s.left) : "");
      put(!s.show ? GHOST : view(s.current));
      if (corner) corner.hidden = readonly || !s.show;
      document.body.classList.toggle("live-corner-on", !!(corner && !corner.hidden));  // the brand beside the small ghost
    }
    // the projector shows the whole slide (Ralf, 2026-10-12): it is made smaller until it fits the window, to 60 % at
    // most; a wall or a word cloud that still does not fit keeps its most frequent texts and says "+ 34 weitere" below
    // them. Scaled as a picture (transform), so the words of a cloud keep their places. Not in the presenter view (it
    // scrolls) and not while the columns stack (participant.css, a narrow window scrolls)
    var fitBox = screen.querySelector("[data-fit]"), presenterView = !!screen.querySelector(".is-presenter");
    var FIT_MIN = 0.6, fitted = "", trimmed = false;
    function trim(target) {  // the most frequent texts that fit into the height `target`, the rest counted below them
      var list = out.querySelector(".live-wall, .live-cloud");
      if (!list || list.children.length < 2) return;
      var items = Array.prototype.slice.call(list.children), more = document.createElement("p");
      more.className = "live-more";
      list.parentNode.insertBefore(more, list.nextSibling);
      function show(k) {
        items.forEach(function (el, i) { if (i < k) list.appendChild(el); else if (el.parentNode) el.parentNode.removeChild(el); });
        more.textContent = t("more").replace("{n}", items.length - k);
        if (list.classList.contains("live-cloud")) layoutCloud(list);
        return fitBox.offsetHeight <= target;
      }
      if (!show(1)) { more.parentNode.removeChild(more); show(items.length); return; }  // not even one: all, the page scrolls
      var lo = 2, hi = items.length - 1, best = 1;
      while (lo <= hi) { var mid = (lo + hi) >> 1; if (show(mid)) { best = mid; lo = mid + 1; } else hi = mid - 1; }
      show(best);
      trimmed = true;
    }
    function fit(resized) {
      if (!fitBox || presenterView) { if (resized) layoutClouds(); return; }
      var key = out.dataset.html + "|" + q.textContent + "|" + n.textContent + "|" + innerWidth + "x" + innerHeight;
      if (key === fitted && !resized) return;
      fitted = key;
      fitBox.style.transform = fitBox.style.marginBottom = "";
      if (trimmed) { out.innerHTML = out.dataset.html || ""; trimmed = false; resized = true; }
      if (resized) layoutClouds(out);
      if (innerWidth <= 800 || out.querySelector(".live-slide-full")) return;  // a presentation slide sizes itself (participant.css)
      // the room down to the page's own space at the bottom (participant.css: the footer line, the ghost in the corner)
      var room = innerHeight - (parseFloat(getComputedStyle(screen).paddingBottom) || 0) - (fitBox.getBoundingClientRect().top + window.scrollY);
      var h = fitBox.offsetHeight;
      if (room <= 0 || h <= room) return;
      if (h * FIT_MIN > room) { trim(room / FIT_MIN); h = fitBox.offsetHeight; }
      var f = Math.max(FIT_MIN, Math.floor(room / h * 100) / 100);
      if (f < 1) { fitBox.style.transform = "scale(" + f + ")"; fitBox.style.marginBottom = -Math.ceil(h * (1 - f)) + "px"; }
    }
    refit = function () { fit(true); };
    // every request numbered (F17): an answer older than the last one asked for is dropped (it would put the
    // slide from before a click back for a moment); the timer asks only while none is on its way (at most 10 s),
    // a click asks at once
    var asked = 0, askedAt = 0;
    function ask(now) {
      if (!now && askedAt && Date.now() - askedAt < 10000) return;
      var mine = ++asked;
      askedAt = Date.now();
      fetch(screen.dataset.results, { credentials: "same-origin", cache: "no-store" }).then(function (r) { return r.json(); })
        // live_ink.js listens: the pen notes over a presentation slide follow every state
        .then(function (s) { if (mine === asked) { draw(s); fit(false); screen.dispatchEvent(new CustomEvent("live:state", { detail: s })); } })
        .catch(function () { /* ask again */ })
        .then(function () { if (mine === asked) askedAt = 0; });
    }
    ask(true);
    setInterval(function () { ask(false); }, (parseInt(screen.dataset.poll, 10) || 2) * 1000);
    // a step the server did not take (the sign-in expired, the right is gone): said at the bottom left for a while
    var stepError = screen.querySelector("[data-step-error]"), stepErrorTimer = null;
    function stepFailed(status) {
      if (!stepError) return;
      stepError.textContent = status === -1 ? de_en("Nicht übernommen: Die Anmeldung ist abgelaufen oder das Recht zum Steuern fehlt. Bitte die Seite neu laden.", "Not applied: the sign-in has expired or the right to steer is missing. Please reload the page.")
        : status === 403 ? de_en("Nicht übernommen: Steuern darf nur, wer die Abstimmung bearbeiten darf.", "Not applied: only people who may edit the vote can steer it.")
        : status ? de_en("Nicht übernommen (Fehler " + status + "). Bitte die Seite neu laden.", "Not applied (error " + status + "). Please reload the page.")
        : de_en("Nicht übernommen: keine Verbindung zum Server.", "Not applied: no connection to the server.");
      stepError.hidden = false;
      clearTimeout(stepErrorTimer);
      stepErrorTimer = setTimeout(function () { stepError.hidden = true; }, 8000);
    }
    // the presenter steps through the questions from the screen (buttons, arrow keys, a clicker)
    var stepping = false;
    function step(where, extra) {
      if (stepping || readonly) return;
      stepping = true;
      var body = new FormData();
      body.append("csrf_token", screen.dataset.csrf);
      body.append("action", where);
      Object.keys(extra || {}).forEach(function (k) { body.append(k, extra[k]); });
      fetch(screen.dataset.control, { method: "POST", body: body, credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
        // a step taken answers with JSON; a missing right or an expired sign-in is sent on to another page
        .then(function (r) { stepping = false; if (!r.ok || r.redirected) stepFailed(r.redirected ? -1 : r.status); ask(true); })
        .catch(function () { stepping = false; stepFailed(0); });
    }
    document.querySelectorAll("[data-step]").forEach(function (b) { b.addEventListener("click", function () { step(b.dataset.step); }); });
    // ↺ "Alles zurück" deletes the answers of this run: the first click arms it (red, a hint), a second within 4 s sends
    var restart = screen.querySelector("[data-restart]"), armed = null;
    if (restart) restart.addEventListener("click", function () {
      if (armed) { clearTimeout(armed); armed = null; restart.classList.remove("is-armed"); step("restart"); return; }
      restart.classList.add("is-armed");
      restart.dataset.title = restart.dataset.title || restart.title;
      restart.title = de_en("Nochmal klicken: alle Antworten dieses Durchgangs löschen und von vorn beginnen", "Click again: delete all answers of this run and begin again");
      armed = setTimeout(function () { armed = null; restart.classList.remove("is-armed"); restart.title = restart.dataset.title; }, 4000);
    });
    if (outline) outline.addEventListener("click", function (e) {
      var dot = e.target.closest("[data-pick]");
      if (dot) step("screen", { item: dot.dataset.pick });
      var start = e.target.closest("[data-release]");  // flow In Blöcken: only its button starts a block
      if (start) { step("release", { block: start.dataset.release }); return; }
      var box = !dot && e.target.closest("[data-block-first]");  // elsewhere in the box: the block from its start on the screen
      if (box) step("screen", { item: box.dataset.blockFirst });
    });
    if (toggleVoting) toggleVoting.addEventListener("click", function () { step(toggleVoting.dataset.state === "on" ? "close" : "reopen"); });
    if (toggleWall) toggleWall.addEventListener("click", function () { step(toggleWall.dataset.state === "on" ? "wall_off" : "wall_on"); });
    if (toggleQa) toggleQa.addEventListener("click", function () { step(toggleQa.dataset.state === "on" ? "qa_screen_off" : "qa_screen_on"); });
    if (toggleBoard) toggleBoard.addEventListener("click", function () { step(toggleBoard.dataset.state === "on" ? "board_off" : "board_on"); });
    if (toggleCorner) toggleCorner.addEventListener("click", function () { step(toggleCorner.dataset.state === "on" ? "corner_off" : "corner_on"); });
    if (toggleReview) toggleReview.addEventListener("click", function () { step(toggleReview.dataset.state === "on" ? "review_off" : "review_on"); });
    out.addEventListener("click", function (e) {  // a word or a post: hidden from the screen (back under Präsentieren → Moderation)
      var text = e.target.closest("[data-hide]");
      if (text && text.dataset.hide) step("hide", { key: text.dataset.hide });
      var asked = e.target.closest("[data-qa-done]");
      if (asked) step("qa_answer", { qa: asked.dataset.qaDone });
      var pick = e.target.closest("[data-review-pick]");  // the review: back to that slide
      if (pick) step("screen", { item: pick.dataset.reviewPick });
      var sortBy = e.target.closest("[data-review-sort]");
      if (sortBy) step("review_sort", { by: sortBy.dataset.reviewSort });
    });
    if (toggleResults) toggleResults.addEventListener("click", function () { step(toggleResults.dataset.state === "on" ? "results_off" : "results_on"); });
    out.addEventListener("click", function (e) { if (e.target.closest('[data-ghost="show"]')) step("results_on"); });
    if (corner) corner.addEventListener("click", function () { step("results_off"); });

    document.addEventListener("keydown", function (e) {
      if (readonly || e.altKey || e.ctrlKey || e.metaKey) return;
      // a focused control keeps its own keys: the Space bar presses the button, arrows move in a field or a list; in
      // a field no letter is a shortcut (code review 2026-10-15, I7)
      var at = e.target && e.target.closest ? e.target : null;
      if (at && at.closest("input, textarea, select, [contenteditable]")) return;
      if (at && at.closest("button, a, [role=button]") && [" ", "ArrowRight", "ArrowLeft", "ArrowUp", "ArrowDown"].indexOf(e.key) >= 0) return;
      if (["ArrowRight", "PageDown", " "].indexOf(e.key) >= 0) { e.preventDefault(); step("next"); }
      else if (["ArrowLeft", "PageUp"].indexOf(e.key) >= 0) { e.preventDefault(); step("prev"); }
      else if ((e.key === "n" || e.key === "N") && nextBlock && !nextBlock.hidden) { e.preventDefault(); step("next_block"); }
      else if ((e.key === "s" || e.key === "S") && toggleVoting && !toggleVoting.hidden) { e.preventDefault(); toggleVoting.click(); }
      else if (e.key === "e" || e.key === "E") { e.preventDefault(); step(shownNow ? "results_off" : "results_on"); }  // ghost: click it, or E
      else if ((e.key === "q" || e.key === "Q") && toggleQa && !toggleQa.hidden) { e.preventDefault(); toggleQa.click(); }
      else if ((e.key === "r" || e.key === "R") && toggleBoard && !toggleBoard.hidden) { e.preventDefault(); toggleBoard.click(); }
      else if ((e.key === "u" || e.key === "U") && toggleReview && !toggleReview.hidden) { e.preventDefault(); toggleReview.click(); }
      else if ((e.key === "k" || e.key === "K") && toggleCorner && !toggleCorner.hidden) { e.preventDefault(); toggleCorner.click(); }
    });
  }
  // the word clouds of the results page (surveys/results.html): drawn and placed as on the screen, again when a fold
  // around one opens (closed it has no width)
  var resClouds = document.querySelectorAll("[data-res-cloud]");
  resClouds.forEach(function (box) { try { box.innerHTML = words(JSON.parse(box.dataset.resCloud)); } catch (e) { box.innerHTML = ""; } });
  if (resClouds.length) {
    layoutClouds();
    document.addEventListener("toggle", function (e) { if (e.target.open) layoutClouds(e.target); }, true);
  }
  // "Zusammenführen" of an earlier run (Präsentieren): the question before it names the run chosen as the target
  document.querySelectorAll("form[data-confirm-template] select[data-merge-into]").forEach(function (sel) {
    sel.addEventListener("change", function () {
      var form = sel.form, name = sel.options[sel.selectedIndex].text;
      form.dataset.confirm = form.dataset.confirmTemplate.split("{into}").join(name);
    });
  });
})();
