// Takes the handbook screenshots from the running local instance (tools/handbook_shots/run.py starts everything).
//   node shots.mjs <seed.json> <out folder> [--port 9333] [--only name,name]
// Each shot: open a page as "Maria Muster", bring it into the state the handbook describes, save a JPEG.
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { Page, sleep } from "./cdp.mjs";

const args = process.argv.slice(2);
const seedFile = args[0];
const outDir = args[1];
const opt = (name, dflt) => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : dflt; };
const only = opt("--only", "") ? opt("--only", "").split(",") : null;
const port = Number(opt("--port", "9333"));
const base = opt("--base", "http://localhost:5070");
const edgePath = opt("--edge", "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe");
const S = JSON.parse(fs.readFileSync(seedFile, "utf8"));
const de = S.lang === "de";
const T = (d, e) => (de ? d : e);
const W = 1600, H = 1029;

// --- helpers running in the page ------------------------------------------------------------------------------
const HELPERS = `
window.__q = (sel, text) => {
  const all = [...document.querySelectorAll(sel)];
  if (text === undefined) return all.find(e => e.offsetParent !== null || e.tagName === 'DETAILS') || all[0] || null;
  const want = Array.isArray(text) ? text : [text];
  return all.find(e => want.some(t => (e.textContent || '').replace(/\\s+/g, ' ').trim().includes(t))) || null;
};
window.__click = (sel, text) => { const e = __q(sel, text); if (!e) throw new Error('no element ' + sel + ' ' + text); e.click(); return true; };
window.__open = (sel, text) => { const e = __q(sel, text); if (!e) throw new Error('no details ' + sel + ' ' + text);
  const d = e.tagName === 'DETAILS' ? e : e.closest('details'); d.open = true; return true; };
window.__top = (sel, text, offset) => { const e = __q(sel, text); if (!e) throw new Error('no element ' + sel + ' ' + text);
  const y = e.getBoundingClientRect().top + window.scrollY - (offset ?? 80); window.scrollTo(0, Math.max(0, y)); return y; };
window.__set = (sel, value) => { const e = document.querySelector(sel); if (!e) throw new Error('no field ' + sel);
  e.focus(); e.value = value; e.dispatchEvent(new Event('input', {bubbles: true})); return true; };
window.__deep = (text, root) => { // the innermost visible element whose text contains the text
  let best = null;
  for (const e of (root || document.body).querySelectorAll('*')) {
    if (!(e.textContent || '').includes(text) || e.offsetParent === null) continue;
    if (![...e.children].some(c => (c.textContent || '').includes(text))) { best = e; break; }
  }
  return best;
};
window.__union = (els, pad) => { pad = pad ?? 0; let x1 = 1e9, y1 = 1e9, x2 = -1e9, y2 = -1e9;
  for (const e of els) { if (!e) continue; const r = e.getBoundingClientRect();
    x1 = Math.min(x1, r.left); y1 = Math.min(y1, r.top); x2 = Math.max(x2, r.right); y2 = Math.max(y2, r.bottom); }
  return {x: Math.max(0, x1 - pad + window.scrollX), y: Math.max(0, y1 - pad + window.scrollY),
          width: x2 - x1 + 2 * pad, height: y2 - y1 + 2 * pad}; };
window.__rect = (sel, text) => { const e = __q(sel, text); if (!e) throw new Error('no element ' + sel + ' ' + text);
  const r = e.getBoundingClientRect(); return {x: r.left + window.scrollX, y: r.top + window.scrollY, width: r.width, height: r.height}; };
`;

async function helpers(p) { await p.eval(HELPERS); }

async function open(p, url, { wait = 500 } = {}) {
  await p.goto(base + url);
  await helpers(p);
  await sleep(wait);
}

const log = [];

async function save(p, name, clip) {
  let buf;
  if (clip && clip.beyond) {
    // taller than the window: make the window as tall as needed, so that the sticky header and footer stay where they
    // belong instead of being drawn into the middle of the picture
    const y = await p.eval("window.scrollY");
    const top = clip.y;
    await p.viewport(W, Math.ceil(top + clip.height + 60), 1);
    await p.eval("window.scrollTo(0, 0)");
    await sleep(400);
    buf = await p.screenshot({ x: clip.x, y: top, width: clip.width, height: clip.height });
    await p.viewport(W, H, 1);
    await p.eval(`window.scrollTo(0, ${y})`);
  } else {
    buf = await p.screenshot(clip);
  }
  fs.writeFileSync(path.join(outDir, name + ".jpg"), buf);
  const info = await p.eval("({title: document.title, path: location.pathname + location.search, err: !!document.querySelector('.error-page, .errorpage')})");
  log.push({ name, ...info });
  console.log(`  ${name}.jpg  ${info.path}  "${info.title}"`);
}

// wait for the page a click sends the browser to
async function clickAndLoad(p, js, wait = 600) {
  const loaded = p.waitFor("Page.loadEventFired");
  await p.eval(js);
  await loaded;
  await p.settle();
  await helpers(p);
  await sleep(wait);
}

// the area from the top of one element to the bottom of another (selectors), scrolled so that the top sits under the
// sticky header; in page coordinates, drawn beyond the window when taller
async function area(p, from, to, { pad = 16, offset = 90, maxH = 1600 } = {}) {
  return await p.eval(`(() => { const a = document.querySelector(${JSON.stringify(from)}), b = document.querySelector(${JSON.stringify(to || from)});
    if (!a || !b) throw new Error('area: element missing');
    window.scrollTo(0, Math.max(0, a.getBoundingClientRect().top + window.scrollY - ${offset}));
    const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
    const x1 = Math.min(ra.left, rb.left), x2 = Math.max(ra.right, rb.right);
    const y1 = ra.top + window.scrollY - ${pad}, y2 = Math.min(rb.bottom + window.scrollY + ${pad}, y1 + ${maxH});
    return {x: Math.max(0, x1 - ${pad}), y: Math.max(0, y1), width: x2 - x1 + 2 * ${pad}, height: y2 - y1,
            beyond: (y2 - window.scrollY) > window.innerHeight}; })()`);
}

const K = S.kundenzufriedenheit;
const L = S.learn, C = S.conjoint, E = S.experiment, V = S.vignetten, P = S.panelbefragung, G = S.guide, LV = S.live, CR = S.crowd;
const fitai = Object.entries(L.items).find(([k]) => k.startsWith("FI"));

