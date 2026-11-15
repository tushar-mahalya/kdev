// Opens each URL in headless Chrome and prints console errors, CSP violations
// and failed requests. Usage: CHROME_PATH=... node scripts/console-check.mjs <base> <path>...
import { spawn } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const [base, ...paths] = process.argv.slice(2);
const chrome = spawn(process.env.CHROME_PATH, ['--headless', '--remote-debugging-port=9333', `--user-data-dir=${mkdtempSync(join(tmpdir(), 'kdev-cc-'))}`, 'about:blank'], { stdio: 'ignore' });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let target;
for (let i = 0; i < 50 && !target; i++) {
  await sleep(200);
  try { target = (await (await fetch('http://127.0.0.1:9333/json')).json()).find((t) => t.type === 'page'); } catch {}
}
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener('open', r));
let id = 0; const pending = new Map(); const problems = [];
ws.addEventListener('message', (ev) => {
  const m = JSON.parse(ev.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m.result); pending.delete(m.id); }
  if (m.method === 'Runtime.consoleAPICalled' && ['error', 'warning'].includes(m.params.type)) problems.push(`console.${m.params.type}: ${m.params.args.map((a) => a.value ?? a.description).join(' ')}`);
  if (m.method === 'Runtime.exceptionThrown') problems.push(`exception: ${m.params.exceptionDetails.exception?.description ?? m.params.exceptionDetails.text}`);
  if (m.method === 'Log.entryAdded' && ['error', 'warning'].includes(m.params.entry.level)) problems.push(`log.${m.params.entry.level}: ${m.params.entry.text} ${m.params.entry.url ?? ''}`);
  if (m.method === 'Network.responseReceived' && m.params.response.status >= 400 && !m.params.response.url.includes('/nope')) problems.push(`HTTP ${m.params.response.status}: ${m.params.response.url}`);
});
const send = (method, params = {}) => new Promise((r) => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
await send('Runtime.enable'); await send('Log.enable'); await send('Network.enable');
for (const p of paths) {
  problems.length = 0;
  await send('Page.navigate', { url: base + p });
  await sleep(2500);
  // exercise search on one page
  if (p === '/docs/') {
    await send('Runtime.evaluate', { expression: "document.querySelector('[data-search-open]').click(); const i=document.querySelector('[data-search-input]'); i.value='restore'; i.dispatchEvent(new Event('input'));" });
    await sleep(+(process.env.SEARCH_WAIT || 1500));
    const r = await send('Runtime.evaluate', { expression: "document.querySelectorAll('[data-search-results] a').length" });
    problems.push(`(search results for "restore": ${r.result.value})`);
  }
  console.log(`${p}: ${problems.length ? '\n  ' + problems.map((x) => x.slice(0, 200)).join('\n  ') : 'clean'}`);
}
// Client-side navigation and interactions: every component must re-initialise
// after an astro:page-load and keep the CSP clean.
const ev = async (expression, wait = 900) => { const r = await send('Runtime.evaluate', { expression, awaitPromise: true }); await sleep(wait); return r.result?.value; };
problems.length = 0;
await send('Page.navigate', { url: base + '/' }); await sleep(2000);
const flow = [];
flow.push(['theme toggle', await ev("document.querySelector('[data-theme-toggle]').click(); document.documentElement.dataset.theme")]);
flow.push(['ending picked', await ev("document.querySelectorAll('[data-pick]')[5].click(); [...document.querySelectorAll('[data-out]')].filter(c=>!c.hidden).map(c=>c.dataset.out).join() + ' art=' + document.querySelector('[data-art].on').dataset.art")]);
flow.push(['command filter', await ev("document.querySelector('[data-intent=\"files\"]').click(); const i=document.querySelector('[data-cmd-filter]'); i.value='version'; i.dispatchEvent(new Event('input')); [...document.querySelectorAll('[data-cmds] .card')].filter(r=>!r.hidden).map(c=>c.querySelector('.card__cmd').textContent).join(', ')")]);
flow.push(['architecture open', await ev("document.querySelector('.how__more').click(); 1", 1500) && await ev("const d=document.querySelector('[data-arch]'); d.open + ' paths=' + d.querySelectorAll('.w').length + ' live=' + d.querySelectorAll('.p').length + ' on=' + d.querySelectorAll('.node.on').length + ' hash=' + location.hash")]);
flow.push(['architecture flow', await ev("document.querySelector('[data-flow=\"restore\"]').click(); document.querySelectorAll('[data-arch] .step-b')[2].click(); 1", 600) && await ev("document.querySelector('[data-side-title]').textContent + ' live=' + document.querySelectorAll('[data-arch] .p').length")]);
flow.push(['architecture node', await ev("document.querySelector('[data-node-id=\"boxpy\"]').click(); 1", 500) && await ev("document.querySelector('[data-node-title]').textContent + ' | ' + document.querySelector('[data-node-src]').getAttribute('href')")]);
flow.push(['architecture close', await ev("document.querySelector('[data-arch-close]').click(); 1", 600) && await ev("document.querySelector('[data-arch]').open + ' hash=' + location.hash")]);
flow.push(['terminal scrollable', await ev("const b=document.querySelector('[data-term-body]'); getComputedStyle(b).overflowY", 100)]);
flow.push(['how step', await ev("document.querySelectorAll('[data-how-step]')[4].click(); document.querySelectorAll('[data-node].on').length")]);
flow.push(['nav to docs (client)', await ev("document.querySelector('a[href=\"/docs/\"]').click(); 1", 1800) && await ev('location.pathname + " theme=" + document.documentElement.dataset.theme')]);
flow.push(['toc active after nav', await ev("document.querySelector('a[href=\"/docs/files/\"]').click(); 1", 1800) && await ev("window.scrollTo(0, 1600); 1", 900) && await ev("document.querySelectorAll('[data-toc-link].is-on').length")]);
flow.push(['back to home (client)', await ev("history.back(); 1", 600) && await ev("history.back(); 1", 1800) && await ev("location.pathname + ' term=' + !!document.querySelector('[data-term][data-ready]') + ' how=' + !!document.querySelector('[data-how][data-ready]')")]);
flow.push(['ending after nav', await ev("document.querySelectorAll('[data-pick]')[7].click(); [...document.querySelectorAll('[data-out]')].filter(c=>!c.hidden).map(c=>c.dataset.out).join()")]);
await send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 2, mobile: true }); await sleep(500);
flow.push(['mobile menu', await ev("document.querySelector('[data-menu-toggle]').click(); !document.querySelector('[data-menu]').hidden + '/' + document.querySelector('[data-menu-toggle]').getAttribute('aria-expanded')")]);
flow.push(['menu closes on Escape', await ev("document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'})); document.querySelector('[data-menu]').hidden")]);
flow.forEach(([k, v]) => console.log(`  ${k}: ${v}`));
console.log(`interactions: ${problems.length ? '\n  ' + problems.map((x) => x.slice(0, 200)).join('\n  ') : 'no console errors'}`);
ws.close(); chrome.kill();
