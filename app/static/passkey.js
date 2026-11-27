// Passkeys (WebAuthn; app/passkeys.py): the sign-in page's button "Mit Passkey anmelden" and the dialog "Passkey
// hinzufügen" on the page "Konto". Written for Askurious, without a library: the server sends the options for
// navigator.credentials as JSON with bytes in base64url, this script turns them into buffers, asks the browser and
// sends the answer back the same way. Nothing starts by itself: only the click on the button asks the browser.
(function () {
  "use strict";

  function toBytes(text) {
    var b64 = String(text).replace(/-/g, "+").replace(/_/g, "/");
    while (b64.length % 4) b64 += "=";
    var raw = atob(b64), out = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }

  function toText(buffer) {
    var bytes = new Uint8Array(buffer), raw = "";
    for (var i = 0; i < bytes.length; i++) raw += String.fromCharCode(bytes[i]);
    return btoa(raw).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  function listed(list) {
    return (list || []).map(function (c) { return Object.assign({}, c, { id: toBytes(c.id) }); });
  }

  function requestOptions(o) {
    var out = Object.assign({}, o, { challenge: toBytes(o.challenge) });
    if (o.allowCredentials) out.allowCredentials = listed(o.allowCredentials);
    return out;
  }

  function creationOptions(o) {
    var out = Object.assign({}, o, { challenge: toBytes(o.challenge), user: Object.assign({}, o.user, { id: toBytes(o.user.id) }) });
    if (o.excludeCredentials) out.excludeCredentials = listed(o.excludeCredentials);
    return out;
  }

  // what the browser returned, as the server reads it (the fields of PublicKeyCredential.toJSON)
  function answer(credential) {
    var r = credential.response, response = { clientDataJSON: toText(r.clientDataJSON) };
    if (r.attestationObject) response.attestationObject = toText(r.attestationObject);
    if (r.authenticatorData) response.authenticatorData = toText(r.authenticatorData);
    if (r.signature) response.signature = toText(r.signature);
    if (r.userHandle && r.userHandle.byteLength) response.userHandle = toText(r.userHandle);
    return { id: credential.id, rawId: toText(credential.rawId), type: credential.type, response: response,
             authenticatorAttachment: credential.authenticatorAttachment || undefined, clientExtensionResults: {} };
  }

  function post(url, token, body) {
    return fetch(url, { method: "POST", credentials: "same-origin",
                        headers: { "Content-Type": "application/json", "X-CSRFToken": token, "Accept": "application/json" },
                        body: JSON.stringify(body || {}) })
      .then(function (r) { return r.json(); });
  }

  function words(key) {
    if (key === "unsupported") return de_en("Dieser Browser kann hier keine Passkeys verwenden.", "This browser cannot use passkeys here.");
    if (key === "cancelled") return de_en("Der Vorgang wurde abgebrochen oder hat zu lange gedauert.", "The step was cancelled or took too long.");
    if (key === "known") return de_en("Auf diesem Gerät ist für dieses Konto schon ein Passkey hinterlegt.", "This device already holds a passkey for this account.");
    return de_en("Das hat nicht geklappt. Bitte erneut versuchen.", "That did not work. Please try again.");
  }

  function wire(form, start) {
    var box = form.querySelector("[data-passkey-error]"), busy = false;
    function say(text) { if (box) { box.textContent = text || ""; box.hidden = !text; } }
    function token() { var field = form.querySelector('input[name="csrf_token"]'); return field ? field.value : ""; }
    function value(name) { var field = form.querySelector('[name="' + name + '"]'); return field ? field.value : ""; }
    function failed(error) {
      var name = error && error.name;
      say(words(name === "NotAllowedError" || name === "AbortError" ? "cancelled" : name === "InvalidStateError" ? "known" : "failed"));
    }
    // on to the page the server names; the same page with another anchor is loaded anew, so it shows what changed
    function leave(url) {
      var to = document.createElement("a");
      to.href = url;
      if (to.pathname === window.location.pathname && to.search === window.location.search) {
        if (to.hash) window.location.hash = to.hash;
        window.location.reload();
      } else window.location.assign(url);
    }
    function done(result) {
      var url = result && (result.next || result.redirect);
      if (url) { leave(url); return; }
      say((result && result.error) || words("failed"));
    }
    return function () {
      if (busy) return;
      say("");
      if (!window.PublicKeyCredential || !navigator.credentials) { say(words("unsupported")); return; }
      busy = true;
      start(form, token, value).then(done, failed).then(function () { busy = false; });
    };
  }

  // the sign-in page: one-time value, the browser's dialog, the answer; then on to the page the server names
  function signIn(form, token, value) {
    var remember = form.querySelector('input[name="remember"]');
    return post(form.getAttribute("data-options"), token(), {}).then(function (got) {
      if (!got || !got.ok) return got;
      return navigator.credentials.get({ publicKey: requestOptions(got.publicKey) }).then(function (credential) {
        return post(form.getAttribute("data-verify"), token(),
                    { credential: answer(credential), remember: !!(remember && remember.checked), next: value("next") });
      });
    });
  }

  // "Passkey hinzufügen": name and proof to the server, the browser makes the passkey, its answer back
  function add(form, token, value) {
    return post(form.getAttribute("data-options"), token(), { name: value("name"), current: value("current"), code: value("code") })
      .then(function (got) {
        if (!got || !got.ok) return got;
        return navigator.credentials.create({ publicKey: creationOptions(got.publicKey) }).then(function (credential) {
          return post(form.getAttribute("data-verify"), token(), { credential: answer(credential) });
        });
      });
  }

  function ready() {
    var entry = document.querySelector("form[data-passkey-sign-in]");
    if (entry) {
      var go = wire(entry, signIn);
      entry.addEventListener("submit", function (e) { e.preventDefault(); go(); });
      var button = entry.querySelector("[data-passkey-start]");
      if (button) button.addEventListener("click", go);
    }
    var adding = document.querySelector("form[data-passkey-add]");
    if (adding) {
      var make = wire(adding, add);
      adding.addEventListener("submit", function (e) { e.preventDefault(); make(); });
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", ready); else ready();
})();
