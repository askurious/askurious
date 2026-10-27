// The page of a coding link (app/transcript_coding_links.py, templates/transcript_coding/start.html): the key stands
// after "#" in the link, so that it reaches no server or proxy log; it goes once in the body of the POST and leaves
// the address bar. As interviewer.js, with a key of its own.
(function () {
  var form = document.querySelector("[data-coding-link-start]");
  if (!form) {
    try { sessionStorage.removeItem("ck-key"); } catch (e) { /* nothing kept */ }
    return;
  }
  var field = form.querySelector("[data-coding-link-key]");
  var key = (window.location.hash || "").replace(/^#/, "");
  if (key) {
    try { sessionStorage.setItem("ck-key", key); } catch (e) { /* private mode: the field holds it */ }
    history.replaceState(null, "", window.location.pathname + window.location.search);
  } else {
    try { key = sessionStorage.getItem("ck-key") || ""; } catch (e) { key = ""; }
  }
  field.value = key;
  if (!key) form.querySelector("[data-coding-link-nokey]").hidden = false;
})();