// --- the shots: name -> async (page) ---------------------------------------------------------------------------
const SHOTS = {
  "dashboard": async (p) => { await open(p, "/dashboard/"); await save(p, "dashboard"); },
  "new-survey": async (p) => { await open(p, "/surveys/new"); await save(p, "new-survey"); },
  "new-from-template": async (p) => {
    await open(p, "/surveys/new");
    await p.eval(`__click('a,button,label,.card', ['${T("Aus einer Vorlage", "From a template")}'])`);
    await sleep(800); await helpers(p);
    await save(p, "new-from-template");
  },
  "editor": async (p) => { await open(p, `/surveys/${L.code}/editor/`); await save(p, "editor"); },
  "settings": async (p) => { await open(p, `/surveys/${L.code}`); await save(p, "settings"); },
  "appearance": async (p) => {
    await open(p, `/surveys/${L.code}/design`, { wait: 1500 });
    await p.eval("window.scrollTo(0, 0)"); await sleep(300);
    await save(p, "appearance");
  },
  "research-plan": async (p) => { await open(p, `/surveys/${L.code}/researchdesign`); await save(p, "research-plan"); },
  "element-likert": async (p) => { await open(p, `/surveys/${L.code}/editor/items/${fitai[1]}`); await save(p, "element-likert"); },
  "learning-aid": async (p) => {
    await open(p, `/surveys/${L.code}/editor/items/${fitai[1]}`);
    await p.eval(`__open('details summary', ['${T("Warum?", "Why?")}'])`);
    await sleep(300);
    await save(p, "learning-aid");
  },
  "ai-scale-dialog": async (p) => {
    await open(p, `/surveys/${L.code}/editor/`);
    await p.eval(`(() => { const it = document.querySelector('.palette-item[data-type=ai_scale]');
      if (it) it.scrollIntoView({block: 'center'});
      const b = document.querySelector('button.palette-add[data-type=ai_scale]'); if (b) b.click();
      const d = document.querySelector('dialog[data-ai-dialog]'); if (d && !d.open) d.showModal(); return !!d; })()`);
    await sleep(600);
    await p.eval(`(() => { const d = document.querySelector('dialog[data-ai-dialog] textarea[name=idea]');
      if (d) { d.value = ${JSON.stringify(T("Wie sehr Menschen einem Pflegeroboter zutrauen, sie im Alltag zuverlässig zu unterstützen",
        "How much people trust a care robot to support them reliably in everyday life"))}; d.dispatchEvent(new Event('input', {bubbles: true})); }
      return !!d; })()`);
    await sleep(300);
    await save(p, "ai-scale-dialog");
  },
  "element-conjoint": async (p) => { await open(p, `/surveys/${C.code}/editor/items/${C.types.conjoint}`); await save(p, "element-conjoint"); },
  "flow-experiment": async (p) => { await open(p, `/surveys/${E.code}/flow/`); await save(p, "flow-experiment"); },
  "flow-vignettes": async (p) => { await open(p, `/surveys/${V.code}/flow/`); await save(p, "flow-vignettes"); },
  "quotas": async (p) => { await open(p, `/surveys/${P.code}/flow/quotas/`); await save(p, "quotas"); },
  "translation": async (p) => { await open(p, `/surveys/${K.code}/translation/`); await save(p, "translation"); },
  "preview-consent": async (p) => {
    await open(p, `/surveys/${L.code}/editor/preview?restart=1`);
    await open(p, `/surveys/${L.code}/editor/preview?page=1`);
    await save(p, "preview-consent");
  },
  "preview-likert": async (p) => {
    await open(p, `/surveys/${L.code}/editor/preview?block=${L.block_of.NUTZEN}`, { wait: 800 });
    await save(p, "preview-likert");
  },
  "preview-conjoint": async (p) => {
    await open(p, `/surveys/${C.code}/editor/preview?block=${C.block_of.cbc}`, { wait: 800 });
    await save(p, "preview-conjoint");
  },
  "field": async (p) => { await open(p, `/surveys/${L.code}/field`); await save(p, "field"); },
  "field-links": async (p) => {
    await open(p, `/surveys/${L.code}/field`);
    await p.eval(`__top('h2,h3,summary,.fold-head', ['${T("Teilnahmelink", "Participation link")}'], 90)`);
    await sleep(300);
    await save(p, "field-links");
  },
  "quality": async (p) => {
    await open(p, `/surveys/${L.code}/quality`, { wait: 1500 });
    await p.eval(`__top('h2,h3,summary,.fold-head', ['${T("Bearbeitungszeiten", "Completion times")}'], 130)`);
    await sleep(500);
    await save(p, "quality");
  },
  "results-hypotheses": async (p) => {
    await open(p, `/surveys/${L.code}/results`, { wait: 1500 });
    await p.eval(`__top('h2,h3,summary', ['${T("Ergebnisse je Hypothese", "Results per hypothesis")}'], 110)`);
    await sleep(400);
    await save(p, "results-hypotheses");
  },
  "results-overview": async (p) => {
    await open(p, `/surveys/${L.code}/results`, { wait: 1500 });
    await p.eval(`__top('h2,h3,summary', ['${T("Skalen im Überblick", "Scales at a glance")}'], 110)`);
    await sleep(400);
    await save(p, "results-overview");
  },
  "results-experiment-a": async (p) => {
    await open(p, `/surveys/${E.code}/results`, { wait: 1500 });
    await p.eval(`__top('h2,h3,summary', ['${T("Ergebnisse je Hypothese", "Results per hypothesis")}'], 110)`);
    await sleep(400);
    await save(p, "results-experiment-a");
  },
  "results-conjoint-a": async (p) => {
    await open(p, `/surveys/${C.code}/results`, { wait: 2500 });
    await p.eval(`__top('h2,h3,summary', ['${T("Ergebnisse je Hypothese", "Results per hypothesis")}'], 110)`);
    await sleep(400);
    await save(p, "results-conjoint-a");
  },
  "dashboards": async (p) => { await open(p, `/surveys/${L.code}/dashboards/`); await save(p, "dashboards"); },
  "export": async (p) => { await open(p, `/surveys/${L.code}/data`); await save(p, "export"); },
  "methods": async (p) => { await open(p, `/surveys/${L.code}/methods`, { wait: 1500 }); await save(p, "methods"); },
  "crowd": async (p) => {
    await open(p, `/surveys/${CR.code}/editor/preview?restart=1`);
    await open(p, `/surveys/${CR.code}/editor/preview?block=${CR.block}`, { wait: 800 });
    await p.eval(`(() => { const r = [...document.querySelectorAll('input[name="i${CR.items.bus}"]')];
      const pick = r.find(e => e.value === '5') || r[4]; pick.click(); pick.dispatchEvent(new Event('change', {bubbles: true}));
      return r.length; })()`);
    await sleep(500);
    await p.eval(`(() => { const b = document.querySelector('[data-crowd-fix-button]'); if (b) b.click(); return !!b; })()`);
    await sleep(4000);
    const clip = await p.eval(`(() => { const box = document.querySelector('[data-crowd]');
      const q = document.querySelector('input[name="i${CR.items.bus}"]').closest('fieldset, .p-item, .question, section') ;
      box.scrollIntoView({block: 'center'});
      const u = __union([q, box], 24); return u; })()`);
    await sleep(500);
    await save(p, "crowd", clip);
  },
  "guide": async (p) => { await open(p, `/guide/${G.code}`); await save(p, "guide"); },
  "guide-ai": async (p) => {
    await open(p, `/guide/${G.code}/ai/develop`);
    await p.eval(`(() => { const t = [...document.querySelectorAll('textarea')].find(e => e.offsetParent);
      t.value = ${JSON.stringify(S.fill.guide_collect)}; t.dispatchEvent(new Event('input', {bubbles: true})); return true; })()`);
    await sleep(300);
    await save(p, "guide-ai");
  },
  "guide-conduct": async (p) => {
    await open(p, `/guide/${G.code}/conversation`, { wait: 800 });
    await p.eval(`(() => { const e = __deep(${JSON.stringify(G.first_question || "")}.slice(0, 30));
      if (e) { (e.closest('a,button,li,[data-goto],[data-jump]') || e).click(); } return !!e; })()`);
    await sleep(800);
    await save(p, "guide-conduct");
  },
  "guide-interviews": async (p) => { await open(p, `/guide/${G.code}/interviews`); await save(p, "guide-interviews"); },
  "live-assistant": async (p) => {
    await open(p, `/presenter/${LV.code}/setup/frage`);
    await p.eval(`(() => { const t = [...document.querySelectorAll('textarea')].find(e => e.offsetParent);
      t.value = ${JSON.stringify(S.fill.live_assistant)}; t.dispatchEvent(new Event('input', {bubbles: true})); return true; })()`);
    await sleep(300);
    await save(p, "live-assistant");
  },
  "live-questions": async (p) => { await open(p, `/presenter/${LV.code}`); await save(p, "live-questions"); },
  // the speaker view, the phones and the end: one sequence, as during a lecture
  "live-speaker-1": async (p) => {
    await open(p, `/presenter/${LV.code}/speaker`, { wait: 1500 });
    const step = async (what) => { await p.eval(`document.querySelector('[data-step=${what}]').click()`); await sleep(2500); };
    await step("next");
    await save(p, "live-speaker-1");
    // the phone at the same moment: the question on the screen
    await p.viewport(390, 844, 1.5, true);
    await open(p, `/live/${LV.code}`, { wait: 1500 });
    await save(p, "live-phone");
    await p.viewport(W, H, 1);
    await open(p, `/presenter/${LV.code}/speaker`, { wait: 1500 });
    await step("next");
    await step("next");
    await save(p, "live-speaker-3");
    await step("end");
    await p.viewport(390, 844, 1.5, true);
    await open(p, `/live/${LV.code}`, { wait: 1500 });
    await save(p, "live-phone-after");
    await p.viewport(W, H, 1);
  },
  "constructs": async (p) => { await open(p, "/constructs/"); await save(p, "constructs"); },
  "groups": async (p) => { await open(p, "/groups/"); await save(p, "groups"); },
  "designs": async (p) => { await open(p, "/designs/"); await save(p, "designs"); },
  "sample-size": async (p) => { await open(p, "/sample-size/", { wait: 800 }); await save(p, "sample-size"); },
  "account": async (p) => { await open(p, "/account/"); await save(p, "account"); },
  "itemtypes": async (p) => { await open(p, "/itemtypes/"); await save(p, "itemtypes"); },
  "stats-glossary": async (p) => { await open(p, "/learn/glossary"); await save(p, "stats-glossary"); },
  "image-styles": async (p) => {
    await open(p, `/surveys/${C.code}#image-style`, { wait: 800 });
    const clip = await p.eval(`(() => { const g = document.querySelector('.style-grid');
      const d = g.closest('details'); if (d) d.open = true;
      g.scrollIntoView({block: 'center'});
      const form = g.closest('form'); return __union([form], 0); })()`);
    await sleep(400);
    await save(p, "image-styles", clip);
  },
  // --- the page "Anleitungen" (guides.md) ----------------------------------------------------------------------
  "guide-panel-params": async (p) => {
    await open(p, `/surveys/${P.code}/field#panel`, { wait: 800 });
    await p.eval(`document.querySelectorAll('#links, #panel').forEach(d => d.open = true)`);
    await sleep(300);
    const clip = await area(p, "#panel table.param-table", "#panel [data-link-rows]", { offset: 190, pad: 20 });
    clip.y = Math.max(0, clip.y - 50); clip.height += 50;  // the heading of the parameter table sits above it
    await sleep(300);
    await save(p, "guide-panel-params", clip);
  },
  "guide-panel-rules": async (p) => {
    await open(p, `/surveys/${P.code}/field#panel`, { wait: 800 });
    await p.eval(`document.querySelectorAll('#links, #panel').forEach(d => d.open = true)`);
    await sleep(300);
    const clip = await area(p, "#panel h3.link-rules-head", "#panel-link", { offset: 140, pad: 20 });
    clip.height += 20;
    await sleep(300);
    await save(p, "guide-panel-rules", clip);
  },
  "guide-panel-paste": async (p) => {
    await open(p, `/surveys/${P.code}/field#panel`, { wait: 800 });
    await p.eval(`document.querySelectorAll('#links, #panel, #panel-paste').forEach(d => d.open = true)`);
    await p.eval(`(() => { const t = document.querySelector('#panel-paste textarea[name=text]');
      t.value = ${JSON.stringify(S.paste)}; return true; })()`);
    await clickAndLoad(p, `document.querySelector('#panel-paste button[name=action][value=preview]').click()`);
    await p.eval(`document.querySelectorAll('#links, #panel, #panel-paste').forEach(d => d.open = true)`);
    await sleep(300);
    const clip = await area(p, "#panel-paste", "#panel-paste", { offset: 140, pad: 16 });
    await sleep(300);
    await save(p, "guide-panel-paste", clip);
    // discard the pasted list again (it is kept per person on the server)
    await clickAndLoad(p, `document.querySelector('#panel-paste button[name=action][value=cancel]').click()`);
  },
  "guide-panel-quota-state": async (p) => {
    await open(p, `/surveys/${P.code}/flow/quotas/?qp=source%3Da`, { wait: 1200 });
    const clip = await area(p, "#quotas", "#quotas", { offset: 140, pad: 12, maxH: 1500 });
    await sleep(300);
    await save(p, "guide-panel-quota-state", clip);
  },
  "guide-panel-quota-link": async (p) => {
    await open(p, P.quota_link, { wait: 1000 });
    const clip = await p.eval(`(() => { const h = Math.min(document.documentElement.scrollHeight, 1600);
      const right = Math.max(...[...document.querySelectorAll('main *')].map(e => e.getBoundingClientRect().right).filter(r => r < ${W}));
      return {x: 0, y: 0, width: Math.min(${W}, Math.ceil(right + 48)), height: Math.max(h, 400), beyond: h > ${H}}; })()`);
    await save(p, "guide-panel-quota-link", clip);
  },
  "guide-panel-board-link": async (p) => {
    await open(p, `/surveys/${P.code}/dashboards/${P.board}#links`, { wait: 1200 });
    const ok = await p.eval(`(() => { const s = document.querySelector('select[name=qp]');
      if (!s) return false; let d = s.closest('details'); while (d) { d.open = true; d = d.parentElement && d.parentElement.closest('details'); }
      const o = [...s.options].find(o => o.value === 'source=a'); if (o) { s.value = o.value; s.dispatchEvent(new Event('change', {bubbles: true})); }
      const lab = s.form && s.form.querySelector('input[name=label]'); if (lab) lab.value = ${JSON.stringify(T("Panelanbieter A", "Panel provider A"))};
      s.form.setAttribute('data-shot', '1'); return true; })()`);
    if (!ok) throw new Error("no provider choice in the dashboard designer");
    await sleep(300);
    const clip = await area(p, "form[data-shot]", "form[data-shot]", { offset: 200, pad: 24 });
    clip.y -= 50; clip.height += 50;
    await save(p, "guide-panel-board-link", clip);
  },
  "guide-experiment-plan": async (p) => {
    await open(p, `/surveys/${E.code}/researchdesign`, { wait: 800 });
    await p.eval(`__top('legend,h3,h2', ['${T("Hypothesen", "Hypotheses")}'], 120)`);
    await sleep(400);
    await save(p, "guide-experiment-plan");
  },
  "guide-experiment-preview": async (p) => {
    await open(p, `/surveys/${E.code}/editor/preview?restart=1`);
    await open(p, `/surveys/${E.code}/editor/preview?block=${E.block_of.text_nutzen}`, { wait: 800 });
    await save(p, "guide-experiment-preview", { x: 0, y: 0, width: W, height: 560 });
  },
  "guide-seminar-add": async (p) => {
    await open(p, `/surveys/${K.code}/field#value-links`, { wait: 800 });
    await p.eval(`(() => { document.querySelectorAll('#links, #value-links').forEach(d => d.open = true);
      const box = document.querySelector('#value-links');
      const t = box.querySelector('textarea[name=names]'); t.value = ${JSON.stringify("Anna Beispiel\nBen Muster\nClara Probe\nKurs Biologie")};
      const b = box.querySelector('input[name=board]'); if (b) b.checked = true; return true; })()`);
    await sleep(300);
    const clip = await area(p, "#value-links", "#value-links", { offset: 140, pad: 16 });
    await save(p, "guide-seminar-add", clip);
  },
  "guide-seminar-table": async (p) => {
    await open(p, `/surveys/${S.seminar.code}/field#value-links`, { wait: 800 });
    await p.eval(`document.querySelectorAll('#links, #value-links').forEach(d => d.open = true)`);
    await sleep(300);
    const clip = await area(p, "#value-links", "#value-links", { offset: 140, pad: 16 });
    await save(p, "guide-seminar-table", clip);
  },
  "guide-seminar-cards": async (p) => {
    await open(p, `/surveys/${S.seminar.code}/value-links/cards`, { wait: 1000 });
    await save(p, "guide-seminar-cards", { x: 0, y: 0, width: W, height: 760 });
  },
  "guide-seminar-dashboard": async (p) => {
    await open(p, `/surveys/${S.seminar.code}/field#value-links`, { wait: 800 });
    const url = await p.eval(`(() => { const i = document.querySelector('input[id^=vb-]'); return i ? i.value : null; })()`);
    if (!url) throw new Error("no dashboard link per person");
    await open(p, new URL(url).pathname, { wait: 1500 });
    await save(p, "guide-seminar-dashboard", { x: 0, y: 0, width: W, height: 540 });
  },
  "guide-waves-code": async (p) => {
    const w = S.waves[0];
    await open(p, `/surveys/${w.code}/editor/preview?restart=1`);
    await open(p, `/surveys/${w.code}/editor/preview?block=${w.block}`, { wait: 800 });
    await save(p, "guide-waves-code", { x: 0, y: 0, width: W, height: 780 });
  },
  "guide-waves-plan": async (p) => {
    await open(p, `/surveys/${S.waves[0].code}/researchdesign#wellen`, { wait: 1000 });
    const clip = await area(p, "#wellen", "#wellen", { offset: 140, pad: 12, maxH: 1300 });
    await save(p, "guide-waves-plan", clip);
  },
  "guide-waves-results": async (p) => {
    await open(p, `/surveys/${S.waves[2].code}/results`, { wait: 2500 });
    await helpers(p);
    await p.eval(`(() => { const h = __q('details summary h2', ['${T("Längsschnitt über die Wellen", "Longitudinal analysis over the waves")}']);
      const d = h.closest('details'); d.open = true; d.setAttribute('data-shot', '1'); return true; })()`);
    await sleep(500);
    const clip = await area(p, "details[data-shot]", "details[data-shot]", { offset: 130, pad: 12, maxH: 1500 });
    await save(p, "guide-waves-results", clip);
  },

};

