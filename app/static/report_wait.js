(function () {
  "use strict";
  var panel = document.querySelector("[data-report-wait]");
  if (!panel) return;
  var url = panel.dataset.url;
  var status = panel.querySelector('[role="status"]');
  var checked = panel.querySelector('[data-report-checked]');
  var timer, pending = false;
  var check = panel.querySelector('[data-report-check]');
  check.addEventListener('click', function (event) {
    if (check.dataset.ready) return;
    event.preventDefault(); clearTimeout(timer); poll();
  });
  function poll() {
    if (pending) return;
    pending = true;
    var controller = new AbortController();
    var timeout = setTimeout(function () { controller.abort(); }, 15000);
    fetch(url, {headers: {"X-Report-Poll": "1"}, credentials: "same-origin", signal: controller.signal}).then(function (response) {
      clearTimeout(timeout); pending = false;
      if (response.status === 202 || response.headers.get("X-Report-Ready") === "1") {
        checked.textContent = de_en("Zuletzt bestätigt: ", "Last confirmed: ") + new Date().toLocaleTimeString();
      }
      if (response.status === 202) {
        status.textContent = de_en("Die Auswertung wird vorbereitet.", "The report is being prepared.");
        timer = setTimeout(poll, 1000); return;
      }
      if (response.headers.get("X-Report-Ready") === "1") {
        if (panel.dataset.download === "1") {
          var link = check;
          link.dataset.ready = '1';
          link.href = url;
          panel.querySelector("h1").textContent = de_en("Download bereit", "Download ready");
          document.title = de_en("Download bereit", "Download ready") + " · " + panel.dataset.title;
          panel.querySelector("[role=status]").textContent = de_en("Die Datei ist bereit. Falls der Download nicht startet, klicken Sie auf „Herunterladen“.", "The file is ready. If the download does not start, click “Download”.");
          link.textContent = de_en("Herunterladen", "Download");
          link.click();
        } else { window.location.replace(url); }
      } else { window.location.replace(url); }
    }).catch(function () {
      clearTimeout(timeout); pending = false;
      status.textContent = de_en("Der Stand konnte nicht abgerufen werden. Die Berechnung kann auf dem Server weiterlaufen. Die Seite versucht es erneut; mit „Stand prüfen“ können Sie sofort nachsehen.", "Progress could not be retrieved. The calculation may still be running on the server. This page will retry; choose “Check progress” to check now.");
      timer = setTimeout(poll, 5000);
    });
  }
  timer = setTimeout(poll, 1000);
})();
