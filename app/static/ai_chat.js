// KI-Chat (app/items/ai_chat.py, app/ai_chat.py): each message goes to the server, which asks the model of the survey
// and stores both; the answer is added to the log. Enter sends, Shift+Enter makes a new line. Nothing of the chat goes
// with the page itself: the server reads the stored messages when the page is sent.
(function () {
  "use strict";

  function bubble(log, mine, who, text) {
    var box = document.createElement("div");
    box.className = "p-aichat-msg " + (mine ? "p-aichat-me" : "p-aichat-ai");
    var label = document.createElement("span");
    label.className = "p-aichat-who";
    label.textContent = who;
    var p = document.createElement("p");
    p.dir = "auto";
    p.textContent = text;  // never as HTML: the model's answer is text
    box.appendChild(label);
    box.appendChild(p);
    log.appendChild(box);
    log.scrollTop = log.scrollHeight;
    return box;
  }

  function setup(root) {
    var url = root.getAttribute("data-aichat-url");
    var log = root.querySelector("[data-aichat-log]");
    var input = root.querySelector("[data-aichat-input]");
    var send = root.querySelector("[data-aichat-send]");
    var form = root.querySelector("[data-aichat-form]");
    var status = root.querySelector("[data-aichat-status]");
    var crisis = root.querySelector("[data-aichat-crisis]");
    var page = root.closest("form");
    if (!url || !input || !send) return;
    var busy = false;

    function csrf() {
      var field = page && page.querySelector("input[name=csrf_token]");
      return field ? field.value : "";
    }

    function left(n) {
      if (n <= 0) { form.hidden = true; return; }
      status.textContent = (root.dataset.textLeft || "").split("{n}").join(n);
    }

    function submit() {
      var text = (input.value || "").trim();
      if (!text || busy) return;
      busy = true; send.disabled = true; input.disabled = true;
      bubble(log, true, root.dataset.textYou, text);
      input.value = "";
      status.textContent = root.dataset.textWait || "";
      var controller = new AbortController();
      var timer = setTimeout(function () { controller.abort(); }, 75000);
      fetch(url, { method: "POST", credentials: "same-origin", signal: controller.signal,
                   headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
                   body: JSON.stringify({ message: text }) })
        .then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) { return [r.status, d]; }); })
        .then(function (res) {
          var d = res[1] || {};
          if (d.crisis && crisis) crisis.hidden = false;
          if (d.reply) bubble(log, false, root.dataset.textAi, d.reply);
          else if (d.refused) bubble(log, false, root.dataset.textAi, d.refused);
          if (typeof d.left === "number") left(d.left);
          if (d.error && d.error !== "page") status.textContent = d.error;
          else if (d.error) status.textContent = root.dataset.textFailed || "";
          if (!d.reply && !d.refused && !d.error) status.textContent = root.dataset.textFailed || "";
        })
        .catch(function () { status.textContent = root.dataset.textFailed || ""; })
        .finally(function () {
          clearTimeout(timer); busy = false; send.disabled = false; input.disabled = false; input.focus();
        });
    }

    send.addEventListener("click", submit);
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(); }
    });
  }

  function start() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-aichat]"), setup);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
