// A small Chrome DevTools Protocol client for headless Edge (Node 22+: global WebSocket and fetch).
export class Page {
  constructor(ws) {
    this.ws = ws;
    this.id = 0;
    this.pending = new Map();
    this.listeners = [];
    ws.addEventListener("message", (ev) => {
      const msg = JSON.parse(typeof ev.data === "string" ? ev.data : ev.data.toString());
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        if (msg.error) reject(new Error(msg.error.message + " " + (msg.error.data || "")));
        else resolve(msg.result);
      } else if (msg.method) {
        for (const l of this.listeners.slice()) l(msg);
      }
    });
  }

  static async connect(port) {
    let targets = [];
    for (let i = 0; i < 50; i++) {
      try {
        targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
        if (targets.some((t) => t.type === "page")) break;
      } catch (e) { /* not up yet */ }
      await sleep(200);
    }
    const target = targets.find((t) => t.type === "page");
    if (!target) throw new Error("no page target in Edge");
    const ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.addEventListener("open", res); ws.addEventListener("error", rej); });
    const page = new Page(ws);
    await page.send("Page.enable");
    await page.send("Runtime.enable");
    await page.send("Network.enable");
    return page;
  }

  send(method, params = {}) {
    const id = ++this.id;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      setTimeout(() => {
        if (this.pending.has(id)) { this.pending.delete(id); reject(new Error("timeout " + method)); }
      }, 60000);
    });
  }

  waitFor(method, timeout = 30000) {
    return new Promise((resolve) => {
      const l = (msg) => {
        if (msg.method === method) { this.listeners = this.listeners.filter((x) => x !== l); resolve(msg.params); }
      };
      this.listeners.push(l);
      setTimeout(() => { this.listeners = this.listeners.filter((x) => x !== l); resolve(null); }, timeout);
    });
  }

  async goto(url) {
    const loaded = this.waitFor("Page.loadEventFired");
    const res = await this.send("Page.navigate", { url });
    if (res.errorText) throw new Error(`navigate ${url}: ${res.errorText}`);
    await loaded;
    await this.settle();
  }

  // wait until fonts are in and nothing moved for a moment
  async settle(ms = 400) {
    await this.eval("document.fonts ? document.fonts.ready.then(() => true) : true");
    await sleep(ms);
  }

  async eval(expression) {
    const r = await this.send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) {
      throw new Error("JS: " + (r.exceptionDetails.exception?.description || r.exceptionDetails.text) + "\n" + expression.slice(0, 300));
    }
    return r.result.value;
  }

  async viewport(width, height, scale = 1, mobile = false) {
    await this.send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: scale, mobile });
    await this.send("Emulation.setTouchEmulationEnabled", { enabled: mobile });
  }

  async screenshot(clip, quality = 88) {
    // a clip taller than the window (or below it) needs the page drawn beyond the window
    const params = { format: "jpeg", quality, captureBeyondViewport: !!(clip && clip.beyond) };
    if (clip) { clip = { x: clip.x, y: clip.y, width: clip.width, height: clip.height }; }
    if (clip) params.clip = { ...clip, scale: 1 };
    const r = await this.send("Page.captureScreenshot", params);
    return Buffer.from(r.data, "base64");
  }
}

export function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }
