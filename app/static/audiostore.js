// The pieces of an interview recording that did not reach the server yet, kept in the browser (IndexedDB) until the
// server confirms them (data audit I-19): an empty battery, a tab the operating system ends or a reload then costs
// nothing; the page sends what is left the next time it opens. Used by interview.js (the interview view) and
// recorder.js (the second device). Without IndexedDB (a private window, an old browser) the pieces live in memory
// only, as before. AskuriousAudioStore.open(scope) gives {put(piece), drop(piece), left()}; scope names the interview.
// open(scope, cipher): the offline mode (interview-offline.js) keeps the pieces encrypted; cipher.seal(blob) and
// cipher.open(blob) give promises of the encrypted and the plain blob.
(function () {
  // the name from before the rename to Askurious stays: a new one would lose the pieces still waiting in a browser
  var NAME = "queryous-audio", STORE = "pieces";
  function none() {
    return { put: function () {}, drop: function () {}, left: function () { return Promise.resolve([]); }, clear: function () { return Promise.resolve(); } };
  }
  function open(scope, cipher) {
    return new Promise(function (resolve) {
      var req;
      try {
        if (!scope || !window.indexedDB) { resolve(none()); return; }
        req = indexedDB.open(NAME, 1);
      } catch (e) { resolve(none()); return; }
      req.onupgradeneeded = function () {
        try { req.result.createObjectStore(STORE, { keyPath: "key" }).createIndex("scope", "scope"); } catch (e) { /* kept */ }
      };
      req.onerror = req.onblocked = function () { resolve(none()); };
      req.onsuccess = function () {
        var db = req.result;
        function pieces(mode) { var tx = db.transaction(STORE, mode); tx.onerror = function (e) { if (e.preventDefault) e.preventDefault(); }; return tx.objectStore(STORE); }
        function key(p) { return scope + "|" + p.rec.id + "|" + p.seq; }
        resolve({
          put: function (p) {
            function write(blob) {
              try {
                pieces("readwrite").put({ key: key(p), scope: scope, rec: { id: p.rec.id, part: p.rec.part, began: p.rec.began },
                                          seq: p.seq, start: p.start, type: p.type, blob: blob, sealed: !!cipher, at: p.at || Date.now() });
              } catch (e) { /* full or gone: the piece stays in memory */ }
            }
            if (cipher) cipher.seal(p.blob).then(write, function () { /* not sealed: never written plain */ });
            else write(p.blob);
          },
          drop: function (p) { try { pieces("readwrite").delete(key(p)); } catch (e) { /* gone */ } },
          clear: function () {  // every piece of this scope (the offline mode, after the sync or a deletion)
            return new Promise(function (ok) {
              try {
                var r = pieces("readwrite").index("scope").getAllKeys(scope);
                r.onsuccess = function () { var st = pieces("readwrite"); (r.result || []).forEach(function (k) { st.delete(k); }); ok(); };
                r.onerror = function () { ok(); };
              } catch (e) { ok(); }
            });
          },
          left: function () {  // the pieces of earlier visits in their order; one recording shares one rec object
            return new Promise(function (ok) {
              try {
                var r = pieces("readonly").index("scope").getAll(scope);
                r.onsuccess = function () {
                  var recs = {};
                  var list = (r.result || []).sort(function (a, b) { return a.at - b.at || a.seq - b.seq; }).map(function (p) {
                    p.rec = recs[p.rec.id] || (recs[p.rec.id] = p.rec);
                    return p;
                  });
                  if (!list.some(function (p) { return p.sealed; })) { ok(list); return; }
                  // sealed pieces: opened with this cipher; a piece it cannot open (another PIN) stays out
                  Promise.all(list.map(function (p) {
                    if (!p.sealed) return p;
                    if (!cipher) return null;
                    return cipher.open(p.blob, p.type).then(function (b) { p.blob = b; p.sealed = false; return p; }, function () { return null; });
                  })).then(function (out) { ok(out.filter(Boolean)); });
                };
                r.onerror = function () { ok([]); };
              } catch (e) { ok([]); }
            });
          }
        });
      };
    });
  }
  // Kept for good (Ralf 2026-10-22, D): the browser may otherwise clear this storage when the device runs short or
  // (Safari) after some days without a visit, and pieces not sent yet would be gone. Asked once per page; a browser
  // that says no or cannot keep it keeps the pieces as before.
  var asked = null;
  function persist() {
    if (!asked) asked = (navigator.storage && navigator.storage.persist ? navigator.storage.persist() : Promise.resolve(false))
      .catch(function () { return false; });
    return asked;
  }
  // true when the device has less than about 50 MB left for this site (some 3 hours of recording at 32 kbit/s)
  function low() {
    if (!navigator.storage || !navigator.storage.estimate) return Promise.resolve(false);
    return navigator.storage.estimate().then(function (e) { return !!(e && e.quota && e.quota - (e.usage || 0) < 50 * 1024 * 1024); })
      .catch(function () { return false; });
  }
  // Several waiting pieces in one request (offline J, app/interview_audio.upload_batch): after a long time without a
  // connection the queue catches up with a minute of recording per request. front(queue) gives the pieces at the front
  // that belong together (one recording, seq without a gap, at most 12 and 1.5 MB), or null when fewer than two;
  // sendBatch(url, csrf, list, meta) posts them, meta(piece) names part, seq, rec and start or age of each.
  function front(queue) {
    var first = queue[0], out = [], size = 0;
    for (var i = 0; first && i < queue.length && out.length < 12; i++) {
      var q = queue[i];
      if (q.rec !== first.rec || q.type !== first.type || (out.length && q.seq !== out[out.length - 1].seq + 1)) break;
      if (size + q.blob.size > 1536 * 1024) break;
      out.push(q); size += q.blob.size;
    }
    return out.length >= 2 ? out : null;
  }
  function sendBatch(url, csrf, list, meta) {
    var body = new FormData();
    body.append("meta", JSON.stringify(list.map(meta)));
    list.forEach(function (p, n) { body.append("p" + n, new Blob([p.blob], { type: p.type }), "p" + n); });
    return fetch(url, { method: "POST", body: body, credentials: "same-origin", headers: { "X-CSRFToken": csrf || "", "X-Requested-With": "fetch" } });
  }
  // The emergency exit (offline L): the pieces still waiting, as one file the person saves; read in again on the page
  // "Aufnahme hochladen" of the interview (audio-upload.js), which sends them as if they came from this page.
  // Format: {"format": "askurious-audio-pieces", "version": 1, "interview": <id>, "pieces": [{part, seq, rec, start
  // or age, type, data (base64)}]}; age is counted to the moment of saving.
  function save(pieces, interview, name, meta) {
    var now = Date.now();
    return Promise.all(pieces.map(function (p) {
      return new Promise(function (ok, fail) {
        var r = new FileReader();
        r.onload = function () { ok(Object.assign(meta(p, now), { type: p.type, data: String(r.result).split(",")[1] || "" })); };
        r.onerror = fail;
        r.readAsDataURL(p.blob);
      });
    })).then(function (list) {
      var blob = new Blob([JSON.stringify({ format: "askurious-audio-pieces", version: 1, interview: interview, pieces: list })], { type: "application/json" });
      var a = document.createElement("a");
      a.href = URL.createObjectURL(blob); a.download = name;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(function () { URL.revokeObjectURL(a.href); }, 60000);
      return list.length;
    });
  }
  window.AskuriousAudioStore = { open: open, persist: persist, low: low, front: front, sendBatch: sendBatch, save: save };
})();
