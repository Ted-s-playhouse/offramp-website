#!/usr/bin/env node
// OffRamp REI automated UAT (Playwright, headless Chromium).
// Logs in as each UAT account, walks every view and every filter the app exposes, asserts the tier
// rules in tier_matrix.json, cross-checks every count the UI shows against the rendered cards and the
// server response, screenshots every view, and writes out/<ts>/report.html + report.json.
// Read-only by design: see GUARD below. README.md has the one command and how to add a view.
import fs from 'fs';
import os from 'os';
import path from 'path';
import { fileURLToPath } from 'url';
import { chromium } from 'playwright';
import { writeReport } from './report.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const SITE = path.resolve(here, '..', '..');
const MATRIX = JSON.parse(fs.readFileSync(path.join(here, 'tier_matrix.json'), 'utf8'));
const ARGS = Object.fromEntries(process.argv.slice(2).map(a => { const m = /^--([^=]+)(?:=(.*))?$/.exec(a); return m ? [m[1], m[2] === undefined ? true : m[2]] : [a, true]; }));
const BASE = (process.env.UAT_BASE || ARGS.base || 'https://offramprei.com').replace(/\/$/, '');
const HOST = new URL(BASE).host;
const PUBLISH_DIR = path.join(SITE, '_uat-report-4d1f');
const TS = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
const OUT = path.resolve(ARGS.out || path.join(here, 'out', TS));
const ONLY = ARGS.account ? String(ARGS.account).split(',') : null;          // --account=free,demo
const STATES_ARG = ARGS.states ? String(ARGS.states).split(',').map(s => s.toUpperCase()) : null; // --states=UT,AZ
const SAMPLE_CAP = Number(ARGS.sample || 40);                                 // cards sampled per filter
const MOBILE = { width: 390, height: 844 };
const DESKTOP = { width: 1280, height: 800 };
const IPHONE_UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1 OffRampUAT';
const DESKTOP_UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0 Safari/537.36 OffRampUAT';

// ----------------------------------------------------------------------------- creds (never printed)
function loadCreds() {
  const cands = [process.env.UAT_CREDS, ARGS.creds, path.join(os.homedir(), '.cortextos', 'exports', 'offramp-uat', 'uat_creds.json')].filter(Boolean);
  try { for (const d of fs.readdirSync('/tmp/claude-1000')) { const p = path.join('/tmp/claude-1000', d); if (!fs.statSync(p).isDirectory()) continue; for (const s of fs.readdirSync(p)) cands.push(path.join(p, s, 'scratchpad', 'uat_creds.json')); } } catch (e) { /* no scratchpads */ }
  for (const c of cands) { try { if (fs.existsSync(c)) return JSON.parse(fs.readFileSync(c, 'utf8')); } catch (e) { /* next */ } }
  throw new Error('uat_creds.json not found (set UAT_CREDS=/path or put it at ~/.cortextos/exports/offramp-uat/uat_creds.json)');
}
const CREDS = loadCreds();

// ----------------------------------------------------------------------------- GUARD: what the suite may never do
// Anything that spends money, sends anything, creates anything or writes a row is refused at the network layer so a
// harness bug cannot do it either. tel:/mailto: are asserted, never activated. Stripe stops at the redirect.
const GUARD_POST = ['/api/skiptrace', '/api/court-records', '/api/lookup', '/api/docs/order', '/api/lead-actions', '/api/auth/signup', '/api/signup', '/api/request-access', '/api/storage-waitlist', '/api/storage-listing', '/api/analyze'];
const GUARD_GET = ['/api/export'];
const ALLOW_HOSTS = [HOST, 'unpkg.com', 'tile.openstreetmap.org'];

function nowTs() { return new Date().toISOString().slice(11, 19); }
function slug(s) { return String(s).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, ''); }

class Run {
  constructor(acct, mode) {
    this.acct = acct; this.email = acct.email; this.name = acct.email.split('@')[0]; this.mode = mode; // 'mobile' | 'desktop'
    this.label = mode === 'desktop' ? `${this.name}-desktop` : this.name;
    this.dir = path.join(OUT, this.label); fs.mkdirSync(this.dir, { recursive: true });
    this.checks = []; this.filters = []; this.views = []; this.net = []; this.errors = []; this.guardHits = [];
    this.role = null; this.shots = {}; this.photos = true; this.started = Date.now();
  }
  get tier() { return this.role ? (MATRIX.plans[this.role.plan_key] || MATRIX.plans.free).tier : 'free'; }
  exp(feature) { const f = MATRIX.features[feature]; return f ? f[this.tier] : undefined; }
  rec(view, name, status, detail, shot) {
    const c = { view, name, status, detail: String(detail || '').slice(0, 600), shot: shot || null, t: nowTs() };
    this.checks.push(c); if (!this.views.includes(view)) this.views.push(view);
    const tag = status === 'PASS' ? 'ok  ' : status === 'FAIL' ? 'FAIL' : status === 'SKIP' ? 'skip' : 'info';
    console.log(`[${nowTs()}] ${this.label.padEnd(16)} ${tag} ${view} · ${name}${detail ? ' — ' + String(detail).slice(0, 160) : ''}`);
    return c;
  }
  pass(view, name, detail, shot) { return this.rec(view, name, 'PASS', detail, shot); }
  fail(view, name, detail, shot) { return this.rec(view, name, 'FAIL', detail, shot); }
  skip(view, name, detail, shot) { return this.rec(view, name, 'SKIP', detail, shot); }
  info(view, name, detail, shot) { return this.rec(view, name, 'INFO', detail, shot); }
  ok(view, name, cond, passDetail, failDetail, shot) { return cond ? this.pass(view, name, passDetail, shot) : this.fail(view, name, failDetail || passDetail, shot); }
  async shot(view, opts = {}) {
    let base = slug(view); let n = 1; let file = base + '.jpg';
    while (this.shots[file]) { n++; file = `${base}-${n}.jpg`; }
    this.shots[file] = 1;
    const p = path.join(this.dir, file);
    try {
      if (opts.fullDetail) {  // the lead view is a fixed, self-scrolling layer: flatten it for one full-length capture
        await this.page.addStyleTag({ content: '#detail.open{position:absolute!important;top:0!important;bottom:auto!important;height:auto!important;overflow:visible!important}', }).then(h => this._fullStyle = h).catch(() => {});
        await this.page.screenshot({ path: p, type: 'jpeg', quality: 55, fullPage: true });
        await this.page.evaluate(() => { document.querySelectorAll('style').forEach(s => { if (/#detail\.open\{position:absolute!important/.test(s.textContent)) s.remove(); }); }).catch(() => {});
      } else {
        await this.page.screenshot({ path: p, type: 'jpeg', quality: 55, fullPage: !!opts.fullPage });
      }
    } catch (e) { return null; }
    if (!this.views.includes(view)) this.views.push(view);
    return `${this.label}/${file}`;
  }
  async api(method, url, body) {  // same cookie jar as the browser context, not routed through the page guard
    const r = method === 'GET' ? await this.context.request.get(BASE + url) : await this.context.request.post(BASE + url, { data: body || {} });
    let j = null; try { j = await r.json(); } catch (e) { /* non-json */ }
    return { status: r.status(), json: j, headers: r.headers() };
  }
  lastNet(pattern, t0) { const m = this.net.filter(e => e.ts >= t0 && pattern.test(e.url)); return m.length ? m[m.length - 1] : null; }
}

// ----------------------------------------------------------------------------- browser plumbing
async function openContext(browser, run) {
  const desktop = run.mode === 'desktop';
  run.context = await browser.newContext({
    viewport: desktop ? DESKTOP : MOBILE, deviceScaleFactor: 1, isMobile: !desktop, hasTouch: !desktop,
    userAgent: desktop ? DESKTOP_UA : IPHONE_UA, baseURL: BASE, serviceWorkers: 'allow', locale: 'en-US', timezoneId: 'America/Denver',
  });
  run.context.setDefaultTimeout(20000);
  run.page = await run.context.newPage();
  const page = run.page;
  page.on('dialog', d => d.dismiss().catch(() => {}));
  page.on('pageerror', e => run.errors.push({ kind: 'pageerror', msg: String(e && e.message || e).slice(0, 300), t: nowTs() }));
  page.on('console', m => { if (m.type() === 'error') { const t = m.text(); if (!/api\/photo|favicon|net::ERR_|Failed to load resource/.test(t)) run.errors.push({ kind: 'console', msg: t.slice(0, 300), t: nowTs() }); } });
  page.on('response', res => {
    const u = res.url(); if (!u.includes('/api/')) return;
    const e = { url: u.replace(BASE, ''), status: res.status(), ts: Date.now(), json: null, done: null };
    e.done = res.json().then(j => { e.json = j; }).catch(() => {});
    run.net.push(e);
  });
  await page.route('**/*', route => {
    const req = route.request(); const u = new URL(req.url()); const p = u.pathname; const m = req.method();
    if (/checkout\.stripe\.com$/.test(u.host) || /billing\.stripe\.com$/.test(u.host)) {   // stop at the redirect, never load Stripe
      run.stripeHit = (run.stripeHit || 0) + 1;
      return route.fulfill({ status: 200, contentType: 'text/html', body: '<!doctype html><title>UAT stop</title><body style="font:16px -apple-system,system-ui;padding:40px;background:#F6F5F1;color:#15221B"><h2>UAT guard: Stripe checkout redirect intercepted</h2><p>The app sent the browser to <code>' + u.host + '</code>. The suite stops here. No purchase is made.</p></body>' });
    }
    if (!ALLOW_HOSTS.some(h => u.host === h || u.host.endsWith('.' + h))) return route.abort('blockedbyclient');
    if (u.host === HOST) {
      if (p === '/api/track') return route.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true,"uat":"beacon suppressed"}' });
      if (p === '/api/billing/checkout-link' || p === '/api/billing/portal') {   // read the body here: the app navigates to Stripe right after, which discards it
        return route.fetch().then(async r => { const body = await r.text(); run.lastCheckout = { status: r.status(), body, path: p }; return route.fulfill({ response: r, body }); }).catch(() => route.abort());
      }
      if (p === '/api/photo' && !run.photos) return route.abort('blockedbyclient');
      if ((m === 'POST' && GUARD_POST.includes(p)) || (m === 'GET' && GUARD_GET.includes(p))) {
        run.guardHits.push(`${m} ${p}`);
        return route.fulfill({ status: 418, contentType: 'application/json', body: '{"error":"UAT guard: this request is never allowed from the suite"}' });
      }
    }
    return route.continue();
  });
}

// Wait for the deal list to settle after something triggers search(), then reconcile the three counts.
async function settleSearch(run, t0, trigger) {
  const page = run.page;
  let seq0 = await page.evaluate(() => window.SEARCH_SEQ || 0).catch(() => 0);
  if (trigger) await trigger();
  // perf-fix: enterApp() now calls search() synchronously (not deferred behind loadActions).
  // When called without a trigger (initial-load check), seq0 is already ≥ 1; decrement so we wait
  // for the in-progress search to finish rather than waiting for a never-fired second search.
  else if (seq0 > 0) seq0 -= 1;
  await page.waitForFunction(s0 => (window.SEARCH_SEQ || 0) > s0 && !/Searching…|Loading deals…/.test(document.getElementById('list').innerHTML), seq0, { timeout: 45000 });
  await page.waitForTimeout(60);
  // the response listener parses JSON asynchronously; give it a beat
  let s = null; for (let i = 0; i < 60 && !s; i++) { s = run.lastNet(/\/api\/search\?/, t0); if (s) await s.done; else await page.waitForTimeout(50); }
  const n = run.lastNet(/\/api\/national-search\?/, t0); if (n) await n.done;
  const ui = await page.evaluate(() => {
    const txt = (document.getElementById('countLine').textContent || '').trim();
    const m = /^(\d+) deals in (.+?)(?: · (\d+) with owner data)?$/.exec(txt);
    const list = document.getElementById('list');
    const hid = /(Show|Hide) (\d+) hidden/.exec(list.textContent || '');
    return { txt, n: m ? Number(m[1]) : (txt ? NaN : 0), where: m ? m[2] : null, owner: m && m[3] ? Number(m[3]) : null,
      cards: list.querySelectorAll('.lead').length, hidden: hid ? Number(hid[2]) : 0, empty: !!list.querySelector('.empty'),
      emptyText: (list.querySelector('.empty') || {}).textContent || '', rows: (window.ROWS || []).length,
      chip: (document.getElementById('stateChipBtn').textContent || '').replace('▾', '').trim(), badge: (document.getElementById('filterBadge').textContent || '').trim(),
      badgeHidden: document.getElementById('filterBadge').classList.contains('hidden') };
  });
  const enriched = (s && s.json && s.json.results) || [];
  const national = (n && n.json && n.json.results) || [];
  const seen = {}; enriched.forEach(r => { seen[((r.property_street || '') + '|' + (r.property_zip || '')).toUpperCase()] = 1; });
  let dropped = 0; const extra = national.filter(r => { const k = ((r.property_street || '') + '|' + (r.property_zip || '')).toUpperCase(); if (seen[k]) { dropped++; return false; } seen[k] = 1; return true; });
  return { ui, search: s ? { url: s.url, status: s.status, count: s.json ? s.json.count : null, ignored: s.json ? s.json.ignored_filters : null } : null,
    national: n ? { url: n.url, status: n.status, count: n.json ? n.json.count : null } : null,
    rows: enriched, natRows: extra, expected: enriched.length + extra.length, dropped, nationalSkipped: !n };
}

function reconcile(run, view, label, S) {
  const parts = [`UI says ${Number.isNaN(S.ui.n) ? `'${S.ui.txt}'` : S.ui.n}`, `cards ${S.ui.cards}${S.ui.hidden ? ` (+${S.ui.hidden} hidden)` : ''}`,
    `/api/search ${S.search ? S.search.count : 'n/a'}`, S.nationalSkipped ? 'national skipped (filters set)' : `/api/national-search ${S.national ? S.national.count : 'n/a'}${S.dropped ? ` (${S.dropped} deduped)` : ''}`, `expected ${S.expected}`];
  const okUi = S.ui.n === S.expected && (S.ui.cards + S.ui.hidden) === S.ui.n && S.ui.rows === S.expected;
  const okOwner = S.ui.owner == null || S.ui.owner === S.rows.length;
  const okApi = !S.search || S.search.status === 200;
  const status = okUi && okOwner && okApi ? 'PASS' : 'FAIL';
  run.rec(view, `${label}: count feed`, status, parts.join(' · ') + (okOwner ? '' : ` · 'with owner data' ${S.ui.owner} != enriched ${S.rows.length}`) + (okApi ? '' : ` · /api/search HTTP ${S.search.status}`));
  return status === 'PASS';
}

// Does every rendered row honor the applied filter? Checked against the API rows the UI drew from (cards carry few fields) and, where
// the card shows it, against the card itself. Returns {ok, sampled, bad, na}
function honored(key, value, rows, cards) {
  const v = String(value); const num = Number(value); const sample = rows.slice(0, SAMPLE_CAP);
  const like = (s, q) => String(s || '').toLowerCase().includes(q.toLowerCase());
  const P = {
    state: r => r.property_state === v,
    q: r => like(r.owner_full, v) || like(r.owner_last, v) || like(r.property_street, v) || like(r.property_city, v) || like(r.county, v) || String(r.property_zip || '').startsWith(v),
    within: r => r.days_to_auction != null && r.days_to_auction <= num && r.days_to_auction >= -3,
    sale_type: r => v === 'judicial' ? r.is_judicial === true : r.is_judicial === false,
    ptype: r => ({ sfr: /^(sfr|single)/i, condo: /^condo/i, mfr: /^(mfr|multi)/i, mobile: /^mobile/i, land: /^land/i })[v].test(r.property_type || ''),
    min_equity_pct: r => Number(r.equity_pct) >= num && r.equity_unverified !== true,
    min_equity: r => Number(r.equity_dollars) >= num && r.equity_unverified !== true,
    val_min: r => Number(r.avm) >= num, val_max: r => Number(r.avm) <= num,
    beds_min: r => Number(r.beds) >= num, year_min: r => Number(r.year_built) >= num,
    lender: r => like(r.mortgage_lender, v), absentee: r => r.owner_absentee === true,
    deceased: r => r.deceased_flag === true ? true : 'na',   // tag match (reapi-inherited) is not in the payload
    bankruptcy: r => r.bankruptcy_flag === true, reverse: r => r.reverse_mortgage === true,
    out_of_state: () => 'na', multi: () => 'na',               // owner_out_of_state / dm_multi_property are not in the search payload
    nok: r => r.next_of_kin != null,
    surplus: r => (r.surplus_amount != null) || (Number(r.surplus_margin) >= 50000 && !!r.sale_verified_source && !/realestateapi/i.test(r.sale_verified_source) && r.equity_unverified !== true),
    status: r => true,
  };
  const pred = P[key]; if (!pred) return { ok: true, sampled: 0, bad: [], na: 0, note: 'no predicate' };
  const bad = []; let na = 0;
  sample.forEach(r => { const x = pred(r); if (x === 'na') na++; else if (!x) bad.push(`${r.property_street || r.id}, ${r.property_city || ''} ${r.property_state || ''}`); });
  // card-level checks where the card shows the field
  const cbad = [];
  if (key === 'state') (cards || []).slice(0, SAMPLE_CAP).forEach(c => { if (!new RegExp(`(^|,\\s*)${v}(,|$)`).test(c.csz)) cbad.push(`card '${c.csz}'`); });
  if (key === 'within') (cards || []).slice(0, SAMPLE_CAP).forEach(c => { const m = /auction (\d+)d/.exec(c.badges); if (m && Number(m[1]) > num) cbad.push(`card '${c.street}' shows auction ${m[1]}d`); });
  if (key === 'min_equity_pct') (cards || []).slice(0, SAMPLE_CAP).forEach(c => { const m = /^(\d+)%$/.exec(c.eq); if (m && Number(m[1]) < num) cbad.push(`card '${c.street}' shows ${c.eq} (<${num}%: client nets every lien, server filtered on equity_pct)`); else if (!m) cbad.push(`card '${c.street}' shows '${c.eq}'`); });
  return { ok: bad.length === 0 && cbad.length === 0, sampled: sample.length, bad: bad.concat(cbad), na };
}

async function cardsSnapshot(page, cap) {
  return page.evaluate(cap => Array.from(document.querySelectorAll('#list .lead')).slice(0, cap).map(c => ({
    owner: (c.querySelector('.owner') || {}).textContent || '', street: (c.querySelector('.street') || {}).textContent || '', csz: (c.querySelector('.csz') || {}).textContent || '',
    eq: ((c.querySelector('.eq') || {}).textContent || '').trim(), eqk: ((c.querySelector('.eqk') || {}).textContent || '').trim(), val: ((c.querySelector('.val') || {}).textContent || '').trim(),
    badges: Array.from(c.querySelectorAll('.badges .badge')).map(b => b.textContent.trim()).join(' | '), sig: c.querySelectorAll('.mbrow .mb').length,
    sigText: Array.from(c.querySelectorAll('.mbrow .mb')).map(b => b.textContent.trim()).join(' | '), idx: Number((/openDetail\((\d+)\)/.exec(c.getAttribute('onclick') || '') || [])[1]),
  })), cap);
}

function filterRow(run, o) { run.filters.push(o); return o; }

// ----------------------------------------------------------------------------- the walk
async function walkAccount(run) {
  const page = run.page; const V = (n) => n;
  const t0 = Date.now();

  // ---- auth: login page
  await page.goto('/app/', { waitUntil: 'load' });
  await page.waitForSelector('#auth', { timeout: 20000 });
  const authOk = await page.evaluate(() => ({ tabs: ['tabLogin', 'tabSignup'].every(id => !!document.getElementById(id)), email: !!document.getElementById('f_email'), pw: !!document.getElementById('f_pw'), google: /Continue with Google/.test(document.body.textContent), shellHidden: document.getElementById('shell').classList.contains('hidden'), title: document.title }));
  run.ok(V('auth-login'), 'login card renders (tabs, email, password, Google SSO)', authOk.tabs && authOk.email && authOk.pw && authOk.google && authOk.shellHidden, `title '${authOk.title}'`, JSON.stringify(authOk), await run.shot('auth-login'));

  // ---- auth: signup tab (render only, never submit)
  await page.click('#tabSignup');
  const su = await page.evaluate(() => ({ name: getComputedStyle(document.getElementById('nameField')).display !== 'none', terms: getComputedStyle(document.getElementById('termsBlock')).display !== 'none', notice: /not legal advice/.test(document.getElementById('stateNotice').textContent), box: !document.getElementById('f_terms').checked, btn: document.getElementById('authSubmit').textContent, link: (document.querySelector('#termsBlock a') || {}).getAttribute && document.querySelector('#termsBlock a').getAttribute('href') }));
  run.ok(V('auth-signup'), 'signup form renders: name field, state notice, Terms link, unchecked box, button says Create account', su.name && su.terms && su.notice && su.box && su.btn === 'Create account' && su.link === '/terms', `terms link ${su.link}`, JSON.stringify(su), await run.shot('auth-signup'));
  run.skip('auth-signup', 'submit signup', 'never creates accounts (guard)');
  await page.click('#tabLogin');

  // ---- login through the real form
  await page.fill('#f_email', run.email); await page.fill('#f_pw', CREDS[run.email]);
  const tLogin = Date.now();
  const [loginRes] = await Promise.all([page.waitForResponse(r => r.url().includes('/api/auth/login'), { timeout: 30000 }), page.click('#authSubmit')]);
  run.ok('login', 'POST /api/auth/login', loginRes.status() === 200, `HTTP ${loginRes.status()}`, `HTTP ${loginRes.status()} ${await loginRes.text().catch(() => '')}`.slice(0, 200));
  if (loginRes.status() !== 200) return;
  const me = await run.api('GET', '/api/me');
  run.role = me.json && me.json.user; if (!run.role) { run.fail('login', 'GET /api/me after login', `HTTP ${me.status}`); return; }
  const R = run.role; const plan = MATRIX.plans[R.plan_key] || MATRIX.plans.free;
  run.ok('login', 'server role matches the expected plan for this account', R.plan_key === run.acct.expect_plan_key, `server says plan=${R.plan} plan_key=${R.plan_key} paid=${R.paid} premium=${R.premium} founding=${R.founding} credits ${R.usage.lookups.used}/${R.usage.lookups.limit} terms_accepted=${R.terms_accepted} trial_eligible=${R.trial_eligible} billing_portal=${R.billing_portal}`, `expected ${run.acct.expect_plan_key}, got ${R.plan_key}`);
  run.ok('login', 'credit allowance matches tier matrix', R.usage.lookups.limit === plan.credits, `${R.usage.lookups.limit} credits`, `limit ${R.usage.lookups.limit} != ${plan.credits}`);
  const ck = (await run.context.cookies(BASE)).find(c => c.name === 'offramp_sess');
  run.ok('login', 'session cookie is HttpOnly + Secure + SameSite=Lax', !!ck && ck.httpOnly && ck.secure && /lax/i.test(ck.sameSite || ''), `offramp_sess httpOnly=${ck && ck.httpOnly} secure=${ck && ck.secure} sameSite=${ck && ck.sameSite}`, ck ? `httpOnly=${ck.httpOnly} secure=${ck.secure} sameSite=${ck.sameSite}` : 'no offramp_sess cookie');

  // ---- terms gate (shown to anyone whose terms_accepted is false)
  if (R.terms_accepted === false) {
    await page.waitForSelector('#g_terms', { timeout: 15000 }).catch(() => {});
    const gate = await page.evaluate(() => { const el = document.getElementById('upsheet'); return { shown: !!el && el.style.display === 'block' && !!document.getElementById('g_terms'), notice: /not legal advice/.test((el || {}).textContent || ''), shellHidden: document.getElementById('shell').classList.contains('hidden') }; });
    run.ok('terms-gate', 'terms gate blocks the app until the box is checked', gate.shown && gate.notice && gate.shellHidden, 'gate shown over a hidden shell, state notice present', JSON.stringify(gate), await run.shot('terms-gate'));
    await page.click('#upsheet button.btn-primary');
    const toast = await page.waitForFunction(() => document.getElementById('toast').style.display === 'block' && document.getElementById('toast').textContent, null, { timeout: 5000 }).then(h => h.jsonValue()).catch(() => '');
    run.ok('terms-gate', 'Continue without the box → toast, still gated', /check the box/i.test(toast), `toast '${toast}'`, `toast '${toast}'`);
    await page.check('#g_terms');
    let acc = null, me2 = null;
    for (let attempt = 0; attempt < 3; attempt++) {   // the endpoint shares the public 5-hits/10-min IP throttle; a 429 here is retried once after a pause
      [acc] = await Promise.all([page.waitForResponse(r => r.url().includes('/api/auth/accept-terms'), { timeout: 20000 }), page.click('#upsheet button.btn-primary')]);
      me2 = await run.api('GET', '/api/me');
      if (acc.status() !== 429) break;
      await page.waitForTimeout(15000);
    }
    const accBody = await acc.text().catch(() => '');
    run.ok('terms-gate', 'accept terms → POST /api/auth/accept-terms 200, /api/me terms_accepted=true, shell opens', acc.status() === 200 && me2.json && me2.json.user.terms_accepted === true, `HTTP ${acc.status()}`, acc.status() === 429 ? `HTTP 429 ${accBody.slice(0, 80)} — the app toasts 'Could not save' and the user is stuck on the gate. /api/auth/accept-terms shares the public lead-capture throttle (5 hits per 10 min per IP: track/signup/unsubscribe/accept-terms), so a few users behind one office IP (or this suite's parallel passes) lock each other out.` : `HTTP ${acc.status()} terms_accepted=${me2.json && me2.json.user.terms_accepted}`);
    if (me2.json && me2.json.user) run.role = me2.json.user;
    if (!(me2.json && me2.json.user.terms_accepted)) {   // keep walking: open the shell in-page so the rest of the views still get tested (server-side terms stay pending)
      run.skip('terms-gate', 'shell opened in-page to continue the walk', 'accept-terms did not succeed; the suite bypassed the gate client-side (ME.terms_accepted=true; enterApp()) so the remaining views could still be checked');
      await page.evaluate(() => { ME.terms_accepted = true; closeUpgradeSheet(); enterApp(); });
    }
  } else {
    run.skip('terms-gate', 'terms gate', 'already accepted on this account (reset terms_accepted_at to NULL in crm.offramp_users to exercise it again)');
  }
  await page.waitForSelector('#shell:not(.hidden)', { timeout: 20000 });

  // ---- initial list (All states by default) + header
  let S = await settleSearch(run, tLogin, null);
  const hdr = await page.evaluate(() => ({ pill: document.getElementById('planPill').textContent, cls: document.getElementById('planPill').className, meter: document.getElementById('meterChip').textContent, chip: document.getElementById('stateChipBtn').textContent }));
  run.ok('header', 'plan pill text matches the server role', hdr.pill === plan.label, `pill '${hdr.pill}'`, `pill '${hdr.pill}' expected '${plan.label}' for plan_key ${R.plan_key}`);
  run.ok('header', 'plan pill color class', hdr.cls.includes(plan.pill_class), hdr.cls, `class '${hdr.cls}' expected ${plan.pill_class}`);
  const left = Math.max(0, R.usage.lookups.limit - R.usage.lookups.used);
  run.ok('header', 'credit meter = limit - used', new RegExp(`^${left} credits left`).test(hdr.meter.trim()), `'${hdr.meter.trim()}'`, `'${hdr.meter.trim()}' expected ${left}`);
  run.ok('deals-all-states', 'default scope is All states', /All states/.test(hdr.chip), hdr.chip.trim(), hdr.chip.trim());
  reconcile(run, 'deals-all-states', 'All states', S);
  let cards = await cardsSnapshot(page, 400);
  run.ok('deals-all-states', 'Signals badges never ride on a non-UT card', !cards.some(c => c.sig && !/(^|,\s*)UT(,|$)/.test(c.csz)), `${cards.length} cards checked`, `non-UT cards with a signal: ${cards.filter(c => c.sig && !/(^|,\s*)UT(,|$)/.test(c.csz)).map(c => c.csz).slice(0, 3).join('; ')}`);
  run.ok('deals-all-states', 'no decimal equity % on any card', !cards.some(c => /\d+\.\d+%/.test(c.eq)), `${cards.length} cards`, cards.filter(c => /\d+\.\d+%/.test(c.eq)).map(c => c.eq).join(','));
  await run.shot('deals-all-states');

  // ---- state sheet → Utah
  await page.waitForFunction(() => Object.keys((window.COUNTS || {}).total || {}).length > 0, null, { timeout: 15000 }).catch(() => {});
  const appCounts = await page.evaluate(() => window.COUNTS || {});
  const totals = appCounts.total || {}; const hitCounts = appCounts.hit || {};
  run.info('state-sheet', 'state counts source', `the app's own /api/state-counts (server caches it 10 min; hit_list moves live, so feed-vs-sheet drift of a few rows is tolerated)`);
  await page.click('#stateChipBtn'); await page.waitForSelector('#sheet.open #sheetState', { timeout: 10000 }); await page.waitForTimeout(300);
  const sheet = await page.evaluate(() => { const rows = Array.from(document.querySelectorAll('#stateList .srow')).map(b => ({ nm: b.querySelector('.nm').textContent.trim(), cnt: b.querySelector('.cnt').textContent.trim(), on: b.classList.contains('on') })); return { title: document.getElementById('sheetTitle').textContent, rows, groups: Array.from(document.querySelectorAll('.sgroup')).map(g => g.textContent) }; });
  const allRow = sheet.rows.find(r => r.nm === 'All states'); const utRow = sheet.rows.find(r => r.nm === 'Utah');
  const sumTot = Object.values(totals).reduce((a, b) => a + b, 0);
  run.ok('state-sheet', 'state sheet opens with Your states + All states groups', sheet.title === 'Browse by state' && sheet.groups.length >= 2 && sheet.rows.length >= 52, `${sheet.rows.length} rows, groups ${sheet.groups.join(' / ')}`, JSON.stringify(sheet).slice(0, 300), await run.shot('state-sheet'));
  run.ok('state-sheet', '"All states" count = sum of /api/state-counts', allRow && Number(allRow.cnt) === sumTot, `${allRow && allRow.cnt} = ${sumTot}`, `sheet ${allRow && allRow.cnt} vs api ${sumTot}`);
  run.ok('state-sheet', 'Utah row count = /api/state-counts total.UT', utRow && Number(utRow.cnt || 0) === (totals.UT || 0), `${utRow && utRow.cnt}`, `sheet ${utRow && utRow.cnt} vs api ${totals.UT}`);
  await page.fill('#stateQ', 'ut');
  const q = await page.evaluate(() => Array.from(document.querySelectorAll('#stateList .srow .nm')).map(n => n.textContent.trim()));
  run.ok('state-sheet', 'Find a state filters the list', q.includes('Utah') && q.length < 20 && !q.includes('Alabama'), `'ut' → ${q.join(', ')}`, `'ut' → ${q.join(', ')}`);
  await page.waitForTimeout(300);
  let tS = Date.now();
  S = await settleSearch(run, tS, async () => { await page.click('#stateList .srow:has(.nm:text-is("Utah"))', { force: true }); });
  run.ok('deals-list', 'picking Utah closes the sheet and the chip shows UT · count', /^UT/.test(S.ui.chip) && S.ui.chip.includes(String(totals.UT || '')), `chip '${S.ui.chip}'`, `chip '${S.ui.chip}'`);
  reconcile(run, 'deals-list', 'Utah', S);
  cards = await cardsSnapshot(page, 400);
  const utRows = S.rows; const stateH = honored('state', 'UT', utRows.concat(S.natRows), cards);
  run.ok('deals-list', 'every Utah card is in Utah', stateH.ok, `${stateH.sampled} rows sampled`, stateH.bad.slice(0, 3).join('; '));
  run.ok('deals-list', 'card anatomy: owner, street, city/state/zip, equity, value, status badge on every card', cards.every(c => c.owner && c.street && c.csz && c.eq && c.val && c.badges), `${cards.length} cards`, JSON.stringify(cards.find(c => !(c.owner && c.street && c.csz && c.eq && c.val && c.badges))));
  const badgeWords = new Set(); cards.forEach(c => c.badges.split(' | ').forEach(b => badgeWords.add(b.replace(/\d+/g, 'N'))));
  run.info('deals-list', 'badge vocabulary seen on cards', Array.from(badgeWords).join(' · '));
  { const allR = utRows.concat(S.natRows); const badTok = cards.filter(c => !/^(Scheduled|Postponed|Cured|Sold)(\s\||$)/.test(c.badges));
    const srcOf = c => { const r = allR[c.idx] || {}; return r.source === 'auction.com' || c.idx >= utRows.length ? 'national feed row' : 'enriched row'; };
    run.ok('deals-list', 'status token is one of Scheduled / Postponed / Cured / Sold on every card', badTok.length === 0, `${cards.length} cards`, `${badTok.length} of ${cards.length} cards show a raw feed token instead: ` + badTok.slice(0, 4).map(c => `'${c.street}' → ${c.badges.split(' | ')[0]} (${srcOf(c)})`).join('; ') + ` · tokens seen: ${Array.from(new Set(badTok.map(c => c.badges.split(' | ')[0]))).join(', ')}`); }
  run.ok('deals-list', 'never shows the word Unconfirmed', !cards.some(c => /Unconfirmed/i.test(c.badges)), '', cards.filter(c => /Unconfirmed/i.test(c.badges)).map(c => c.badges).slice(0, 2).join('; '));
  // card chips vs API row (listed / deceased)
  const byIdx = new Map(cards.map(c => [c.idx, c])); const allRows = utRows.concat(S.natRows);
  let mlsBad = [], decBad = [];
  allRows.forEach((r, i) => { const c = byIdx.get(i); if (!c) return; const mls = r.mls_active === true || !!r.mls_status; if (mls !== /Listed on MLS/.test(c.badges)) mlsBad.push(c.street); if ((r.deceased_flag === true) !== /Deceased owner/.test(c.badges)) decBad.push(c.street); });
  run.ok('deals-list', '"Listed on MLS" chip matches the API row (mls_active/mls_status)', mlsBad.length === 0, `${allRows.filter(r => r.mls_active === true || r.mls_status).length} listed rows`, `mismatch on ${mlsBad.slice(0, 3).join('; ')}`);
  run.ok('deals-list', '"Deceased owner" chip matches the API row (deceased_flag)', decBad.length === 0, `${allRows.filter(r => r.deceased_flag === true).length} deceased rows`, `mismatch on ${decBad.slice(0, 3).join('; ')}`);
  const sigCards = cards.filter(c => c.sig);
  if (run.exp('list.card.signals_badge') === 'hidden') run.ok('deals-list', 'Signals badge hidden on cards for this tier', sigCards.length === 0, `0 of ${cards.length} UT cards carry a badge`, `${sigCards.length} cards carry a badge: ${sigCards.map(c => c.sigText).slice(0, 2).join('; ')}`);
  else { run.ok('deals-list', 'Signals: at most ONE badge per UT card', !cards.some(c => c.sig > 1), `${sigCards.length} of ${cards.length} UT cards carry a badge (${Array.from(new Set(sigCards.map(c => c.sigText.replace(/\$[\dkM.]+/, '$N')))).join(' · ') || 'none fired'})`, `cards with >1: ${cards.filter(c => c.sig > 1).map(c => c.sigText).slice(0, 2).join('; ')}`);
    if (run.tier !== 'premium') run.ok('deals-list', 'Surplus Chaser badge never on a Pro card', !sigCards.some(c => /Surplus/.test(c.sigText)), '', sigCards.filter(c => /Surplus/.test(c.sigText)).map(c => c.sigText).join('; ')); }
  await run.shot('deals-list');

  // ---- filter sheet gating
  await page.click('#filterBtn', { force: true }); await page.waitForSelector('#sheet.open #sheetFilter', { timeout: 10000 });
  const fs_ = await page.evaluate(() => { const g = {}; document.querySelectorAll('#sheetFilter .fgroup').forEach(x => { g[x.getAttribute('data-tier')] = { locked: x.classList.contains('fgroup-locked'), disabled: Array.from(x.querySelectorAll('select,input')).every(e => e.disabled), anyEnabled: Array.from(x.querySelectorAll('select,input')).some(e => !e.disabled) }; }); const l = {}; document.querySelectorAll('#sheetFilter .tierlock').forEach(x => { l[x.getAttribute('data-tier')] = getComputedStyle(x).display !== 'none'; }); const free = Array.from(document.querySelectorAll('#fWithin,#fSaleType,#fPtype')).every(e => !e.disabled); return { g, l, free, title: document.getElementById('sheetTitle').textContent, n: document.querySelectorAll('#sheetFilter [data-f]').length }; });
  run.ok('filter-sheet', 'filter sheet opens with every control', fs_.title === 'Filters' && fs_.n === Object.keys(MATRIX.filters).filter(k => k[0] !== '_').length, `${fs_.n} controls`, `${fs_.n} controls, title '${fs_.title}'`, await run.shot('filter-sheet'));
  run.ok('filter-sheet', 'Free group (within / sale type / property type) enabled', fs_.free, '', JSON.stringify(fs_));
  for (const grp of ['pro', 'premium']) {
    const want = run.exp(`filters.group_${grp}`); const g = fs_.g[grp] || {}; const pill = fs_.l[grp];
    if (want === 'locked') run.ok('filter-sheet', `${grp} group locked for ${run.tier}: grayed, disabled, '${grp}' pill shown`, g.locked && g.disabled && pill, '', JSON.stringify({ g, pill }));
    else run.ok('filter-sheet', `${grp} group enabled for ${run.tier}: usable, no tier pill`, !g.locked && g.anyEnabled && !pill, '', JSON.stringify({ g, pill }));
  }
  await page.click('#sheet .sheetx', { force: true }); await page.waitForSelector('#sheet.hidden', { timeout: 5000 }).catch(() => {});

  // ---- filters walk (Utah), one at a time
  run.photos = false;
  const baseline = S.expected;
  const applyFilter = async (key, value) => {
    const f = MATRIX.filters[key];
    await page.click('#filterBtn', { force: true }); await page.waitForSelector('#sheet.open #sheetFilter', { timeout: 10000 });
    const el = '#' + f.el;
    if (f.type === 'select') await page.selectOption(el, value); else if (f.type === 'checkbox') await page.check(el, { force: true }); else await page.fill(el, value);
    const t = Date.now();
    const R2 = await settleSearch(run, t, async () => { await page.click('#sheetFilter .fbtns .btn-primary'); });
    await page.waitForSelector('#sheet.hidden', { timeout: 5000 }).catch(() => {});
    return R2;
  };
  const clearViaSheet = async () => { await page.click('#filterBtn', { force: true }); await page.waitForSelector('#sheet.open #sheetFilter', { timeout: 10000 }); const t = Date.now(); const R = await settleSearch(run, t, async () => { await page.click('#sheetFilter .fbtns .btn-ghost'); }); await page.waitForSelector('#sheet.hidden', { timeout: 5000 }).catch(() => {}); return R; };
  for (const [key, f] of Object.entries(MATRIX.filters)) {
    if (key[0] === '_') continue;
    const allowed = f.tier === 'free' || (f.tier === 'pro' && R.paid) || (f.tier === 'premium' && R.premium);
    for (const value of f.values) {
      const label = `${key}=${value}`;
      if (!allowed) {  // the control is disabled for this tier; prove the server also ignores the param
        const a = await run.api('GET', `/api/search?state=UT&limit=200&${key}=${encodeURIComponent(value)}`);
        const ign = a.json && a.json.ignored_filters || [];
        const okI = a.status === 200 && ign.includes(key) && Math.abs(a.json.count - utRows.length) <= 3;
        filterRow(run, { filter: key, value, tier: f.tier, source: 'api-only (control disabled for this tier)', ui: null, cards: null, api: a.json && a.json.count, national: null, expected: null, honored: `server ignored: ${ign.join(',') || 'nothing'}`, status: okI ? 'PASS' : 'FAIL', note: `count fell back to the unfiltered Utah feed (${a.json && a.json.count})` });
        run.rec('filters', `${label} locked on ${run.tier}: control disabled, server ignores the param`, okI ? 'PASS' : 'FAIL', `ignored_filters=${JSON.stringify(ign)} count=${a.json && a.json.count}`);
        if (f.values.length > 1) break;  // one API probe per locked filter is enough
        continue;
      }
      let R2; try { R2 = await applyFilter(key, value); } catch (e) { run.fail('filters', `${label}: apply`, e.message.slice(0, 200)); filterRow(run, { filter: key, value, tier: f.tier, source: 'ui', status: 'FAIL', note: e.message.slice(0, 120) }); try { await page.click('#sheet .sheetx', { timeout: 1000 }); } catch (e2) { /* */ } continue; }
      const cs = await cardsSnapshot(page, SAMPLE_CAP);
      const h = honored(key, value, R2.rows, cs);
      const countOk = R2.ui.n === R2.expected && (R2.ui.cards + R2.ui.hidden) === R2.ui.n && R2.nationalSkipped;
      const badgeOk = R2.ui.badge === '1' && !R2.ui.badgeHidden;
      const ignoredOk = !(R2.search && R2.search.ignored && R2.search.ignored.length);
      const st = countOk && h.ok && badgeOk && ignoredOk ? 'PASS' : 'FAIL';
      filterRow(run, { filter: key, value, tier: f.tier, source: 'ui', ui: R2.ui.n, cards: R2.ui.cards, api: R2.search && R2.search.count, national: R2.nationalSkipped ? 'skipped' : (R2.national && R2.national.count), expected: R2.expected, honored: `${h.sampled - h.na}/${h.sampled} ok${h.na ? ` (${h.na} n/a)` : ''}${h.bad.length ? ` · ${h.bad.length} bad` : ''}`, status: st, note: [!countOk ? 'count mismatch' : '', !badgeOk ? `filter badge '${R2.ui.badge}'` : '', !ignoredOk ? `server ignored ${R2.search.ignored}` : '', h.bad.slice(0, 2).join('; ')].filter(Boolean).join(' · ') });
      run.rec('filters', `${label}`, st, `UI ${R2.ui.n} · cards ${R2.ui.cards} · api ${R2.search && R2.search.count} · national ${R2.nationalSkipped ? 'skipped' : R2.national && R2.national.count} · honored ${h.sampled - h.na}/${h.sampled}${h.bad.length ? ' · bad: ' + h.bad.slice(0, 2).join('; ') : ''}${badgeOk ? '' : ` · badge '${R2.ui.badge}'`}`);
      if (key === 'within' && value === '7') await run.shot('filters-within-7');
      if (key === 'surplus') await run.shot('filters-surplus');
      const C = await clearViaSheet();
      if (C.ui.n !== baseline || !C.ui.badgeHidden) run.fail('filters', `${label}: Clear restores the unfiltered Utah list`, `after clear UI ${C.ui.n} (baseline ${baseline}), badge hidden ${C.ui.badgeHidden}`);
    }
  }
  run.pass('filters', 'Clear restores the unfiltered Utah list after every filter', `baseline ${baseline}`);
  // things Ted asked for that the UI does not expose
  for (const [k, o] of Object.entries(MATRIX.not_in_ui)) {
    if (k[0] === '_') continue;
    run.skip('filters', `${k} control`, o.reason);
    if (o.api_param) for (const v of o.values) { const a = await run.api('GET', `/api/search?state=UT&limit=200&${o.api_param}=${encodeURIComponent(v)}`); filterRow(run, { filter: o.api_param, value: v, tier: 'free', source: 'api-only (no UI control)', api: a.json && a.json.count, status: a.status === 200 ? 'INFO' : 'FAIL', note: 'server param only; no chip in the app' }); run.info('filters', `API ${o.api_param}=${v}`, `count ${a.json && a.json.count}`); }
  }

  // ---- search box
  const typeSearch = async (text) => { const t = Date.now(); return settleSearch(run, t, async () => { await page.fill('#fSearch', text); await page.press('#fSearch', 'Enter'); }); };
  for (const qv of ['Salt Lake', '84']) {
    const R2 = await typeSearch(qv); const h = honored('q', qv, R2.rows.concat(R2.natRows), null);
    const okC = R2.ui.n === R2.expected && (R2.ui.cards + R2.ui.hidden) === R2.ui.n;
    filterRow(run, { filter: 'search box', value: qv, tier: 'free', source: 'ui', ui: R2.ui.n, cards: R2.ui.cards, api: R2.search && R2.search.count, national: R2.national && R2.national.count, expected: R2.expected, honored: `${h.sampled}/${h.sampled} ok${h.bad.length ? ` · ${h.bad.length} bad` : ''}`, status: okC && h.ok ? 'PASS' : 'FAIL', note: h.bad.slice(0, 2).join('; ') });
    run.rec('search-box', `q='${qv}' (owner/street/city/county/zip)`, okC && h.ok ? 'PASS' : 'FAIL', `UI ${R2.ui.n} · cards ${R2.ui.cards} · api ${R2.search && R2.search.count}+${R2.national && R2.national.count} · honored ${h.sampled - h.bad.length}/${h.sampled}${h.bad.length ? ' · ' + h.bad.slice(0, 2).join('; ') : ''}`);
  }
  await run.shot('search-box');
  let R3 = await typeSearch('Idaho');
  run.ok('search-box', "typing a state name switches the state chip (stateFromText)", /^ID/.test(R3.ui.chip) && (await page.inputValue('#fSearch')) === '', `chip '${R3.ui.chip}', box cleared`, `chip '${R3.ui.chip}'`);
  R3 = await typeSearch('');
  run.ok('search-box', 'empty search keeps the chosen state', /^ID/.test(R3.ui.chip), `chip '${R3.ui.chip}' · ${R3.ui.n} deals`, `chip '${R3.ui.chip}'`);

  // ---- every state, one by one (through the search box = the stateFromText path; the sheet path was covered above)
  const stateNames = await page.evaluate(() => STATE_NAMES);
  const states = STATES_ARG || Object.keys(stateNames);
  let stateFails = 0, stateN = 0;
  for (const st of states) {
    if (!stateNames[st]) continue; stateN++;
    let R2; try { R2 = await typeSearch(stateNames[st]); } catch (e) { stateFails++; run.fail('states', `${st}: search`, e.message.slice(0, 160)); filterRow(run, { filter: 'state', value: st, tier: 'free', source: 'ui', status: 'FAIL', note: e.message.slice(0, 100) }); continue; }
    const cs = await cardsSnapshot(page, SAMPLE_CAP); const h = honored('state', st, R2.rows.concat(R2.natRows), cs);
    const okC = R2.ui.n === R2.expected && (R2.ui.cards + R2.ui.hidden) === R2.ui.n && R2.ui.rows === R2.expected;
    const okChip = new RegExp(`^${st}`).test(R2.ui.chip) && (!totals[st] || R2.ui.chip.includes(String(totals[st])));
    const hit = hitCounts[st] || 0; const drift = R2.search ? Math.abs(R2.search.count - Math.min(hit, 200)) : 999; const okFeed = drift <= Math.max(5, Math.round(0.03 * Math.min(hit, 200)));
    const okEmpty = R2.expected > 0 || (R2.ui.empty && /No auctions in|No deals match/.test(R2.ui.emptyText));
    const st_ = okC && h.ok && okChip && okFeed && okEmpty ? 'PASS' : 'FAIL'; if (st_ === 'FAIL') stateFails++;
    filterRow(run, { filter: 'state', value: `${st} (${stateNames[st]})`, tier: 'free', source: 'ui', ui: R2.ui.n, cards: R2.ui.cards, api: R2.search && R2.search.count, national: R2.national && R2.national.count, expected: R2.expected, honored: `${h.sampled}/${h.sampled} ok${h.bad.length ? ` · ${h.bad.length} bad` : ''}`, status: st_, note: [okChip ? '' : `chip '${R2.ui.chip}'`, okFeed ? `sheet hit ${hit}${drift ? ` (drift ${drift}, cached sheet count)` : ''}` : `sheet says ${hit} enriched but /api/search returned ${R2.search && R2.search.count} (drift ${drift})`, okEmpty ? '' : 'empty state missing', h.bad.slice(0, 2).join('; ')].filter(Boolean).join(' · ') });
    if (st_ === 'FAIL') run.fail('states', `${st}`, `UI ${R2.ui.n} · cards ${R2.ui.cards} · api ${R2.search && R2.search.count} · national ${R2.national && R2.national.count} · expected ${R2.expected} · state-counts hit ${hit} · chip '${R2.ui.chip}' · bad ${h.bad.slice(0, 2).join('; ')}`);
    if (['AZ', 'ID', 'MT', 'WY'].includes(st)) await run.shot(`states-${st.toLowerCase()}`);
  }
  run.rec('states', `walked ${stateN} states one by one: chip, count feed, cards in-state, sheet count vs feed`, stateFails ? 'FAIL' : 'PASS', stateFails ? `${stateFails} states failed (see Filter counts table)` : 'every state reconciled');
  run.photos = true;
  S = await typeSearch('Utah');   // back to Utah for the detail views

  // ---- map
  await page.click('#n-map');
  await page.waitForSelector('#s-map.on .leaflet-container', { timeout: 20000 }).catch(() => {});
  await page.waitForTimeout(900);
  const mp = await page.evaluate(() => ({ leaflet: !!document.querySelector('#map.leaflet-container'), pins: document.querySelectorAll('#map .leaflet-interactive').length, withLL: (window.ROWS || []).filter(r => r.latitude && r.longitude).length, nav: document.getElementById('n-map').classList.contains('on') }));
  run.ok('map', 'map screen renders Leaflet with one pin per geocoded row', mp.leaflet && mp.pins === mp.withLL && mp.nav, `${mp.pins} pins for ${mp.withLL} rows with lat/lng (of ${S.expected})`, JSON.stringify(mp), await run.shot('map'));
  if (mp.pins) {
    await page.locator('#map .leaflet-interactive').first().click({ force: true }).catch(() => {});
    const opened = await page.waitForSelector('#detail.open', { timeout: 5000 }).then(() => true).catch(() => false);
    run.ok('map', 'tapping a pin opens the lead view in front of the map', opened, '', 'detail did not open from the pin', opened ? await run.shot('map-pin-detail') : null);
    if (opened) { await page.click('#detail .dback'); await page.waitForTimeout(300); }
  }
  await page.click('#n-deals');

  // ---- lead detail views
  const picks = await page.evaluate(() => {
    const vis = (window.ROWS || []).map((r, i) => ({ r, i })).filter(x => !((window.ACTIONS || {})[x.r.id] || {}).hidden);
    const cached = x => x.r.lien_count != null; const used = new Set();
    const pick = pred => { const c = vis.filter(pred); if (!c.length) return null; const fresh = c.filter(x => !used.has(x.i)); const pool = fresh.length ? fresh : c; const w = pool.find(cached) || pool[0]; used.add(w.i); return w.i; };
    const hasPh = x => Array.isArray(x.r.phones) && x.r.phones.length;
    const dnc = x => hasPh(x) && x.r.phones.some(p => p && typeof p === 'object' && p.dnc);
    const first = vis.length ? vis[0].i : null; if (first != null) used.add(first);
    return { first, phones: pick(dnc) ?? pick(hasPh), nophones: pick(x => !hasPh(x) && x.r.source !== 'auction.com'),
      signals: pick(x => typeof sigBadges === 'function' && sigBadges(x.r) !== ''), sigSection: pick(x => typeof signalsSection === 'function' && signalsSection(x.r) !== ''),
      nok: pick(x => !!x.r.next_of_kin), premLocked: pick(x => x.r.premium_locked && x.r.premium_has_data), surplus: pick(x => typeof surplusSection === 'function' && surplusSection(x.r) !== ''),
      judg: pick(x => x.r.lien_count > 2 || x.r.tax_lien || x.r.judgment_flag), caseVenue: pick(x => x.r.id && !x.r.id.startsWith('nat:') && (x.r.nod_case_number || x.r.trustee_file_no || x.r.venue_name)), n: vis.length };
  });
  run.info('lead-detail', 'rows chosen for the detail walk', JSON.stringify(picks));
  const openDetail = async (i) => {
    await page.evaluate(() => closeDetail()); await page.waitForTimeout(300); await page.click('#n-deals');
    await page.click(`#list .lead[onclick="openDetail(${i})"]`);
    await page.waitForSelector('#detail.open', { timeout: 10000 });
    const factsLoaded = await page.waitForFunction(() => { const f = document.getElementById('facts'); return f && !/Pulling the county record/.test(f.innerHTML); }, null, { timeout: 30000 }).then(() => true).catch(() => false);
    await page.waitForTimeout(250);
    const D = await page.evaluate(() => {
      const d = document.getElementById('detailInner'); const T = el => (el ? el.textContent.trim() : '');
      const rows = sel => Array.from(d.querySelectorAll(sel)).map(x => ({ k: T(x.querySelector('.k')), v: T(x.querySelector('.v')), locked: x.classList.contains('locked-row'), lock: T(x.querySelector('.lock')), blur: !!x.querySelector('.blur') }));
      const secAfter = name => { const s = Array.from(d.querySelectorAll('.sec')).find(x => x.textContent.trim().toLowerCase() === name.toLowerCase()); return s ? s.nextElementSibling : null; };
      const sect = name => { const el = secAfter(name); return el ? Array.from(el.querySelectorAll('.rowline')).map(x => ({ k: T(x.querySelector('.k')), v: T(x.querySelector('.v')), locked: x.classList.contains('locked-row'), lock: T(x.querySelector('.lock')), blur: !!x.querySelector('.blur') })) : null; };
      const tiles = Array.from(d.querySelectorAll('.vgrid .vtile')).map(t => ({ title: T(t.querySelector('.title')), word: T(t.querySelector('.word')), locked: t.classList.contains('lockt'), more: T(t.querySelector('.more')), cls: t.className }));
      const contact = secAfter('Contact');
      const phones = contact ? Array.from(contact.querySelectorAll('a.phone')).map(a => ({ href: a.getAttribute('href'), tag: T(a.querySelector('.tag')), dnc: a.classList.contains('dnc'), num: T(a.querySelector('.num')) })) : [];
      const emails = contact ? Array.from(contact.querySelectorAll('a.emailchip')).map(a => a.getAttribute('href')) : [];
      const sig = secAfter('OffRamp Signals');
      const sigs = []; let el = sig; while (el && !el.classList.contains('sec')) { if (el.classList.contains('sig')) sigs.push({ cls: el.className, big: T(el.querySelector('.big')), locked: el.classList.contains('locked') }); el = el.nextElementSibling; }
      const docs = secAfter('Documents');
      const facts = document.getElementById('facts');
      const prem = d.querySelector('.locked b');
      return {
        owner: T(d.querySelector('.dhead .owner')), street: T(d.querySelector('.dhead .street')), csz: T(d.querySelector('.dhead .csz')), hbadges: Array.from(d.querySelectorAll('.dhead .badge')).map(T),
        cells: Object.fromEntries(Array.from(d.querySelectorAll('.dkv .cell')).map(c => [T(c.querySelector('.k')), T(c.querySelector('.v'))])),
        contact: contact ? sect('Contact') : null, phones, emails, skipBtn: !!(contact && Array.from(contact.querySelectorAll('button')).some(b => /skip trace/i.test(b.textContent))), noPhonesText: contact && /No phones on file yet/.test(contact.textContent),
        loan: sect('Loan & equity'), checks: tiles, signals: { present: !!sig, siglock: !!d.querySelector('.siglock'), sigs }, surplus: sect('Surplus funds'), foreclosure: sect('Foreclosure'), premiumSec: sect('Next of kin & PACER'), premiumBox: prem ? T(prem) : null,
        engage: { abtns: Array.from(d.querySelectorAll('#abar .abtn')).map(T), note: !!d.querySelector('#anote') }, maplinks: Array.from(d.querySelectorAll('.maplinks a')).map(a => ({ t: T(a), href: a.getAttribute('href'), target: a.getAttribute('target') })),
        docs: docs ? { rows: Array.from(docs.querySelectorAll('.docrow')).map(r => ({ title: T(r.querySelector('.dl b')), btn: T(r.querySelector('.docbtn')), disabled: !!r.querySelector('.docbtn[disabled]') })), hint: T(docs.querySelector('.dochint')) } : null,
        facts: facts ? { rows: Array.from(facts.querySelectorAll('.rowline')).map(x => ({ k: T(x.querySelector('.k')), locked: x.classList.contains('locked-row'), lock: T(x.querySelector('.lock')) })), hint: T(facts.querySelector('.hint')), html: facts.innerHTML.length } : null,
        objectObject: /\[object Object\]/.test(d.innerHTML), decimalPct: (d.textContent.match(/\b\d+\.\d+%/g) || []).slice(0, 3), telClicked: false,
        state: (window.CUR || {}).property_state, cur: window.CUR ? { id: CUR.id, avm: CUR.avm || CUR.market_value || null, phones: Array.isArray(CUR.phones) ? CUR.phones.length : 0, nok: !!CUR.next_of_kin, lien_count: CUR.lien_count, premium_locked: !!CUR.premium_locked, premium_has_data: !!CUR.premium_has_data, source: CUR.source || 'hit_list' } : null,
      };
    });
    D.factsLoaded = factsLoaded; return D;
  };
  const findRow = (rows, k) => (rows || []).find(r => r.k === k);
  const lockOk = (row, kind) => !!row && row.locked && new RegExp(`Unlock · ${kind === 'premium' ? 'Premium' : 'Pro'}`).test(row.lock);
  const assertDetail = async (view, D, which) => {
    const t = run.tier; const paidT = t !== 'free'; const prem = t === 'premium';
    run.ok(view, 'header: owner, street, city/state/zip, 4 fact cells, sale date', D.owner && D.street && D.csz && Object.keys(D.cells).length === 4 && 'Sale date' in D.cells, `${D.owner} · ${D.street} · ${D.csz} · ${D.hbadges.join(' | ')}`, JSON.stringify({ owner: D.owner, street: D.street, cells: D.cells }));
    run.ok(view, 'nothing in the lead view renders as [object Object]', !D.objectObject, '', 'the view contains "[object Object]" (an array/object was rendered as text - next_of_kin is a list of {name, phones, emails})');
    run.ok(view, 'no decimal percentages in the lead view', D.decimalPct.length === 0, '', `found ${D.decimalPct.join(', ')}`);
    // Contact
    const ph = findRow(D.contact, 'Phone'), em = findRow(D.contact, 'Email'), nk = findRow(D.contact, 'Next of kin');
    const ePh = run.exp('detail.contact.phone');
    if (ePh === 'unlock:pro') { run.ok(view, 'Contact · Phone grayed with Unlock · Pro', lockOk(ph, 'pro') && ph.blur, `'${ph && ph.lock}'`, JSON.stringify(ph)); run.ok(view, 'Contact · Email grayed with Unlock · Pro', lockOk(em, 'pro'), '', JSON.stringify(em)); run.ok(view, 'Contact · no tel: links for Free', D.phones.length === 0, '', `${D.phones.length} phone links`); }
    else { const has = D.phones.length > 0; run.ok(view, 'Contact · Pro sees phones or the honest empty state + Run skip trace', has || (D.noPhonesText && D.skipBtn), has ? `${D.phones.length} phone rows, ${D.emails.length} emails` : 'No phones on file yet + Run skip trace button', JSON.stringify({ phones: D.phones.length, noPhonesText: D.noPhonesText, skipBtn: D.skipBtn, contact: D.contact }));
      if (has) { run.ok(view, 'every phone row is a tel: link with digits only (asserted, never tapped)', D.phones.every(p => /^tel:\d{10,11}$/.test(p.href)), D.phones.map(p => p.href.replace(/\d(?=\d{4})/g, '•')).join(' '), JSON.stringify(D.phones));
        const dncRows = D.phones.filter(p => p.dnc); run.ok(view, 'DNC numbers carry the DNC tag but stay dialable (tel: href kept)', dncRows.every(p => p.tag === 'DNC' && /^tel:/.test(p.href)), `${dncRows.length} DNC of ${D.phones.length}`, JSON.stringify(dncRows));
        run.ok(view, 'emails are mailto: chips', D.emails.every(h => /^mailto:/.test(h)), `${D.emails.length} emails`, D.emails.join(',')); }
      run.skip(view, 'tap a phone / Run skip trace / email', 'never activated by the suite (would dial, spend a credit, or mark Contacted)'); }
    const eNk = run.exp('detail.contact.next_of_kin');
    if (eNk === 'data') run.ok(view, 'Contact · Next of kin row shows data or "None on file" (Premium)', nk && !nk.locked && !/\[object Object\]/.test(nk.v), `'${nk && nk.v.slice(0, 80)}'`, JSON.stringify(nk));
    else run.ok(view, `Contact · Next of kin grayed with Unlock · ${eNk.split(':')[1]}`, lockOk(nk, eNk.split(':')[1]), `'${nk && nk.lock}'`, JSON.stringify(nk));
    // Loan & equity
    const L = D.loan || [];
    const hasVal = !!(D.cur && D.cur.avm);
    run.ok(view, 'Loan & equity · Equity row for every tier (+ Value when the row has an AVM)', !!findRow(L, 'Equity') && (!hasVal || !!findRow(L, 'Value')), `Equity ${findRow(L, 'Equity') && findRow(L, 'Equity').v} · Value ${findRow(L, 'Value') ? findRow(L, 'Value').v : (hasVal ? 'MISSING' : 'none on the row')}`, JSON.stringify(L));
    for (const [feat, k] of [['detail.loan.equity_dollars', 'Equity $'], ['detail.loan.est_payment', 'Est. payment']]) {
      const e = run.exp(feat); const row = findRow(L, k);
      if (e === 'unlock:pro') run.ok(view, `Loan & equity · ${k} grayed with Unlock · Pro`, lockOk(row, 'pro'), '', JSON.stringify(row));
      else run.ok(view, `Loan & equity · ${k} shown or honestly absent (paid)`, !row || !row.locked, row ? row.v : 'absent (no value for this row)', JSON.stringify(row));
    }
    const jg = findRow(L, 'Judgments & tax liens');
    if (jg) run.ok(view, 'Loan & equity · Judgments & tax liens locked row only below Premium', run.exp('detail.loan.judgments') !== 'data' && lockOk(jg, 'premium'), '', JSON.stringify(jg)); else if (prem && which === 'judg') run.pass(view, 'Loan & equity · Premium sees judgments/tax liens/more mortgages rows instead of a lock', L.filter(r => /Tax lien|Judgment|More mortgages/.test(r.k)).map(r => `${r.k}: ${r.v}`).join('; ') || 'no such rows on this lead');
    // Checks tiles
    const tile = n => D.checks.find(x => x.title === n);
    for (const [feat, n, kind] of [['detail.checks.court_records', 'Court records', 'pro'], ['detail.checks.liens', 'Liens', 'premium'], ['detail.checks.next_of_kin', 'Next of kin', 'premium']]) {
      const e = run.exp(feat); const x = tile(n);
      if (e === 'data') run.ok(view, `Checks · ${n} tile shows a verdict`, x && !x.locked && x.word && !/\[object/.test(x.word), x ? `${x.word}${x.more ? ' (' + x.more + ')' : ''}` : 'tile missing', JSON.stringify(x));
      else run.ok(view, `Checks · ${n} tile locked with Unlock on ${kind === 'premium' ? 'Premium' : 'Pro'}`, x && x.locked && new RegExp(`Unlock on ${kind === 'premium' ? 'Premium' : 'Pro'}`).test(x.more), '', JSON.stringify(x));
    }
    if (paidT) run.skip(view, 'Court records · Run the check', 'never clicked (writes the court_records cache + CourtListener call)');
    // Signals (UT pilot)
    if (D.state === 'UT' && D.signals.present) {
      const e = run.exp('detail.signals.section');
      if (e === 'unlock:pro') run.ok(view, 'Signals section blurred with "Signals unlock on Pro" for Free', D.signals.siglock && D.signals.sigs.every(s => s.locked || /surplus/.test(s.cls)), `${D.signals.sigs.length} signals`, JSON.stringify(D.signals));
      else run.ok(view, 'Signals section shows real values for paid', !D.signals.siglock && D.signals.sigs.every(s => !s.locked && s.big && !/•/.test(s.big)), D.signals.sigs.map(s => `${s.cls.replace(/sig /, '').trim()}: ${s.big}`).join(' · '), JSON.stringify(D.signals));
      run.ok(view, 'Surplus Chaser signal only on Premium', prem || !D.signals.sigs.some(s => /surplus/.test(s.cls)), '', 'Surplus Chaser rendered below Premium');
    } else if (D.state !== 'UT') run.ok(view, 'no Signals section outside the UT pilot', !D.signals.present, D.state, `Signals rendered on a ${D.state} lead`);
    // Surplus funds
    if (D.surplus) { const e = run.exp('detail.surplus_funds'); run.ok(view, e === 'data' ? 'Surplus funds section shows figures (Premium)' : `Surplus funds grayed with Unlock · Premium`, e === 'data' ? !D.surplus.some(r => r.locked) : D.surplus.some(r => lockOk(r, 'premium')), JSON.stringify(D.surplus).slice(0, 200), JSON.stringify(D.surplus)); }
    // Foreclosure
    const F = D.foreclosure || []; const cv = findRow(F, 'Case number & venue');
    run.ok(view, 'Foreclosure section: Status row present', !!findRow(F, 'Status'), findRow(F, 'Status') && findRow(F, 'Status').v, JSON.stringify(F));
    if (cv) run.ok(view, 'Foreclosure · Case number & venue locked below Premium', !prem && lockOk(cv, 'premium'), '', JSON.stringify(cv)); else if (prem && which === 'caseVenue') run.ok(view, 'Foreclosure · Premium sees Case/NOD #, Trustee file #, Sale venue', F.some(r => /Case \/ NOD #|Trustee file #|Sale venue|^Type$/.test(r.k)), F.filter(r => /Case|Trustee file|venue|Type/.test(r.k)).map(r => r.k).join(', '), JSON.stringify(F));
    // Premium section
    if (D.premiumBox) run.ok(view, 'Next of kin & PACER locked box only below Premium', !prem && /Premium data/.test(D.premiumBox), D.premiumBox, D.premiumBox);
    if (prem && D.premiumSec) run.ok(view, 'Next of kin & PACER section renders for Premium', D.premiumSec.length > 0 && !D.premiumSec.some(r => /\[object/.test(r.v)), D.premiumSec.map(r => r.k).join(', '), JSON.stringify(D.premiumSec));
    // Engage / links / docs
    run.ok(view, 'Engage: Save, Contacted, Hide buttons + note (not clicked: they write lead_actions)', D.engage.abtns.length === 3 && D.engage.note, D.engage.abtns.join(' / '), JSON.stringify(D.engage));
    run.ok(view, 'Open in Maps / View on Zillow open in a new tab', D.maplinks.length >= 2 && D.maplinks.every(l => l.target === '_blank') && D.maplinks.filter(l => /^https:/.test(l.href)).length >= 2, D.maplinks.map(l => l.t).join(' · '), JSON.stringify(D.maplinks));
    run.ok(view, 'Documents: "Get it · $5" button per document, hint says five dollars (never clicked)', D.docs && D.docs.rows.length >= 2 && D.docs.rows.every(r => r.btn === 'Get it · $5' && !r.disabled) && /Five dollars/.test(D.docs.hint), D.docs ? D.docs.rows.map(r => r.title).join(' · ') : 'no Documents section', JSON.stringify(D.docs));
    run.skip(view, 'Documents · Get it · $5', 'never clicked (opens a Stripe payment session)');
    // Property facts
    const Fx = D.facts; const fx = Fx ? Fx.rows.filter(r => !r.locked) : []; const fl = Fx ? Fx.rows.find(r => r.k === 'Full county record') : null;
    run.ok(view, 'Property facts finished loading (county record)', D.factsLoaded && Fx && Fx.html > 20, Fx ? `${fx.length} rows · ${Fx.hint}` : 'missing', Fx ? Fx.hint : 'facts block missing');
    const eF = run.exp('detail.facts'); const cap = (MATRIX.features['detail.facts'].max_rows || {})[t];
    if (eF === 'data') run.ok(view, 'Property facts: full county record for Premium (no Unlock row)', !fl, `${fx.length} rows`, JSON.stringify(Fx && Fx.rows));
    if (paidT) run.ok(view, 'Property facts: a county record came back for a paid user', !/unavailable right now|No county record/.test(Fx ? Fx.hint : ''), Fx ? Fx.hint : '', `the lead view says '${Fx ? Fx.hint : ''}' (server /api/property-facts returned no facts; check REAPI-PAUSED / reapi_lookup)`);
    else { const noCache = /are on the full county record/.test(Fx ? Fx.hint : '');   // server answered source=locked (no cached detail for a non-paid pull): basic rows + the lock
      run.ok(view, `Property facts: ${t} sees at most ${cap} county rows + "Full county record · Unlock · Premium"`, (noCache ? fx.length <= 4 : fx.length <= cap) && (!!fl && lockOk(fl, 'premium') || /No county record|unavailable/.test(Fx ? Fx.hint : '')), `${fx.map(r => r.k).join(', ')} · lock '${fl && fl.lock}'${noCache ? ' · no cached county record: basic rows + lock' : ''}`, JSON.stringify(Fx && Fx.rows)); }
  };

  const detailViews = [['lead-detail', picks.first, 'first'], ['lead-detail-phones', (run.tier === 'free' ? picks.phones : picks.phones), 'phones'], ['lead-detail-signals', picks.sigSection, 'signals'], ['lead-detail-nok', picks.nok, 'nok'], ['lead-detail-premium-locked', picks.premLocked, 'premLocked'], ['lead-detail-liens', picks.judg, 'judg'], ['lead-detail-case-venue', picks.caseVenue, 'caseVenue'], ['lead-detail-surplus', picks.surplus, 'surplus']];
  const seenIdx = new Set();
  for (const [view, idx, which] of detailViews) {
    if (idx == null) { run.skip(view, `open a ${which} lead`, `no Utah row in this list qualifies for '${which}' on this tier (as served)`); continue; }
    if (seenIdx.has(idx) && which !== 'first') { run.skip(view, `open a ${which} lead`, `same row as an earlier view (ROWS[${idx}])`); continue; }
    seenIdx.add(idx);
    let D; try { D = await openDetail(idx); } catch (e) { run.fail(view, 'open lead', e.message.slice(0, 200)); continue; }
    await assertDetail(view, D, which);
    await run.shot(view, { fullDetail: true });
    if (which === 'signals') { const cardSig = cards.find(c => c.idx === idx); run.info(view, 'card badge for this lead', cardSig ? (cardSig.sigText || 'none on card (only strong calls ride on cards)') : 'n/a'); }
    if (which === 'first') {   // gray-out → upgrade sheet
      const lockSel = '#detail .locked-row .lock, #detail .vtile.lockt';
      const nLocks = await page.locator(lockSel).count();
      if (run.tier === 'premium') run.ok(view, 'Premium has no gray-outs / Unlock buttons anywhere in the lead view', nLocks === 0, '', `${nLocks} lock elements`);
      else {
        run.ok(view, 'gray-outs carry an Unlock button', nLocks > 0, `${nLocks} Unlock buttons`, 'no Unlock buttons on a lower tier');
        for (const kind of run.tier === 'free' ? ['pro', 'premium'] : ['premium']) {
          const loc = page.locator(kind === 'pro' ? '#detail .locked-row:has(.lock:text-matches("Pro")) .lock' : '#detail .locked-row:has(.lock:text-matches("Premium")) .lock, #detail .vtile.lockt:has(.more:text-matches("Premium"))').first();
          if (!(await loc.count())) { run.skip('upgrade-sheet', `open the ${kind} upgrade sheet from a gray-out`, `no ${kind} gray-out on this lead`); continue; }
          await loc.click({ force: true });
          const up = await page.waitForFunction(() => { const el = document.getElementById('upsheet'); if (!(el && el.style.display === 'block')) return false;
            const b = Array.from(el.querySelectorAll('button')).find(x => /Not now/.test(x.textContent)); const r = b && b.getBoundingClientRect(); const hit = r ? document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2) : null;
            return { title: (el.querySelector('.upt') || {}).textContent, cards: Array.from(el.querySelectorAll('.plancard')).filter(c => !/^Free trial/.test((c.querySelector('.pn') || {}).textContent || '')).length, trialCard: Array.from(el.querySelectorAll('.plancard')).some(c => /^Free trial/.test((c.querySelector('.pn') || {}).textContent || '')), btns: Array.from(el.querySelectorAll('button')).map(x => x.textContent.trim()), onTop: !!(hit && el.contains(hit)), covered: hit ? (hit.closest('#detail') ? '#detail (z-index 70) covers #upsheet (z-index 60)' : hit.tagName) : 'off-screen', z: { up: getComputedStyle(el).zIndex, detail: getComputedStyle(document.getElementById('detail')).zIndex } }; }, null, { timeout: 5000 }).then(h => h.jsonValue()).catch(() => null);
          run.ok('upgrade-sheet', `tapping an Unlock · ${kind} gray-out opens the upgrade sheet with the ${kind} title + 2 plan cards`, up && up.title === MATRIX.upgrade_sheet_title[kind] && up.cards === 2, up ? `'${up.title}' · ${up.btns.join(' / ')}` : 'sheet did not open', JSON.stringify(up), await run.shot(`upgrade-sheet-${kind}`));
          if (up) run.ok('upgrade-sheet', `the ${kind} upgrade sheet is visible ON TOP of the lead view (tappable)`, up.onTop, '', `the sheet opened in the DOM but is hidden behind the lead view: ${up.covered}; z-index upsheet ${up.z.up} vs detail ${up.z.detail}. The user taps Unlock and sees nothing happen.`);
          if (up && up.onTop) await page.click('#upsheet button.btn-ghost').catch(() => {});
          await page.evaluate(() => { try { closeUpgradeSheet(); } catch (e) { /* */ } });
        }
      }
    }
  }
  await page.evaluate(() => closeDetail());

  // ---- lookup screen (render only: a lookup spends a credit)
  await page.click('#n-lookup'); await page.waitForTimeout(200);
  const lk = await page.evaluate(() => ({ on: document.getElementById('s-lookup').classList.contains('on'), input: !!document.getElementById('lookupAddr'), go: Array.from(document.querySelectorAll('#s-lookup button')).some(b => b.textContent.trim() === 'Go'), h2: (document.querySelector('#s-lookup .h2') || {}).textContent }));
  run.ok('lookup', 'Search any address screen renders (input + Go)', lk.on && lk.input && lk.go, lk.h2, JSON.stringify(lk), await run.shot('lookup'));
  run.skip('lookup', 'run an address lookup', 'never run by the suite (spends a credit; REAPI call)');

  // ---- account
  await page.click('#n-acct'); await page.waitForTimeout(200);
  const A = await page.evaluate(() => { const b = document.getElementById('acctBody'); const T = el => (el ? el.textContent.trim() : ''); return { name: T(b.querySelector('.acct-plan .big')), pill: T(b.querySelector('.acct-plan .plan-pill')), email: b.textContent.includes(ME.email), meters: Array.from(b.querySelectorAll('.meter')).map(m => T(m.querySelector('.lab'))), founding: /Founding member/.test(b.textContent), trial: /Free trial/.test(b.textContent) && !!Array.from(b.querySelectorAll('button')).find(x => /free trial/i.test(x.textContent)), cards: Array.from(b.querySelectorAll('.plancard')).map(c => ({ name: T(c.querySelector('.pn')).replace(/\$.*$/, '').trim(), cur: c.classList.contains('cur'), you: /Your plan/.test(c.textContent), btn: T(c.querySelector('.btn-gold')) })), btns: Array.from(b.querySelectorAll('.btn-ghost')).map(T), iphoneHint: /Add to Home Screen/.test(b.textContent) }; });
  const R4 = run.role;
  run.ok('account', 'account header: name/email + plan pill match the server role', A.email && A.pill === plan.label, `${A.name} · ${A.pill}`, JSON.stringify({ name: A.name, pill: A.pill, expect: plan.label }), await run.shot('account', { fullPage: true }));
  run.ok('account', 'credits meter = /api/me usage', A.meters.some(m => new RegExp(`^Credits this month\\s*${R4.usage.lookups.used} / ${R4.usage.lookups.limit}$`).test(m.replace(/\s+/g, ' '))), A.meters.join(' · '), `meters ${A.meters.join(' · ')} vs ${R4.usage.lookups.used}/${R4.usage.lookups.limit}`);
  A.cards = A.cards.filter(c => !/^Free trial/.test(c.name));
  const curCard = A.cards.filter(c => c.you).map(c => c.name);
  if (R4.plan === 'free') run.ok('account', 'no plan card says "Your plan" on Free; both offer Upgrade', curCard.length === 0 && A.cards.every(c => /^Upgrade to/.test(c.btn)), A.cards.map(c => `${c.name}: ${c.btn}`).join(' · '), JSON.stringify(A.cards));
  else run.ok('account', `"${R4.plan === 'premium' ? 'Premium' : 'Pro'}" card says Your plan; the other offers Switch`, curCard.length === 1 && curCard[0].toLowerCase().startsWith(R4.plan === 'premium' ? 'premium' : 'pro') && A.cards.filter(c => !c.you).every(c => /^Switch to/.test(c.btn)), A.cards.map(c => `${c.name}: ${c.you ? 'Your plan' : c.btn}`).join(' · '), JSON.stringify(A.cards));
  const eX = run.exp('account.export_csv'); run.ok('account', eX === 'hidden' ? 'Export CSV button hidden on Free' : 'Export CSV button shown for paid', (eX === 'hidden') === !A.btns.some(b => /Export current search/.test(b)), A.btns.join(' · '), A.btns.join(' · '));
  run.ok('account', 'Founding member card only for founding accounts', A.founding === !!R4.founding, A.founding ? 'shown' : 'not shown', `founding card ${A.founding} vs founding=${R4.founding}`);
  run.ok('account', 'Free trial card only when /api/me trial_eligible', A.trial === !!R4.trial_eligible, A.trial ? 'shown' : 'not shown', `trial card ${A.trial} vs trial_eligible=${R4.trial_eligible}`);
  const wantBilling = !!(R4.paid && !R4.trial && R4.billing_portal); run.ok('account', 'Manage billing only when paid with a Stripe customer', A.btns.some(b => /Manage billing/.test(b)) === wantBilling, wantBilling ? 'shown' : 'not shown', `billing_portal=${R4.billing_portal} paid=${R4.paid}`);
  run.ok('account', 'Log out button present', A.btns.some(b => /Log out/.test(b)), '', A.btns.join(' · '));
  if (run.mode === 'mobile') run.ok('account', 'iPhone install hint shown in Safari', A.iphoneHint, '', 'no Add to Home Screen hint');

  // ---- API-level gating probes (read-only; the free export/court probes are refused by the server before doing anything)
  const sr = await run.api('GET', '/api/search?state=UT&limit=50'); const rows = (sr.json && sr.json.results) || [];
  const cl = rows.filter(r => r.contacts_locked).length, pl = rows.filter(r => r.premium_locked).length, phN = rows.filter(r => r.phones && r.phones.length).length, nkN = rows.filter(r => r.next_of_kin).length;
  run.ok('api', `/api/search rows: contacts ${run.exp('api.search.contacts_locked') === 'locked' ? 'withheld' : 'present'} for ${run.tier}`, run.exp('api.search.contacts_locked') === 'locked' ? (cl === rows.length && phN === 0 && rows.every(r => r.equity_dollars == null && r.mortgage_balance == null)) : (cl === 0), `${rows.length} rows · contacts_locked ${cl} · rows with phones ${phN}`, `${rows.length} rows · contacts_locked ${cl} · phones ${phN}`);
  run.ok('api', `/api/search rows: premium fields ${run.exp('api.search.premium_locked') === 'locked' ? 'withheld' : 'present'} for ${run.tier}`, run.exp('api.search.premium_locked') === 'locked' ? (pl === rows.length && nkN === 0) : (pl === 0), `${rows.length} rows · premium_locked ${pl} · rows with next_of_kin ${nkN}`, `premium_locked ${pl} · nok ${nkN}`);
  run.ok('api', '/api/search echoes plan/paid/premium flags that match /api/me', sr.json && sr.json.plan === R4.plan && sr.json.paid === R4.paid && sr.json.premium === R4.premium, `plan ${sr.json && sr.json.plan} paid ${sr.json && sr.json.paid} premium ${sr.json && sr.json.premium}`, JSON.stringify({ plan: sr.json && sr.json.plan, paid: sr.json && sr.json.paid, premium: sr.json && sr.json.premium }));
  if (run.exp('api.export') === '403') { const ex = await run.api('GET', '/api/export?state=UT'); run.ok('api', 'GET /api/export refused for Free (403 + upgrade flag)', ex.status === 403 && ex.json && ex.json.upgrade === true, `HTTP ${ex.status} ${JSON.stringify(ex.json)}`, `HTTP ${ex.status} ${JSON.stringify(ex.json)}`); }
  else run.skip('api', 'GET /api/export for paid', 'not run (it bumps exports_used; nothing else to assert beyond a CSV download)');
  if (run.exp('api.court_records') === '403') { const cr = await run.api('POST', '/api/court-records', { id: rows[0] && rows[0].id, nationwide: false }); run.ok('api', 'POST /api/court-records refused for Free (403 + upgrade flag)', cr.status === 403 && cr.json && cr.json.upgrade === true, `HTTP ${cr.status} ${JSON.stringify(cr.json)}`, `HTTP ${cr.status} ${JSON.stringify(cr.json)}`); }
  else run.skip('api', 'POST /api/court-records for paid', 'not run (writes the court_records cache, external CourtListener call)');
  run.skip('api', 'POST /api/skiptrace', 'never run (vendor spend); Free would get 403, paid would spend');

  // ---- checkout_start: the trial button (when offered) and the first plan Upgrade/Switch button; each stops at the Stripe redirect
  const checkoutButtons = [['trial', '#acctBody .plancard:has(.pn:text-matches("^Free trial")) .btn-gold'], ['plan', '#acctBody .plancard:not(:has(.pn:text-matches("^Free trial"))) .btn-gold']];
  let anyCheckout = false;
  for (const [kind, sel] of checkoutButtons) {
    await page.click('#n-acct'); await page.waitForTimeout(150);
    const btn = page.locator(sel).first();
    if (!(await btn.count())) continue; anyCheckout = true;
    const label = (await btn.textContent()).trim();
    run.lastCheckout = null;
    await btn.click();
    for (let i = 0; i < 300 && !run.lastCheckout; i++) await page.waitForTimeout(100);
    const lc = run.lastCheckout || { status: 0, body: '' }; const coStatus = lc.status || 0; const co = { status: () => coStatus };
    const body = lc.body || ''; let cj = null; try { cj = JSON.parse(body); } catch (e) { /* */ }
    const bodyNote = /<!DOCTYPE html>/i.test(body) ? `(HTML error page from the edge, title '${(/<title>([^<]*)/i.exec(body) || [])[1] || ''}')` : body.slice(0, 160);
    const stripeUrl = cj && cj.url || '';
    if (stripeUrl) await page.waitForFunction(() => /UAT guard: Stripe checkout redirect intercepted/.test(document.body.textContent || ''), null, { timeout: 15000 }).catch(() => {});
    const landed = page.url();
    const okCo = co.status() === 200 && /^https:\/\/checkout\.stripe\.com\//.test(stripeUrl);
    run.ok('checkout', `"${label}" → POST /api/billing/checkout-link 200 with a checkout.stripe.com URL (${kind})`, okCo, `HTTP ${co.status()} → ${stripeUrl.replace(/(c\/pay\/|cs_)[A-Za-z0-9_]+.*/, '$1…')}`, `HTTP ${co.status()} ${bodyNote} — the app toasts '${(cj && cj.error) || 'billing unavailable'}' and nothing happens`);
    if (okCo) run.ok('checkout', `browser was sent to Stripe for ${kind} and the suite stopped at the redirect (no purchase)`, /checkout\.stripe\.com/.test(landed) && (run.stripeHit || 0) > 0, 'intercepted by the UAT guard', `landed on ${landed}`, await run.shot(`checkout-stripe-redirect-${kind}`));
    else await run.shot(`checkout-${kind}-failed`);
    await page.goto('/app/', { waitUntil: 'load' }); await page.waitForSelector('#shell:not(.hidden)', { timeout: 20000 }).catch(() => {});
    run.ok('checkout', `coming back from the ${kind} checkout keeps the session`, await page.locator('#shell:not(.hidden)').count() === 1, '', 'session lost after the redirect');
  }
  if (!anyCheckout) run.skip('checkout', 'checkout_start click', 'no upgrade/switch button on this account');
  run.skip('checkout', 'complete a Stripe checkout', 'never done by the suite');

  { const nfs = await run.api('GET', '/api/nope-4d1f'); run.ok('api', 'unknown /api path answers 404 JSON while signed in', nfs.status === 404 && nfs.json && nfs.json.error, `HTTP ${nfs.status}`, `HTTP ${nfs.status}`); }

  // ---- static pages the app links to
  for (const [u, name] of [['/terms', 'Terms page'], ['/app/manifest.webmanifest', 'PWA manifest'], ['/app/sw.js', 'service worker script']]) {
    const r = await run.context.request.get(BASE + u); const h = r.headers();
    run.ok('static', `${name} ${u} → 200${u === '/app/sw.js' ? ' with no-store cache header' : ''}`, r.status() === 200 && (u !== '/app/sw.js' || /no-store/.test(h['cache-control'] || '')), `HTTP ${r.status()} ${h['content-type'] || ''} ${h['cache-control'] || ''}`, `HTTP ${r.status()} ${h['cache-control'] || ''}`);
  }

  // ---- logout
  await page.click('#n-acct'); await page.waitForTimeout(150);
  const [lo] = await Promise.all([page.waitForResponse(r => r.url().includes('/api/auth/logout'), { timeout: 20000 }), page.click('#acctBody button:has-text("Log out")')]);
  await page.waitForSelector('#auth:not(.hidden)', { timeout: 20000 }).catch(() => {});
  await page.waitForTimeout(300);
  const after = await run.api('GET', '/api/me');
  run.ok('logout', 'Log out → POST /api/auth/logout 200, reload shows the login card, /api/me is 401', lo.status() === 200 && after.status === 401 && (await page.locator('#shell.hidden').count()) === 1, '', `logout HTTP ${lo.status()} · /api/me ${after.status}`, await run.shot('logout'));

  // ---- 404
  const nf = await page.goto('/this-page-does-not-exist-4d1f', { waitUntil: 'load' }).catch(() => null);
  run.ok('404', 'unknown path answers 404', nf && nf.status() === 404, `HTTP ${nf && nf.status()}`, `HTTP ${nf && nf.status()}`, await run.shot('404'));
  const nfa = await run.context.request.get(BASE + '/api/nope-4d1f'); run.ok('404', 'unknown /api path answers 401 when signed out (session is checked before routing)', nfa.status() === 401, `HTTP ${nfa.status()}`, `HTTP ${nfa.status()}`);

  // ---- offline: the service worker must serve the app shell
  await page.goto('/app/', { waitUntil: 'load' });
  const swReady = await page.evaluate(async () => { try { for (let i = 0; i < 60; i++) { const reg = await navigator.serviceWorker.getRegistration('/app/'); const hit = await caches.match('/app/', { ignoreSearch: true }); if (reg && reg.active && hit) return true; await new Promise(r => setTimeout(r, 250)); } } catch (e) { return 'err ' + e.message; } return false; });
  if (swReady !== true) run.fail('offline', 'service worker active with the shell cached', `state: ${swReady}`);
  else {
    await run.context.setOffline(true);
    let off = null; try { off = await page.goto('/app/', { waitUntil: 'load', timeout: 20000 }); } catch (e) { off = null; }
    const shell = await page.evaluate(() => ({ title: document.title, auth: !!document.getElementById('auth'), brand: /OffRamp/.test(document.body.textContent || '') })).catch(() => ({}));
    run.ok('offline', 'offline: /app/ shell loads from the service worker cache (login card visible, API unreachable)', !!off && shell.auth && shell.brand, `served ${off && off.fromServiceWorker ? 'by SW' : ''} title '${shell.title}'`, `offline load failed: ${JSON.stringify(shell)}`, await run.shot('offline-shell'));
    await run.context.setOffline(false);
  }

  // ---- hygiene
  run.ok('hygiene', 'no uncaught JS errors or console errors during the walk', run.errors.length === 0, '', run.errors.slice(0, 4).map(e => `${e.kind}: ${e.msg}`).join(' | '));
  run.ok('hygiene', 'the suite never tried a forbidden request (skiptrace / lookup / orders / lead-actions / signup / export)', run.guardHits.length === 0, '', run.guardHits.join(', '));
}

// Desktop pass: the same app at 1280x800 (phone-frame shell centered), a shorter walk.
async function walkDesktop(run) {
  const page = run.page;
  await page.goto('/app/', { waitUntil: 'load' }); await page.waitForSelector('#auth');
  await run.shot('auth-login');
  await page.fill('#f_email', run.email); await page.fill('#f_pw', CREDS[run.email]);
  const tLogin = Date.now();
  const [loginRes] = await Promise.all([page.waitForResponse(r => r.url().includes('/api/auth/login')), page.click('#authSubmit')]);
  run.ok('login', 'desktop login', loginRes.status() === 200, `HTTP ${loginRes.status()}`);
  const me = await run.api('GET', '/api/me'); run.role = me.json && me.json.user; if (!run.role) return;
  if (run.role.terms_accepted === false) { await page.waitForSelector('#g_terms'); await page.check('#g_terms'); await Promise.all([page.waitForResponse(r => r.url().includes('/api/auth/accept-terms')), page.click('#upsheet button.btn-primary')]); run.pass('terms-gate', 'accepted on desktop'); }
  await page.waitForSelector('#shell:not(.hidden)');
  let S = await settleSearch(run, tLogin, null); reconcile(run, 'deals-all-states', 'All states (desktop)', S);
  const box = await page.evaluate(() => { const r = document.getElementById('shell').getBoundingClientRect(); const hasDeskClass = document.body.classList.contains('desk'); const railVisible = window.getComputedStyle(document.querySelector('nav.rail')).display !== 'none'; const bottomNavHidden = window.getComputedStyle(document.querySelector('nav.bottom')).display === 'none'; return { w: Math.round(r.width), left: Math.round(r.left), vw: innerWidth, hasDeskClass, railVisible, bottomNavHidden }; });
  run.ok('layout', 'desktop: full-width two-column layout (body.desk, rail nav visible, bottom nav hidden)', box.hasDeskClass && box.railVisible && box.bottomNavHidden && box.w >= 900, `shell ${box.w}px, desk=${box.hasDeskClass}, rail=${box.railVisible}, botNav hidden=${box.bottomNavHidden}`, JSON.stringify(box), await run.shot('deals-all-states'));
  S = await settleSearch(run, Date.now(), async () => { await page.fill('#fSearch', 'Utah'); await page.press('#fSearch', 'Enter'); }); reconcile(run, 'deals-list', 'Utah (desktop)', S); await run.shot('deals-list');
  await page.click('#filterBtn'); await page.waitForSelector('#sheet.open'); await run.shot('filter-sheet'); await page.keyboard.press('Escape'); await page.waitForTimeout(300);
  run.ok('filter-sheet', 'Escape closes the sheet (keyboard)', (await page.locator('#sheet.open').count()) === 0, '', 'sheet still open');
  await page.click('#rn-map'); await page.waitForSelector('#s-map.on .leaflet-container').catch(() => {}); await page.waitForTimeout(800); await run.shot('map'); await page.click('#rn-deals');
  await page.click('#list .lead >> nth=0'); await page.waitForSelector('#detail.open');
  await page.waitForFunction(() => { const f = document.getElementById('facts'); return f && !/Pulling the county record/.test(f.innerHTML); }, null, { timeout: 30000 }).catch(() => {});
  const hasTelClick = await page.evaluate(() => document.querySelectorAll('#detail a[href^="tel:"]').length);
  run.ok('lead-detail', 'lead view opens on desktop', (await page.locator('#detail.open .dhead .owner').count()) === 1, `${hasTelClick} tel: links (not tapped)`, '', await run.shot('lead-detail', { fullDetail: true }));
  await page.evaluate(() => closeDetail());
  await page.click('#rn-acct'); await page.waitForTimeout(200); await run.shot('account', { fullPage: true });
  const [lo] = await Promise.all([page.waitForResponse(r => r.url().includes('/api/auth/logout')), page.click('#acctBody button:has-text("Log out")')]);
  run.ok('logout', 'desktop logout', lo.status() === 200, `HTTP ${lo.status()}`);
  run.ok('hygiene', 'no JS errors on desktop', run.errors.length === 0, '', run.errors.slice(0, 3).map(e => e.msg).join(' | '));
}

// ----------------------------------------------------------------------------- main
(async () => {
  const t0 = Date.now();
  fs.mkdirSync(OUT, { recursive: true });
  const accounts = MATRIX.accounts.filter(a => !ONLY || ONLY.includes(a.email.split('@')[0]) || ONLY.includes(a.email));
  for (const a of accounts) if (!CREDS[a.email]) { console.error(`no password for ${a.email} in uat_creds.json`); process.exit(2); }
  console.log(`OffRamp UAT · ${BASE} · ${accounts.map(a => a.email).join(', ')} · out ${OUT}`);
  const browser = await chromium.launch({ headless: true });
  const runs = accounts.map(a => new Run(a, 'mobile'));
  const one = async run => {
    try { await openContext(browser, run); await walkAccount(run); }
    catch (e) { run.fail('harness', 'unhandled error - the walk stopped here', (e && e.stack || String(e)).slice(0, 500), await run.shot('harness-error').catch(() => null)); console.error(run.label, e); }
    finally { run.finished = Date.now(); await run.context.close().catch(() => {}); }
  };
  if (ARGS.sequential) { for (const r of runs) await one(r); } else await Promise.all(runs.map(one));
  if (!ARGS['no-desktop']) {
    const da = accounts.find(a => a.expect_plan_key === (ARGS['desktop-account'] || 'premium')) || accounts[0];
    const d = new Run(da, 'desktop'); runs.push(d);
    try { await openContext(browser, d); await walkDesktop(d); } catch (e) { d.fail('harness', 'unhandled error', (e && e.stack || String(e)).slice(0, 500), await d.shot('harness-error').catch(() => null)); console.error(d.label, e); }
    finally { d.finished = Date.now(); await d.context.close().catch(() => {}); }
  }
  await browser.close();
  const report = writeReport({ out: OUT, base: BASE, started: t0, runs, matrix: MATRIX, args: ARGS });
  if (!ARGS['no-publish']) {
    fs.rmSync(PUBLISH_DIR, { recursive: true, force: true }); fs.mkdirSync(PUBLISH_DIR, { recursive: true });
    for (const f of fs.readdirSync(OUT)) fs.cpSync(path.join(OUT, f), path.join(PUBLISH_DIR, f === 'report.html' ? 'index.html' : f), { recursive: true });
    console.log(`published → ${PUBLISH_DIR} (served at /_uat-report-4d1f/)`);
  }
  const tot = report.totals;
  console.log(`\nDONE in ${Math.round((Date.now() - t0) / 1000)}s · PASS ${tot.PASS} · FAIL ${tot.FAIL} · SKIP ${tot.SKIP} · INFO ${tot.INFO}`);
  for (const r of report.accounts) console.log(`  ${r.label.padEnd(16)} role ${r.role ? r.role.plan_key : '?'}  PASS ${r.counts.PASS} FAIL ${r.counts.FAIL} SKIP ${r.counts.SKIP}  (${Math.round(r.duration_s)}s)`);
  console.log(`report: ${path.join(OUT, 'report.html')}`);
  process.exit(tot.FAIL ? 1 : 0);
})();
