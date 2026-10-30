// Runs app/static/recorder.js (the phone's recording page) in a small fake browser for
// tests/interview/test_recording_restart.py: the microphone goes away while recording, and the page records on by
// itself (Ralf 2026-10-30). Prints the observations as JSON on stdout. Argument: how many times the microphone
// cannot be opened again after the loss.
const fs = require("fs"), path = require("path"), vm = require("vm");
const failing = parseInt(process.argv[2] || "0", 10);

function element(attrs) {
  const listeners = {}, classes = new Set();
  return {
    attrs: attrs || {}, textContent: "", hidden: false, disabled: false, listeners,
    getAttribute(name) { return name in this.attrs ? this.attrs[name] : null; },
    addEventListener(name, fn) { (listeners[name] = listeners[name] || []).push(fn); },
    click() { (listeners.click || []).forEach((fn) => fn({ preventDefault() {} })); },
    classList: { add: (c) => classes.add(c), remove: (c) => classes.delete(c), toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)), has: (c) => classes.has(c) },
  };
}
const parts = {
  "[data-rec-toggle]": element(), "[data-rec-stop]": element(), ".iv-recorder-buttons": element(),
  "[data-rec-state]": element(), "[data-rec-time]": element(), "[data-rec-save]": element(),
};
const root = element({ "data-csrf": "x", "data-audio-url": "/audio", "data-state-url": "/state", "data-audio-part": "1" });
root.querySelector = (sel) => parts[sel] || null;

let timers = [];
const sent = [], recorders = [], streams = [];
let opens = 0;
function track() { return { readyState: "live", stop() { this.readyState = "ended"; } }; }
function stream() {
  const t = track(), s = { tracks: [t], getTracks: () => s.tracks, getAudioTracks: () => s.tracks };
  streams.push(s);
  return s;
}
class MediaRecorder {
  constructor(s) { this.stream = s; this.state = "inactive"; this.mimeType = "audio/webm"; recorders.push(this); }
  static isTypeSupported(m) { return m === "audio/webm"; }
  start() { this.state = "recording"; }
  stop() { if (this.state === "inactive") return; this.state = "inactive"; this.ondataavailable({ data: { size: 3 } }); }
}
const window = {
  MediaRecorder, addEventListener() {},
  AskuriousMic: { attach: () => ({
    open: () => { opens += 1; return opens > 1 && opens <= 1 + failing ? Promise.reject(new Error("no mic")) : Promise.resolve(stream()); },
    show() {}, hide() {}, lock() {} }) },
};
const sandbox = {
  window, MediaRecorder, console,
  document: { documentElement: { lang: "de" }, querySelector: (sel) => (sel === "[data-recorder]" ? root : null), addEventListener() {}, visibilityState: "visible" },
  navigator: { mediaDevices: { getUserMedia: () => Promise.resolve(stream()) }, onLine: true },
  fetch: (url) => { sent.push(url); return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) }); },
  setTimeout: (fn, ms) => { timers.push(fn); return timers.length; }, clearTimeout: () => {},
  setInterval: () => 0, clearInterval: () => {}, Date, Math, JSON, Promise, parseInt,
};
sandbox.AskuriousMic = window.AskuriousMic;  // the page names it as a global, as in the browser
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "app", "static", "recorder.js"), "utf8"), sandbox);

const tick = () => new Promise((r) => setImmediate(r));
async function run() {
  await tick();
  parts["[data-rec-toggle]"].click();
  await tick(); await tick();
  recorders[0].ondataavailable({ data: { size: 5 } });
  streams[0].tracks[0].onended();  // the headset goes away
  const during = parts["[data-rec-state]"].textContent;
  for (let i = 0; i < 12; i++) { const now = timers; timers = []; now.forEach((fn) => fn()); await tick(); await tick(); }
  if (recorders[1]) { recorders[1].ondataavailable({ data: { size: 5 } }); for (let i = 0; i < 4; i++) await tick(); }
  const audio = sent.filter((u) => u.startsWith("/audio?"));
  process.stdout.write(JSON.stringify({
    during, after: parts["[data-rec-state]"].textContent, toggle: parts["[data-rec-toggle]"].textContent,
    recorders: recorders.length, recording: recorders.filter((r) => r.state === "recording").length,
    firstStopped: recorders[0].state === "inactive", opens,
    parts: [...new Set(audio.map((u) => /part=(\d+)/.exec(u)[1]))],
    pieces: audio.map((u) => /part=(\d+)&seq=(\d+)/.exec(u).slice(1).join(":")),
  }));
}
run();
