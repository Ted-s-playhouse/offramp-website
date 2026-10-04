#!/usr/bin/env node
// Detail parity check (perf 2026-10-03: card-sized feed rows).
// Proves that a lead opened from a CARD_COLS feed row + /api/property hydration renders the SAME detail HTML
// (header, body, contacts, facts, loan/checks) as the old full-row feed did, for every tier, on a set of leads.
// Runs the real inline JS from app/index.html in Node with the same DOM stubs as app_smoke.mjs; the "old" app is
// read from git (--old-rev, default HEAD) so the comparison is against the code that shipped before this change.
//
// Usage:
//   node scripts/detail_parity.mjs --base=http://127.0.0.1:8197 --ids=<uuid>,<uuid>,... [--old-rev=HEAD] [--tiers=free,pro,premium]
// Needs the UAT creds file (UAT_CREDS, /tmp/claude-1000/**/uat_creds.json or ~/.cortextos/exports/offramp-uat/uat_creds.json).
// Read-only: logs in, GETs /api/me, /api/search?state=UT, /api/property, and /api/property-facts for ONE cached lead.
// Exit 0 = every tier x lead identical, 1 = a difference (printed) or a harness error.
import fs from 'fs';
import os from 'os';
import path from 'path';
import vm from 'vm';
import { execSync } from 'child_process';
import { fileURLToPath } from 'url';

const here = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(here, '..');
const ARGS = Object.fromEntries(process.argv.slice(2).filter(a => a.startsWith('--')).map(a => { const [k, ...v] = a.slice(2).split('='); return [k, v.join('=') || true]; }));
const BASE = String(ARGS.base || 'http://127.0.0.1:8197').replace(/\/$/, '');
const IDS = String(ARGS.ids || '').split(',').filter(Boolean);
const TIERS = String(ARGS.tiers || 'free,pro,premium').split(',');
const OLD_REV = String(ARGS['old-rev'] || 'HEAD');
const FACTS_LEAD = String(ARGS['facts-lead'] || '547967de-0cf9-4fd5-8593-b0a6aa236346'); // UT, county record cached, liens filled: no live pull, no JIT write
if (!IDS.length) { console.error('need --ids=<uuid>,...'); process.exit(1); }

function findCreds() {
  const cands = [process.env.UAT_CREDS, ARGS.creds].filter(Boolean);
  try { const p = execSync('find /tmp/claude-1000 -name uat_creds.json 2>/dev/null | head -1').toString().trim(); if (p) cands.push(p); } catch (e) {}
  cands.push(path.join(os.homedir(), '.cortextos', 'exports', 'offramp-uat', 'uat_creds.json'));
  for (const c of cands) if (c && fs.existsSync(c)) return c;
  throw new Error('uat_creds.json not found');
}
const creds = JSON.parse(fs.readFileSync(findCreds(), 'utf8'));
const tierOf = (email) => email.startsWith('free@') ? 'free' : email.startsWith('premium@') ? 'premium' : 'pro';

async function login(email) {
  const r = await fetch(BASE + '/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email, password: creds[email] }) });
  if (!r.ok) throw new Error('login failed for ' + email + ' ' + r.status);
  const sc = r.headers.getSetCookie ? r.headers.getSetCookie() : [r.headers.get('set-cookie')];
  const m = sc.join(';').match(/offramp_sess=([^;]+)/); if (!m) throw new Error('no cookie');
  return m[1];
}
async function getJson(pathname, cookie) {
  const r = await fetch(BASE + pathname, { headers: { Cookie: 'offramp_sess=' + cookie } });
  if (!r.ok) throw new Error(pathname + ' -> ' + r.status);
  return r.json();
}

const inlineJs = (html) => [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]).join('\n');
const NEW_JS = inlineJs(fs.readFileSync(path.join(ROOT, 'app', 'index.html'), 'utf8'));
const OLD_JS = inlineJs(execSync(`git show ${OLD_REV}:app/index.html`, { cwd: ROOT }).toString());

function sandbox(stubs) {
  const els = {};
  const mk = () => ({ innerHTML: '', value: '', textContent: '', className: '', style: {}, checked: false, dataset: {}, hidden: false, scrollTop: 0,
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    addEventListener() {}, appendChild() {}, querySelectorAll() { return []; }, querySelector() { return null; }, setAttribute() {}, getAttribute() { return null; }, focus() {}, blur() {} });
  const fetches = [];
  const g = {};
  g.document = { getElementById: id => (els[id] = els[id] || mk()), querySelectorAll: () => [], querySelector: () => mk(), addEventListener() {}, body: mk(), head: mk(), createElement: () => mk(), hidden: false, documentElement: mk() };
  g.window = g; g.globalThis = g; g.addEventListener = () => {}; g.removeEventListener = () => {};
  g.localStorage = { getItem: () => null, setItem() {}, removeItem() {} }; g.sessionStorage = g.localStorage;
  g.navigator = { serviceWorker: { register() { return Promise.resolve(); } }, userAgent: 'parity' };
  g.location = { hash: '', search: '', href: 'https://offramprei.com/app/', origin: 'https://offramprei.com', pathname: '/app/', reload() {} };
  g.history = { replaceState() {}, pushState() {} };
  g.setTimeout = () => 0; g.setInterval = () => 0; g.clearTimeout = () => {}; g.clearInterval = () => {};
  g.console = console; g.Promise = Promise; g.JSON = JSON; g.Math = Math; g.Number = Number; g.String = String; g.Object = Object; g.Array = Array; g.Date = Date; g.RegExp = RegExp; g.Error = Error;
  g.encodeURIComponent = encodeURIComponent; g.decodeURIComponent = decodeURIComponent; g.URLSearchParams = URLSearchParams; g.URL = URL; g.isNaN = isNaN; g.parseInt = parseInt; g.parseFloat = parseFloat;
  g.requestAnimationFrame = () => 0; g.AbortController = AbortController;
  g.fetch = (u) => { const s = String(u); fetches.push(s); const body = stubs(s) || { results: [] };
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(JSON.parse(JSON.stringify(body))), text: () => Promise.resolve(JSON.stringify(body)) }); };
  return { g, els, fetches };
}
const settle = async (n = 40) => { for (let i = 0; i < n; i++) await new Promise(r => setImmediate(r)); };

// Render one lead: returns the visible pieces of the detail view
async function renderDetail(js, me, feedRows, fullRow, factsResp) {
  const { g, els } = sandbox((u) => u.includes('/api/property-facts') ? factsResp : u.includes('/api/property?') ? { property: fullRow } : u.includes('/api/search') ? { results: feedRows } : u.includes('/api/me') ? { user: me } : null);
  vm.createContext(g);
  try { vm.runInContext(js, g); } catch (e) { /* top-level boot fetch is stubbed; ignore */ }
  g.ME = JSON.parse(JSON.stringify(me)); g.STATE = 'UT';
  g.ROWS = JSON.parse(JSON.stringify(feedRows));
  g.renderList();
  const listHtml = els.list.innerHTML;
  g.openDetail(0);
  await settle();
  const h = (id) => (els[id] ? els[id].innerHTML : '');
  return { list: listHtml, detail: h('detailInner'), facts: h('facts'), contacts: h('contacts'), loanchecks: h('loanchecks'), courtrec: h('courtrec'), row: g.ROWS[0] };
}

function firstDiff(a, b) { let i = 0; while (i < a.length && i < b.length && a[i] === b[i]) i++; return { at: i, a: a.slice(Math.max(0, i - 80), i + 160), b: b.slice(Math.max(0, i - 80), i + 160) }; }

let failed = false, checks = 0;
for (const email of Object.keys(creds)) {
  const tier = tierOf(email); if (!TIERS.includes(tier)) continue;
  const cookie = await login(email);
  const me = (await getJson('/api/me', cookie)).user;
  const feed = (await getJson('/api/search?state=UT&limit=200', cookie)).results;
  const cardCols = Object.keys(feed[0] || {}).length;
  const factsResp = await getJson('/api/property-facts?id=' + FACTS_LEAD, cookie);
  for (const id of IDS) {
    const card = feed.find(r => r.id === id);
    const full = (await getJson('/api/property?id=' + id, cookie)).property;
    if (!card) { console.log(`[${tier}] ${id}: not in the UT feed right now (skipped)`); continue; }
    const fullCols = Object.keys(full).length;
    // the facts stub for the JIT variant: a row with no lien sheet gets liens from the county record
    const jitFacts = factsResp && factsResp.facts ? { facts: Object.assign({}, factsResp.facts, { liens: { lien_count: 1, lien_first_position: 'First', lien_first_lender: 'PARITY BANK', lien_first_amount: 123456, lien_first_type: 'Conventional', lien_second_amount: null, lien_total_amount: 123456 } }) } : null;
    const variants = [['facts', factsResp], ['facts-locked', { source: 'locked' }], ['facts-none', { facts: null, source: 'miss' }]];
    if (jitFacts) variants.push(['facts-jit', jitFacts]);
    for (const [vname, fr] of variants) {
      const jit = vname === 'facts-jit';
      const fullV = jit ? Object.assign({}, full, { lien_count: null }) : full;
      const cardV = jit ? Object.assign({}, card, { lien_count: null }) : card;
      const before = await renderDetail(OLD_JS, me, [fullV], fullV, fr);          // old app, full row from the feed
      const after = await renderDetail(NEW_JS, me, [cardV], fullV, fr);           // new app, card row + /api/property
      const afterFull = await renderDetail(NEW_JS, me, [fullV], fullV, fr);       // new app, full row (e.g. a re-open)
      for (const piece of ['detail', 'facts', 'contacts', 'loanchecks', 'courtrec']) {
        checks++;
        for (const [nm, cand] of [['card+property', after], ['full', afterFull]]) {
          if (before[piece] !== cand[piece]) {
            failed = true; const d = firstDiff(before[piece], cand[piece]);
            console.log(`DIFF [${tier}] ${id} ${vname} ${piece} (${nm}) at ${d.at}\n  before: ${JSON.stringify(d.a)}\n  after : ${JSON.stringify(d.b)}`);
          }
        }
      }
      // the feed card itself (list HTML) must be identical from the card row and from the full row
      checks++;
      if (before.list !== after.list) { failed = true; const d = firstDiff(before.list, after.list); console.log(`DIFF [${tier}] ${id} ${vname} card at ${d.at}\n  before: ${JSON.stringify(d.a)}\n  after : ${JSON.stringify(d.b)}`); }
      if (before.detail.length < 500 || !/dhead/.test(before.detail) || /Loading the lead/.test(after.detail)) { failed = true; console.log(`FAIL [${tier}] ${id} ${vname}: detail did not render (before ${before.detail.length} chars, after has loading=${/Loading the lead/.test(after.detail)})`); }
    }
    console.log(`[${tier}] ${id}: ok  (card row ${cardCols} keys, full row ${fullCols} keys, variants ${variants.length})`);
  }
}
console.log(`${checks} comparisons, ${failed ? 'DIFFERENCES FOUND' : 'all identical'}`);
console.log(failed ? 'PARITY: FAIL' : 'PARITY: PASS');
process.exit(failed ? 1 : 0);
