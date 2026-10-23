// The choice of the microphone and a level meter for the recording of an interview (interview.js on the tablet,
// recorder.js on the second device). The browser lists its audio inputs (their names only once the page was allowed
// to use the microphone); the choice is kept per device in this browser. "Testen" opens the chosen microphone for the
// level only, so it can be checked before the interview. Loaded before interview.js and recorder.js.
(function () {
  // the page language of the researcher (data-ui-lang on <body> of the interview view, else <html lang>; app/lang.py)
  function de_en(de, en) { return (document.body.getAttribute("data-ui-lang") || document.documentElement.lang) === "en" ? en : de; }
  var KEY = "askurious-mic", OLD_KEY = "queryous-mic";  // OLD_KEY: the name before the rename to Askurious
  var media = navigator.mediaDevices;
  function stored() { try { return localStorage.getItem(KEY) || localStorage.getItem(OLD_KEY) || ""; } catch (e) { return ""; } }
  function store(id) { try { if (id) localStorage.setItem(KEY, id); else localStorage.removeItem(KEY); } catch (e) { /* no storage */ } }

  function attach(box) {
    var select = box.querySelector("[data-mic-select]"), level = box.querySelector("[data-mic-level]"), test = box.querySelector("[data-mic-test]");
    var bar = level ? level.querySelector("span") : null, meter = null, testStream = null;
    function fill() {
      if (!select || !media || !media.enumerateDevices) return;
      media.enumerateDevices().then(function (all) {
        var inputs = all.filter(function (d) { return d.kind === "audioinput" && d.deviceId !== "communications"; });
        var want = select.value || stored();
        select.textContent = "";
        var standard = document.createElement("option");
        standard.value = ""; standard.textContent = de_en("Standard-Mikrofon", "Default microphone");
        select.appendChild(standard);
        inputs.forEach(function (d, n) {
          if (d.deviceId === "default" || !d.deviceId) return;  // the browser's default: the first option
          var o = document.createElement("option");
          o.value = d.deviceId; o.textContent = d.label || de_en("Mikrofon ", "Microphone ") + (n + 1);
          select.appendChild(o);
        });
        select.value = Array.prototype.some.call(select.options, function (o) { return o.value === want; }) ? want : "";
        select.hidden = select.options.length < 2;  // only the default: nothing to choose
      }).catch(function () { select.hidden = true; });
    }
    function open() {  // the chosen microphone, mono (speech; data audit I-23); one that is gone falls back to the default
      var id = select ? select.value : stored();
      var ask = id ? media.getUserMedia({ audio: { deviceId: { exact: id }, channelCount: 1 } }).catch(function () { return media.getUserMedia({ audio: { channelCount: 1 } }); })
                   : media.getUserMedia({ audio: { channelCount: 1 } });
      return ask.then(function (s) { fill(); return s; });
    }
    function show(stream) {  // the level of a stream until stop()
      hide();
      var Ctx = window.AudioContext || window.webkitAudioContext;
      if (!bar || !Ctx) return;
      var ctx = new Ctx(), analyser = ctx.createAnalyser(), data = new Uint8Array(512), raf = null;
      analyser.fftSize = 512;
      if (ctx.resume) ctx.resume();
      ctx.createMediaStreamSource(stream).connect(analyser);
      level.hidden = false;
      (function draw() {
        analyser.getByteTimeDomainData(data);
        var sum = 0;
        for (var i = 0; i < data.length; i++) { var v = (data[i] - 128) / 128; sum += v * v; }
        var rms = Math.sqrt(sum / data.length), pct = Math.min(100, Math.round(Math.sqrt(rms) * 140));
        bar.style.width = pct + "%";
        level.classList.toggle("is-loud", pct > 92);
        raf = requestAnimationFrame(draw);
      })();
      meter = function () { cancelAnimationFrame(raf); ctx.close(); bar.style.width = "0"; level.hidden = true; };
    }
    function hide() { if (meter) { meter(); meter = null; } }
    function endTest() {
      if (!testStream) return;
      testStream.getTracks().forEach(function (t) { t.stop(); });
      testStream = null; hide();
      if (test) test.textContent = de_en("Testen", "Test");
    }
    if (select) select.addEventListener("change", function () {
      store(select.value);
      if (testStream) { endTest(); test.click(); }  // a running test follows the choice
    });
    if (test) test.addEventListener("click", function () {
      if (testStream) { endTest(); return; }
      open().then(function (s) { testStream = s; test.textContent = de_en("Test beenden", "End test"); show(s); })
            .catch(function () { test.textContent = de_en("kein Zugriff", "no access"); });
    });
    if (media && media.addEventListener) media.addEventListener("devicechange", fill);
    fill();
    return {
      open: function () { endTest(); return open(); },  // for the recording; the test gives way
      show: show, hide: hide,
      lock: function (on) { if (select) select.disabled = on; if (test) test.disabled = on; }
    };
  }
  window.AskuriousMic = { attach: attach };
})();
