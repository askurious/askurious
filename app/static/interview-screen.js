// The interviewee's own screen (app/interview_screen.py, templates/interview/screen.html). Without a bound device the
// key after "#" goes once in the body of a POST and leaves the address bar, so that it reaches no server log. Bound:
// the page asks every few seconds what it shows ("wait", "task-<id>-<rev>", "ended") and loads anew when that changes;
// the task's answer is sent without leaving the page and may be sent again while the task stays released.
(function () {
  var box = document.querySelector("[data-ivs]");
  if (!box) return;
  var open = box.querySelector("[data-ivs-open]");
  if (open) {
    var key = (window.location.hash || "").replace(/^#/, "");
    if (key) {
      history.replaceState(null, "", window.location.pathname);
      open.querySelector("[data-ivs-key]").value = key;
      open.querySelector("[data-ivs-opening]").hidden = false;
      open.submit();
    } else {
      open.querySelector("[data-ivs-nokey]").hidden = false;
    }
    return;
  }
  var current = box.getAttribute("data-key"), every = (parseInt(box.getAttribute("data-poll"), 10) || 2) * 1000;
  function ended() {
    var t = document.querySelector("template[data-ivs-ended]");
    box.innerHTML = t ? t.innerHTML : "";
  }
  if (current === "ended") return;
  var timer = null;
  function ask() {
    fetch(box.getAttribute("data-state"), { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (d && d.key && d.key !== current) {
          if (d.key === "ended") { ended(); return; }
          window.location.reload();
          return;
        }
        timer = setTimeout(ask, document.hidden ? every * 3 : every);
      })
      .catch(function () { timer = setTimeout(ask, every * 2); });
  }
  timer = setTimeout(ask, every);
  var form = box.querySelector("[data-ivs-form]");
  if (!form) return;
  var note = form.querySelector("[data-ivs-note]");
  function say(text, bad) {
    note.textContent = text;
    note.hidden = !text;
    note.classList.toggle("p-error", !!bad);
  }
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    form.querySelectorAll(".p-item.has-error").forEach(function (el) { el.classList.remove("has-error"); });
    var button = form.querySelector("button[type=submit]");
    if (button) button.disabled = true;
    fetch(form.getAttribute("action"), { method: "POST", body: new FormData(form), credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) { return { status: r.status, d: d || {} }; }); })
      .then(function (res) {
        if (button) button.disabled = false;
        if (res.d.ok) { say(box.getAttribute("data-sent")); return; }
        if (res.d.error === "ended") { ended(); return; }
        if (res.d.error === "closed") { say(box.getAttribute("data-closed"), true); return; }
        if (res.d.error === "invalid") {
          var first = null;
          Object.keys(res.d.errors || {}).forEach(function (id) {
            var item = form.querySelector("#i" + id);
            if (item) { item.classList.add("has-error"); first = first || res.d.errors[id]; }
          });
          say(first || box.getAttribute("data-fail"), true);
          return;
        }
        if (res.d.error === "empty") { say(box.getAttribute("data-empty"), true); return; }
        say(box.getAttribute("data-fail"), true);
      })
      .catch(function () { if (button) button.disabled = false; say(box.getAttribute("data-fail"), true); });
  });
})();
