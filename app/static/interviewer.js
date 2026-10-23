// The page of an interviewer link (app/interviewer.py, templates/interviewer/start.html): the key stands after "#" in
// the link, so that it reaches no server or proxy log; it goes once in the body of the POST and leaves the address bar.
(function () {
  var form = document.querySelector("[data-interviewer-start]");
  if (!form) {  // the list of interviews: the link worked, the key is no longer needed in this tab
    try { sessionStorage.removeItem("iv-key"); } catch (e) { /* nothing kept */ }
    return;
  }
  var field = form.querySelector("[data-interviewer-key]");
  var key = (window.location.hash || "").replace(/^#/, "");
  if (key) {
    try { sessionStorage.setItem("iv-key", key); } catch (e) { /* private mode: the field holds it */ }
    history.replaceState(null, "", window.location.pathname + window.location.search);
  } else {
    try { key = sessionStorage.getItem("iv-key") || ""; } catch (e) { key = ""; }
  }
  field.value = key;
  if (!key) form.querySelector("[data-interviewer-nokey]").hidden = false;
})();