// --- run ----------------------------------------------------------------------------------------------------
fs.mkdirSync(outDir, { recursive: true });
const profile = path.join(path.dirname(seedFile), "edge-profile");
const edge = spawn(edgePath, ["--headless=new", `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
  "--no-first-run", "--no-default-browser-check", "--hide-scrollbars", "--disable-gpu", "--mute-audio",
  "--disable-extensions", "--disable-sync", "--force-color-profile=srgb", `--lang=${de ? "de-DE" : "en-GB"}`,
  "--renderer-process-limit=2", "about:blank"], { stdio: "ignore" });
let failed = [];
try {
  const p = await Page.connect(port);
  await p.viewport(W, H, 1);
  await p.goto(`${base}/auth/dev-login?orcid=${S.orcid}&name=${encodeURIComponent(S.name)}&next=/dashboard/`);
  for (const [name, fn] of Object.entries(SHOTS)) {
    if (only && !only.includes(name)) continue;
    try {
      await p.viewport(W, H, 1);
      await p.eval("window.scrollTo(0, 0)");
      await fn(p);
    } catch (e) {
      failed.push(name);
      console.log(`  FAILED ${name}: ${e.message.split("\n")[0]}`);
    }
  }
} finally {
  edge.kill();
}
fs.writeFileSync(path.join(path.dirname(seedFile), `shots-${S.lang}.json`), JSON.stringify({ log, failed }, null, 1));
if (failed.length) console.log("failed:", failed.join(", "));
