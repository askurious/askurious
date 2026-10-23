// The field kit of the offline mode (interview/_field_kit.html, app/interview_offline.py; concept "Konzept
// Offline-Modus für Interviews", Ralf 2026-10-22, E1-E8 as recommended). It runs before interview.js and hands it the
// interview to conduct (AskuriousOffline.ready); interview.js then keeps its state here instead of in the tab.
// - Unlock: a PIN of this device (E3 a). PBKDF2 (600,000 rounds, SHA-256) makes an AES-GCM key from it that lives in
//   this page only; everything kept (the pack with its deletion codes, each interview, each audio piece) is sealed with
//   it. A forgotten PIN leaves the data unreadable; there is no master key (E4 a).
// - Prepare (with a network): the server reserves places (pid, identifier, deletion code) for this device.
// - Without a network: a free place becomes an interview after the consent step; "Interview beenden" shows its
//   deletion code; then the next one.
// - Sync (with a network, by itself or "Alles abgleichen"): each interview as a whole (app/interview_offline.sync),
//   its recording piece by piece, its end; then the device deletes its copy.
// - interview.js runs once per page, so the next interview starts from a fresh page: the key of this tab is kept in
//   IndexedDB for the tab (sessionStorage names it) and dropped by "Sperren", a closed tab or after 12 hours.
(function () {
  "use strict";
  var root = document.querySelector("[data-interview][data-offline]");
  if (!root) return;
  function de_en(de, en) { return (document.body.getAttribute("data-ui-lang") || document.documentElement.lang) === "en" ? en : de; }
  var kit = root.querySelector("[data-kit]"), stateEl = kit.querySelector("[data-kit-state]");
  var lockForm = kit.querySelector("[data-kit-lock]"), openBox = kit.querySelector("[data-kit-open]");
  var slotsEl = kit.querySelector("[data-kit-slots]"), slotTpl = kit.querySelector("[data-kit-slot]");
  var newForm = kit.querySelector("[data-kit-new]"), codeBox = kit.querySelector("[data-kit-code]");
  var guide = root.getAttribute("data-guide"), csrf = root.getAttribute("data-csrf");
  var packUrl = root.getAttribute("data-pack-url"), syncUrl = root.getAttribute("data-sync-url");
  var ROUNDS = 600000, TAB = "askurious-offline-tab", NEXT = "askurious-offline-next", enc = new TextEncoder(), dec = new TextDecoder();
  var key = null, pack = null, current = null, syncing = false, resolveReady;

  function say(text, bad) { stateEl.textContent = text || ""; stateEl.classList.toggle("danger-text", !!bad); }
  function show(el, on) { el.hidden = !on; }

  // --- storage (IndexedDB "askurious-offline": meta = salt, check, device; packs; interviews) -----------------------
  var dbp = null;
  function db() {
    if (!dbp) dbp = new Promise(function (ok, fail) {
      if (!window.indexedDB) { fail(new Error("no IndexedDB")); return; }
      var req = indexedDB.open("askurious-offline", 1);
      req.onupgradeneeded = function () {
        ["meta", "packs", "interviews"].forEach(function (n) { if (!req.result.objectStoreNames.contains(n)) req.result.createObjectStore(n); });
      };
      req.onsuccess = function () { ok(req.result); };
      req.onerror = req.onblocked = function () { fail(req.error || new Error("blocked")); };
    });
    return dbp;
  }
  function tx(store, mode, fn) {
    return db().then(function (d) {
      return new Promise(function (ok, fail) {
        var t = d.transaction(store, mode), r = fn(t.objectStore(store));
        t.oncomplete = function () { ok(r && "result" in r ? r.result : undefined); };
        t.onerror = t.onabort = function () { fail(t.error); };
      });
    });
  }
  function get(store, k) { return tx(store, "readonly", function (s) { return s.get(k); }); }
  function put(store, k, v) { return tx(store, "readwrite", function (s) { return s.put(v, k); }); }
  function del(store, k) { return tx(store, "readwrite", function (s) { return s.delete(k); }); }
  function all(store) {
    return tx(store, "readonly", function (s) { return s.getAllKeys(); }).then(function (keys) {
      return Promise.all((keys || []).map(function (k) { return get(store, k).then(function (v) { return [k, v]; }); }));
    });
  }

  // --- crypto ----------------------------------------------------------------------------------------------------------
  function derive(pin, salt) {
    return crypto.subtle.importKey("raw", enc.encode(pin), "PBKDF2", false, ["deriveKey"]).then(function (base) {
      return crypto.subtle.deriveKey({ name: "PBKDF2", salt: salt, iterations: ROUNDS, hash: "SHA-256" }, base,
                                     { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
    });
  }
  function sealBytes(bytes) {
    var iv = crypto.getRandomValues(new Uint8Array(12));
    return crypto.subtle.encrypt({ name: "AES-GCM", iv: iv }, key, bytes).then(function (c) { return { iv: iv, data: new Uint8Array(c) }; });
  }
  function openBytes(sealed) { return crypto.subtle.decrypt({ name: "AES-GCM", iv: sealed.iv }, key, sealed.data).then(function (b) { return new Uint8Array(b); }); }
  function seal(obj) { return sealBytes(enc.encode(JSON.stringify(obj))); }
  function open(sealed) { return openBytes(sealed).then(function (b) { return JSON.parse(dec.decode(b)); }); }
  var cipher = {  // for audiostore.js: a piece as a sealed blob ([12 bytes iv][ciphertext]) and back
    seal: function (blob) {
      return blob.arrayBuffer().then(function (buf) { return sealBytes(new Uint8Array(buf)); })
        .then(function (s) { return new Blob([s.iv, s.data], { type: "application/octet-stream" }); });
    },
    open: function (blob, type) {
      return blob.arrayBuffer().then(function (buf) {
        var all_ = new Uint8Array(buf);
        return openBytes({ iv: all_.slice(0, 12), data: all_.slice(12) });
      }).then(function (b) { return new Blob([b], { type: type || "audio/webm" }); });
    }
  };

  // --- unlock ----------------------------------------------------------------------------------------------------------
  function unlock(pin, first) {
    return get("meta", "lock").then(function (lock) {
      if (!lock) {
        var salt = crypto.getRandomValues(new Uint8Array(16));
        return derive(pin, salt).then(function (k) { key = k; return seal("askurious"); })
          .then(function (check) { return put("meta", "lock", { salt: salt, check: check }); });
      }
      return derive(pin, lock.salt).then(function (k) { key = k; return open(lock.check); }).then(function (v) {
        if (v !== "askurious") throw new Error("pin");
      }, function () { key = null; throw new Error("pin"); });
    });
  }
  function remember() {  // this tab stays unlocked across its own reloads
    var tab = Math.random().toString(36).slice(2);
    try { sessionStorage.setItem(TAB, tab); } catch (e) { return Promise.resolve(); }
    return put("meta", "session", { tab: tab, key: key, at: Date.now() });
  }
  function recall() {
    var tab = null;
    try { tab = sessionStorage.getItem(TAB); } catch (e) { /* none */ }
    return get("meta", "session").then(function (s) {
      if (s && tab && s.tab === tab && Date.now() - s.at < 12 * 3600 * 1000) { key = s.key; return true; }
      if (s) return del("meta", "session").then(function () { return false; });
      return false;
    });
  }
  function lockNow() {
    try { sessionStorage.removeItem(TAB); sessionStorage.removeItem(NEXT); } catch (e) { /* none */ }
    return del("meta", "session").then(function () { location.reload(); });
  }
  function device() {
    return get("meta", "device").then(function (d) {
      if (d) return d;
      var id = Array.prototype.map.call(crypto.getRandomValues(new Uint8Array(12)), function (b) { return ("0" + b.toString(16)).slice(-2); }).join("");
      return put("meta", "device", id).then(function () { return id; });
    });
  }

  // --- the pack and the interviews --------------------------------------------------------------------------------------
  function loadPack() { return get("packs", guide).then(function (s) { return s ? open(s) : null; }); }
  function savePack() { return pack ? seal(pack).then(function (s) { return put("packs", guide, s); }) : del("packs", guide); }
  function ivKey(pid) { return guide + "|" + pid; }
  function loadIv(pid) { return get("interviews", ivKey(pid)).then(function (s) { return s ? open(s) : null; }); }
  function saveIv(iv) { return seal(iv).then(function (s) { return put("interviews", ivKey(iv.pid), s); }); }
  function expired() { return pack && new Date(pack.until).getTime() < Date.now(); }

  function draw() {
    slotsEl.textContent = "";
    if (!pack) { say(de_en("Keine Interviews reserviert. Mit Netz „Für offline vorbereiten“.", "No interviews reserved. With a network, “Prepare for offline”.")); return Promise.resolve(); }
    return Promise.all(pack.slots.map(function (s) { return loadIv(s.pid).then(function (iv) { return [s, iv]; }); })).then(function (rows) {
      var free = 0;
      rows.forEach(function (pair) {
        var s = pair[0], iv = pair[1], li = slotTpl.content.firstElementChild.cloneNode(true), go = li.querySelector("[data-slot-go]");
        li.querySelector("[data-slot-label]").textContent = s.label;
        var st = s.synced ? de_en("abgeglichen", "synced") : s.erased ? de_en("gelöscht (Löschcode genutzt)", "deleted (deletion code used)")
          : !iv ? de_en("frei", "free") : iv.finished ? de_en("beendet, wartet auf Abgleich", "ended, waiting for the sync")
          : de_en("begonnen", "started");
        li.querySelector("[data-slot-state]").textContent = st;
        if (s.synced || s.erased || (iv && iv.finished) || expired()) go.remove();
        else if (iv) { go.textContent = de_en("Fortsetzen", "Continue"); go.addEventListener("click", function () { begin(s, iv); }); }
        else { free += 1; go.textContent = de_en("Beginnen", "Start"); go.addEventListener("click", function () { ask(s); }); }
        slotsEl.appendChild(li);
      });
      var until = new Date(pack.until).toLocaleDateString(document.documentElement.lang || undefined);
      say(expired() ? de_en("Das Feldpaket ist abgelaufen (" + until + "): nur noch abgleichen.", "The field pack has expired (" + until + "): sync only.")
                    : de_en(free + " freie Interviews, gültig bis " + until + ".", free + " free interviews, valid until " + until + "."), expired());
    });
  }

  // --- prepare -----------------------------------------------------------------------------------------------------------
  kit.querySelector("[data-kit-prepare]").addEventListener("submit", function (e) {
    e.preventDefault();
    var n = parseInt(kit.querySelector("[data-kit-n]").value, 10) || 5;
    say(de_en("Wird vorbereitet …", "Preparing …"));
    device().then(function (dev) {
      return fetch(packUrl, { method: "POST", credentials: "same-origin", body: JSON.stringify({ n: n, device: dev }),
                              headers: { "Content-Type": "application/json", "X-CSRFToken": csrf || "", "X-Requested-With": "fetch" } });
    }).then(function (r) { if (!r.ok) throw new Error(r.status === 401 ? "signin" : String(r.status)); return r.json(); }).then(function (d) {
      var old = pack && !expired() ? pack.slots.filter(function (s) { return !s.synced && !s.erased; }) : [];
      // a second pack: its places go after those still open; one pack id per place, so each syncs with its own
      pack = { until: d.until, device: d.device, slots: old.concat(d.slots.map(function (s) { return { pid: s.pid, label: s.label, code: s.code, pack: d.pack }; })) };
      return savePack();
    }).then(function () {
      if (window.AskuriousAudioStore && AskuriousAudioStore.persist) AskuriousAudioStore.persist();
      if (navigator.serviceWorker && navigator.serviceWorker.controller) navigator.serviceWorker.controller.postMessage({ keep: location.href });
      return draw();
    }).catch(function (err) {
      say(err && err.message === "signin" ? de_en("Bitte zuerst anmelden (Interviewlink oder Anmeldung).", "Please sign in first (interviewer link or sign-in).")
                                         : de_en("Vorbereiten geht nur mit Netz. Bitte mit Verbindung erneut versuchen.", "Preparing needs a network. Please try again with a connection."), true);
    });
  });

  // --- start, continue, end ----------------------------------------------------------------------------------------------
  function ask(slot) {
    show(openBox, false); show(newForm, true);
    newForm.querySelector("[data-new-label]").textContent = slot.label;
    newForm.querySelector("[data-new-date]").value = new Date().toISOString().slice(0, 10);
    newForm.onsubmit = function (e) {
      e.preventDefault();
      var f = new FormData(newForm), now = new Date().toISOString();
      var iv = { pid: slot.pid, label: slot.label, pack: slot.pack, started: now, date: f.get("date") || "", mode: f.get("mode") || "",
                 consent: { form: f.get("consent_form") || "", audio: !!f.get("audio"), all: now }, trial: !!f.get("trial"),
                 st: null, finished: false };
      if (!f.get("consent")) return;  // the browser asks for the tick (required); no interview without it
      saveIv(iv).then(function () { show(newForm, false); begin(slot, iv); });
    };
  }
  newForm.querySelector("[data-new-cancel]").addEventListener("click", function () { show(newForm, false); show(openBox, true); });

  function begin(slot, iv) {
    current = { slot: slot, iv: iv };
    var label = root.querySelector(".iv-label");
    if (label) label.textContent = slot.label + (iv.trial ? " · " + de_en("Probeinterview", "trial") : "");
    var rec = root.querySelector("[data-iv-rec]");
    if (rec) { if (iv.consent.audio) rec.setAttribute("data-audio-store", "off-" + guide + "-" + slot.pid); else rec.remove(); }
    if (!resolveReady) {  // interview.js already runs for another interview: this one starts on a fresh page
      try { sessionStorage.setItem(NEXT, slot.pid); } catch (e) { /* none */ }
      location.reload();
      return;
    }
    kit.hidden = true;
    resolveReady({ key: "askurious-offline-" + slot.pid, st: iv.st }); resolveReady = null;
  }

  // what interview.js calls (see there, "offline mode")
  var keeping = Promise.resolve();
  window.AskuriousOffline = {
    ready: new Promise(function (ok) { resolveReady = ok; }),
    cipher: cipher,
    keep: function (st) {
      if (!current) return;
      current.iv.st = JSON.parse(JSON.stringify(st));
      keeping = keeping.then(function () { return saveIv(current.iv); }).catch(function () { /* full: the page holds it */ });
    },
    finish: function (st) {
      current.iv.st = JSON.parse(JSON.stringify(st));
      current.iv.finished = true; current.iv.ended = new Date().toISOString();
      return keeping.then(function () { return saveIv(current.iv); }).then(function () {
        kit.hidden = false; show(openBox, false); show(codeBox, true);
        codeBox.querySelector("[data-code-value]").textContent = current.slot.code;
      });
    }
  };
  codeBox.querySelector("[data-code-done]").addEventListener("click", function () { location.reload(); });
  kit.querySelector("[data-kit-lockbtn]").addEventListener("click", lockNow);

  // --- sync ----------------------------------------------------------------------------------------------------------------
  function post(url, body) {
    return fetch(url, { method: "POST", credentials: "same-origin", body: JSON.stringify(body),
                        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf || "", "X-Requested-With": "fetch" } });
  }
  function payload(st) {  // the state as interview.js sends it online (body())
    st = st || {};
    var elapsed = (st.elapsed || 0) + (st.since ? Date.now() - st.since : 0);
    return { states: st.states || {}, ticks: Object.keys(st.ticks || {}), fits: st.fits || {}, notes: st.notes || {},
             marks: st.marks || [], elapsed: elapsed, blocks: st.blocks || {}, running: false };
  }
  function sendAudio(slot, url) {
    if (!window.AskuriousAudioStore) return Promise.resolve();
    return AskuriousAudioStore.open("off-" + guide + "-" + slot.pid, cipher).then(function (store) {
      return store.left().then(function (pieces) {
        var moved = {};
        return pieces.reduce(function (chain, p) {
          return chain.then(function () {
            function once() {
              var part = moved[p.rec.id] || p.rec.part;
              return fetch(url + "?part=" + part + "&seq=" + p.seq + "&start=" + (p.start || 0) + "&rec=" + p.rec.id,
                           { method: "POST", body: p.blob, credentials: "same-origin",
                             headers: { "Content-Type": p.type, "X-CSRFToken": csrf || "", "X-Requested-With": "fetch" } })
                .then(function (r) {
                  if (r.ok) return;
                  if (r.status === 409) return r.json().catch(function () { return {}; }).then(function (d) {
                    if (d && d.part && moved[p.rec.id] !== d.part) { moved[p.rec.id] = d.part; return once(); }
                    throw new Error("audio 409");
                  });
                  throw new Error("audio " + r.status);
                });
            }
            return once();
          });
        }, Promise.resolve()).then(function () { return store.clear(); });
      });
    });
  }
  function syncOne(slot) {
    return loadIv(slot.pid).then(function (iv) {
      if (!iv || slot.synced) return "skip";
      var base = { pack: iv.pack || slot.pack, device: pack.device, pid: slot.pid, date: iv.date, mode: iv.mode, consent: iv.consent,
                   trial: iv.trial, started: iv.started, state: payload(iv.st), forms: Object.values((iv.st || {}).forms || {}) };
      return post(syncUrl, base).then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) { return [r, d]; }); })
        .then(function (rd) {
          var r = rd[0], d = rd[1];
          if (r.status === 410) { slot.erased = true; return forget(slot).then(function () { return "erased"; }); }
          if (!r.ok) throw new Error(d.error || String(r.status));
          return sendAudio(slot, d.audio).then(function () {
            if (!iv.finished) return "open";  // continued later online, or ended here after the next visit
            return post(syncUrl, Object.assign({}, base, { finish: true, ended: iv.ended })).then(function (r2) {
              if (!r2.ok) throw new Error(String(r2.status));
              slot.synced = true;
              return forget(slot).then(function () { return "done"; });
            });
          });
        });
    });
  }
  function forget(slot) {  // the device's copy goes: the interview, its pieces, its deletion code
    slot.code = "";
    return del("interviews", ivKey(slot.pid)).then(function () {
      return window.AskuriousAudioStore ? AskuriousAudioStore.open("off-" + guide + "-" + slot.pid, cipher).then(function (s) { return s.clear(); }) : null;
    }).then(savePack);
  }
  function syncAll() {
    if (syncing || !pack || !key) return Promise.resolve();
    syncing = true;
    say(de_en("Wird abgeglichen …", "Syncing …"));
    var done = 0, open_ = 0, failed = [];
    return pack.slots.reduce(function (chain, slot) {
      return chain.then(function () {
        return syncOne(slot).then(function (how) { if (how === "done" || how === "erased") done += 1; if (how === "open") open_ += 1; },
                                  function (err) { failed.push(slot.label + " (" + (err && err.message || err) + ")"); });
      });
    }, Promise.resolve()).then(function () {
      if (pack.slots.every(function (s) { return s.synced || s.erased; }) || (expired() && !failed.length)) pack = null;
      return savePack();
    }).then(draw).then(function () {
      syncing = false;
      if (failed.length) say(de_en("Nicht abgeglichen: ", "Not synced: ") + failed.join(", "), true);
      else if (done || open_) say(de_en(done + " Interviews abgeglichen und vom Gerät gelöscht" + (open_ ? ", " + open_ + " begonnene übertragen" : "") + ".",
                                        done + " interviews synced and deleted from the device" + (open_ ? ", " + open_ + " started ones sent" : "") + "."));
    });
  }
  kit.querySelector("[data-kit-sync]").addEventListener("click", syncAll);
  window.addEventListener("online", function () { if (!current) syncAll(); });

  // --- the page without a network: the service worker keeps this page and its files (interview-sw.js) --------------------
  var swUrl = root.getAttribute("data-sw");
  if (swUrl && navigator.serviceWorker) {
    navigator.serviceWorker.register(swUrl, { scope: location.pathname }).then(function () { return navigator.serviceWorker.ready; }).then(function (reg) {
      var files = Array.prototype.map.call(document.querySelectorAll("script[src], link[rel=stylesheet][href], link[rel~=icon][href], img[src]"),
                                           function (el) { return el.src || el.href; });
      if (reg && reg.active) reg.active.postMessage({ keep: location.href, files: files });
    }).catch(function () { say(de_en("Dieser Browser kann die Seite nicht für ohne Netz behalten.", "This browser cannot keep the page for use without a network."), true); });
  }

  // --- start ---------------------------------------------------------------------------------------------------------------
  function firstTime() { return get("meta", "lock").then(function (l) { return !l; }); }
  if (!window.crypto || !crypto.subtle) {
    say(de_en("Dieser Browser kann nicht verschlüsseln (nur über https): Der Offline-Modus geht hier nicht.",
              "This browser cannot encrypt (https only): offline mode does not work here."), true);
    return;
  }
  function opened() {  // unlocked: the list, or straight into the interview a reload was for
    return loadPack().then(function (p) {
      pack = p; show(lockForm, false); show(openBox, true);
      if (window.AskuriousAudioStore && AskuriousAudioStore.persist) AskuriousAudioStore.persist();
      var next = null;
      try { next = sessionStorage.getItem(NEXT); sessionStorage.removeItem(NEXT); } catch (e) { /* none */ }
      var slot = next && pack ? pack.slots.filter(function (s) { return s.pid === next; })[0] : null;
      if (slot) return loadIv(slot.pid).then(function (iv) { if (iv && !iv.finished) begin(slot, iv); else return draw(); });
      return draw().then(function () { if (navigator.onLine) syncAll(); });
    });
  }
  recall().then(function (back) { return back ? opened() : firstTime().then(locked); }, function () {
    say(de_en("Dieser Browser hat keinen lokalen Speicher (privates Fenster?): Der Offline-Modus geht hier nicht.",
              "This browser has no local storage (private window?): offline mode does not work here."), true);
  });
  function locked(first) {
    kit.querySelectorAll("[data-kit-first]").forEach(function (el) { el.hidden = !first; });
    show(lockForm, true);
    lockForm.addEventListener("submit", function (e) {
      e.preventDefault();
      var pin = lockForm.querySelector("[data-kit-pin]").value, again = lockForm.querySelector("[data-kit-pin2]").value;
      if (first && pin !== again) { say(de_en("Die beiden PINs sind verschieden.", "The two PINs differ."), true); return; }
      say(de_en("Wird entsperrt …", "Unlocking …"));
      unlock(pin, first).then(function () { return remember(); }).then(opened, function () {
        say(de_en("PIN falsch.", "Wrong PIN."), true);
      });
    });
  }
})();
