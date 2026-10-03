#!/usr/bin/env node
// OffRamp app smoke test (added 2026-10-03 after the "Searching…" outage: commit 9f3c3f7 removed
// two filter vars but search() still referenced them, so every search threw before rendering).
// Runs the REAL inline JS from app/index.html in Node with DOM stubs, feeds live rows to the
// /api/search fetch, calls search(), and fails if the list never renders.
//
// Usage:
//   node scripts/app_smoke.mjs [rows.json]        # rows.json = array of /api/search result rows
//   node scripts/app_smoke.mjs                    # no file: tries http://127.0.0.1:8092/api/search?state=AZ&limit=200
//                                                 #   (needs OFFRAMP_SMOKE_COOKIE=<session cookie> for an authed call,
//                                                 #    otherwise falls back to 3 synthetic rows)
// Exit 0 = pass, 1 = fail. Run it before every app push.
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const here = path.dirname(fileURLToPath(import.meta.url));
const indexPath = path.join(here, '..', 'app', 'index.html');
const html = fs.readFileSync(indexPath, 'utf8');
const blocks = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);
if (!blocks.length) { console.error('SMOKE FAIL: no inline <script> blocks found in app/index.html'); process.exit(1); }
const js = blocks.join('\n');

async function loadRows() {
  const arg = process.argv[2];
  if (arg) return JSON.parse(fs.readFileSync(arg, 'utf8'));
  try {
    const headers = process.env.OFFRAMP_SMOKE_COOKIE ? { cookie: process.env.OFFRAMP_SMOKE_COOKIE } : {};
    const r = await globalThis.fetch('http://127.0.0.1:8092/api/search?state=AZ&limit=200', { headers });
    if (r.ok) { const j = await r.json(); if (j.results && j.results.length) return j.results; }
  } catch (e) { /* server not reachable; fall through */ }
  return [
    { id: 's1', property_street: '1 Test St', property_city: 'Phoenix', property_state: 'AZ', property_zip: '85001', foreclosure_status: 'Scheduled', auction_date: '2026-11-01', days_to_auction: 29, equity_pct: 40, avm: 300000, liens: null },
    { id: 's2', property_street: '2 Test St', property_city: 'Mesa', property_state: 'AZ', property_zip: '85201', foreclosure_status: 'Postponed', auction_date: '2026-11-05', days_to_auction: 33, equity_pct: null, equity_unverified: true, avm: 250000, lien_count: 2, lien_first_lender: 'Bank', lien_first_amount: 100000 },
    { id: 's3', property_street: '3 Test St', property_city: 'Tucson', property_state: 'AZ', property_zip: '85701', foreclosure_status: null, auction_date: '2026-10-20', days_to_auction: 17, equity_pct: 75, avm: 410000, sale_verified_source: 'trustee-page', latitude: 32.2, longitude: -110.9 },
  ];
}

const rows = await loadRows();
const els = {};
const mk = () => ({ innerHTML: '', value: '', textContent: '', className: '', style: {}, checked: false, dataset: {}, hidden: false,
  classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
  addEventListener() {}, appendChild() {}, querySelectorAll() { return []; }, querySelector() { return null; }, setAttribute() {}, getAttribute() { return null; }, focus() {}, blur() {} });
const fetches = [];
const g = globalThis;
g.document = { getElementById: id => (els[id] = els[id] || mk()), querySelectorAll: () => [], querySelector: () => mk(), addEventListener() {}, body: mk(), createElement: () => mk(), hidden: false };
g.window = g; g.addEventListener = () => {}; g.removeEventListener = () => {};
g.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
g.sessionStorage = g.localStorage;
g.navigator = { serviceWorker: { register() { return Promise.resolve(); } }, userAgent: 'smoke' };
g.location = { hash: '', search: '', href: 'https://offramprei.com/app/', origin: 'https://offramprei.com', pathname: '/app/', reload() {} };
g.history = { replaceState() {}, pushState() {} };
g.setTimeout = () => 0; g.setInterval = () => 0; g.clearTimeout = () => {}; g.clearInterval = () => {};
g.L = undefined; // Leaflet absent on purpose; map code must tolerate it
g.fetch = (u) => { fetches.push(String(u)); const s = String(u); const body = s.includes('/api/search') ? { results: rows, count: rows.length } : s.includes('/api/me') ? { email: 'smoke@offramprei.com', plan: 'pro', paid: true } : { results: [] };
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body), text: () => Promise.resolve(JSON.stringify(body)) }); };

let failed = false;
try { (0, eval)(js); } catch (e) { console.error('SMOKE WARN: top-level script error (continuing):', e.message); }
try {
  g.STATE = 'AZ'; g.ME = { paid: true, plan: 'pro' };
  if (typeof g.search !== 'function') throw new Error('search() is not defined after loading app/index.html');
  g.search();
  for (let i = 0; i < 6; i++) await new Promise(r => setImmediate(r));
  const listHtml = els.list ? els.list.innerHTML : '';
  const n = (g.ROWS || []).length;
  const stuck = /Searching/.test(listHtml);
  const searched = fetches.some(u => u.includes('/api/search'));
  console.log(`fetches: ${fetches.length} (search called: ${searched}) | ROWS: ${n} | list html: ${listHtml.length} chars | still "Searching": ${stuck}`);
  if (!searched) { console.error('SMOKE FAIL: /api/search was never requested'); failed = true; }
  if (stuck) { console.error('SMOKE FAIL: list still shows "Searching…" after search() resolved'); failed = true; }
  if (n === 0) { console.error('SMOKE FAIL: ROWS is empty after search()'); failed = true; }
  if (listHtml.length < 100) { console.error('SMOKE FAIL: list did not render any cards'); failed = true; }
  // detail render on the first row must not throw either
  if (typeof g.openDetail === 'function') { try { g.openDetail(0); } catch (e) { console.error('SMOKE FAIL: openDetail(0) threw:', e.message); failed = true; } }
} catch (e) {
  console.error('SMOKE FAIL: search() threw:', e.name, e.message);
  failed = true;
}
console.log(failed ? 'SMOKE: FAIL' : 'SMOKE: PASS');
process.exit(failed ? 1 : 0);
