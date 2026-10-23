// The interview view without a connection (offline G, Ralf 2026-10-22): this service worker lets the page of a stored
// interview (/guide/<code>/conversation?interview=<id>, scope of exactly that path, interview.js registers it) open
// again without a network: a reload, a closed tab, a browser that ended. Served by app/interview.py (interview_sw),
// so that its scope may lie under /guide/.
// - The page: from the network first; when it does not answer within a few seconds, the copy kept here, marked with
//   data-offline-copy so that interview.js takes the state this device kept (localStorage) over the page's own.
// - Its files under /static/ (each address carries the hash of its content, ?v=): from here when kept, else fetched.
// - Nothing else is touched: saves, recording pieces and every other page go to the network as before.
// Privacy: the copy holds the notes of that interview; it goes when the interview ends (message "drop" from
// interview.js), and every copy older than 12 hours goes the next time this worker runs. The field kit of the offline
// mode (/guide/<code>/offline, app/interview_offline.py) holds only the guide; its copy lasts 7 days.
"use strict";
var PAGES = "askurious-iv-pages-1", FILES = "askurious-iv-files-1", MAX_AGE = 12 * 3600 * 1000, WAIT = 6000;

function isKit(url) { return /^\/guide\/[^/]+\/offline$/.test(url.pathname); }  // the field kit: the guide, no interview in it
function isPage(url) { return (/^\/guide\/[^/]+\/conversation$/.test(url.pathname) && url.searchParams.has("interview")) || isKit(url); }
function maxAge(url) { return isKit(new URL(url)) ? 7 * 24 * 3600 * 1000 : MAX_AGE; }  // E5 b: a field week

function sweep() {  // copies older than 12 hours and files no page needs any more
  return caches.open(PAGES).then(function (cache) {
    return cache.keys().then(function (keys) {
      return Promise.all(keys.map(function (req) {
        return cache.match(req).then(function (res) {
          var at = res ? Number(res.headers.get("X-Kept-At") || 0) : 0;
          if (!at || Date.now() - at > maxAge(req.url)) return cache.delete(req);
          return null;
        });
      })).then(function () { return cache.keys(); });
    });
  }).then(function (left) { if (!left.length) return caches.delete(FILES); return null; });
}

self.addEventListener("install", function () { self.skipWaiting(); });
self.addEventListener("activate", function (e) {
  e.waitUntil(caches.keys().then(function (names) {  // an older version's stores
    return Promise.all(names.filter(function (n) { return /^askurious-iv-/.test(n) && n !== PAGES && n !== FILES; }).map(function (n) { return caches.delete(n); }));
  }).then(sweep).then(function () { return self.clients.claim(); }));
});
self.addEventListener("message", function (e) {
  var d = e.data || {};
  if (d.drop) e.waitUntil(caches.open(PAGES).then(function (c) { return c.delete(d.drop, { ignoreVary: true }); }).then(sweep));
  // the first visit: the page loaded before this worker ran, so it names itself and its files to be kept now
  if (d.keep && isPage(new URL(d.keep))) {
    e.waitUntil(fetch(d.keep, { credentials: "same-origin" }).then(function (res) {
      if (res.ok && !res.redirected) return keepPage(new Request(d.keep), res);
      return null;
    }).catch(function () { /* offline already: the next visit keeps it */ }));
  }
  if (Array.isArray(d.files)) {
    var files = d.files.filter(function (f) { var u = new URL(f, self.location.origin); return u.origin === self.location.origin && u.pathname.indexOf("/static/") === 0 && u.searchParams.has("v"); });
    e.waitUntil(caches.open(FILES).then(function (c) {
      return Promise.all(files.map(function (f) { return c.match(f).then(function (hit) { return hit || c.add(f).catch(function () {}); }); }));
    }));
  }
});

function keepPage(req, res) {
  return res.clone().text().then(function (html) {
    var headers = new Headers({ "Content-Type": res.headers.get("Content-Type") || "text/html; charset=utf-8", "X-Kept-At": String(Date.now()) });
    return caches.open(PAGES).then(function (c) { return c.put(req.url, new Response(html, { headers: headers })); });
  });
}
function offlineCopy(req) {
  return caches.open(PAGES).then(function (c) { return c.match(req.url); }).then(function (res) {
    if (!res || Date.now() - Number(res.headers.get("X-Kept-At") || 0) > maxAge(req.url)) return null;
    return res.text().then(function (html) {
      return new Response(html.replace("data-interview ", 'data-interview data-offline-copy="' + res.headers.get("X-Kept-At") + '" '),
                          { headers: { "Content-Type": res.headers.get("Content-Type") || "text/html; charset=utf-8" } });
    });
  });
}

self.addEventListener("fetch", function (e) {
  var req = e.request, url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin) return;
  if (req.mode === "navigate" && isPage(url)) {
    e.respondWith(new Promise(function (resolve) {
      var done = false;
      function give(res) { if (!done && res) { done = true; resolve(res); } }
      // a slow network: the kept copy after WAIT, the network's answer still refreshes the copy when it comes
      var timer = setTimeout(function () { offlineCopy(req).then(give); }, WAIT);
      fetch(req).then(function (res) {
        clearTimeout(timer);
        if (res.ok && !res.redirected && (res.headers.get("Content-Type") || "").indexOf("text/html") === 0) e.waitUntil(keepPage(req, res));
        give(res);
      }, function () {
        clearTimeout(timer);
        offlineCopy(req).then(function (copy) { give(copy || Response.error()); });
      });
    }));
    return;
  }
  if (url.pathname.indexOf("/static/") === 0 && url.searchParams.has("v")) {
    e.respondWith(caches.open(FILES).then(function (c) {
      return c.match(req).then(function (hit) {
        return hit || fetch(req).then(function (res) { if (res.ok) c.put(req, res.clone()); return res; });
      });
    }));
  }
});
