# Current State

*Last updated: 2026-10-04. Update this document whenever project state materially changes.*

---

## Current Release State

**Pre-launch.** 30-day trial window is open (`OFFRAMP_TRIAL_LIVE=1`). Go-live date TBD (tonight vs Sunday 2026-10-05 11 AM MT — pending Ted decision). UAT passing. Stripe live mode active since 2026-09-27. No public subscribers yet.

Service worker version: **v46**.

---

## What Is Working

**VERIFIED from UAT (713/715 passing as of 2026-10-03):**

- Deal feed: active foreclosure auction listings load for all states with data
- State chip navigation and deal count chips
- Search (owner name, street, city, zip)
- Equity display: AVM minus all recorded liens; negative equity shows signed number; unverified rows with a stored pct show the number (not n/a) as of 2026-10-04
- Lead detail: property facts, loan/mortgage rows, lien tile, court records
- Skip-trace: DealMachine → Tracerfy → REAPI waterfall, mobile-only, result cached
- Deal analysis (ARV / rehab / offer / ROI calculator)
- CSV export (Pro/Premium)
- Auth: signup, login, logout, password reset via email, Google SSO
- Stripe billing: checkout, trial, plan upgrade, webhook → plan update
- Document pull: $5 Stripe Checkout, order written to DB, Telegram alert to operator
- Service worker / PWA install
- iOS TestFlight build via GitHub Actions
- Security headers (CSP in report-only mode, HSTS, X-Frame-Options, etc.)
- Public SEO deal pages (/deals/<slug>)
- Admin-gated `_folder` routes

**VERIFIED — data pipeline:**
- Daily REAPI delta feed (new auction IDs only, 12:00 UTC)
- JIT property detail fetch on lead open (fills liens/mortgages, cached to crm.offramp_lookups)
- Skip-trace router with per-vendor benching on 401/402
- CourtListener RECAP bankruptcy lookup
- Obituary → deceased owner match cron

---

## Known Issues

### UAT Failures (2 remaining — not code bugs)

**Issue:** 2 UAT assertions fail on one specific lead's REAPI property facts.  
**Severity:** Low — data gap for one test lead, not a product defect.  
**Root cause:** REAPI returns no property facts for that particular APN.  
**Workaround:** None needed; real leads load correctly.  
**Status:** Accepted; will resolve if REAPI coverage improves for that county.

### n/a Equity on Truly Blank Rows

**Issue:** ~1,350 rows nationally (vs ~5,497 before the 2026-10-04 fix) still show "n/a / equity unverified" because `equity_pct` is genuinely NULL in the database.  
**Severity:** Low — these are rows where no equity estimate is possible (unrecorded/private liens, feed-overwrite incident rows with mortgage_balance=0).  
**Root cause breakdown:**
- ~20 rows: 2026-08-03 feed-overwrite incident set mortgage_balance to 0
- ~6 rows (ID): unrecorded lien (seller-finance, tax, HOA) — no pct possible
- Remaining: REAPI data gap (no lien data returned for those counties/states)

---

## External Data Limitations

These are REAPI/DealMachine data gaps, not software defects:

- **REAPI lien coverage:** Some counties have incomplete lien data. Rows where REAPI returns only an "open-lien estimate" flag were previously shown as n/a; now they show the stored pct (2026-10-04 fix). Truly blank rows remain n/a.
- **REAPI auction coverage:** Not all states have equal auction data depth. Idaho appears to have higher n/a rates due to REAPI coverage gaps.
- **DealMachine skip-trace:** org 23501 was cancelled (2026-10-03). Waterfall now skips it. Org 40711 is primary.
- **CourtListener:** Free tier, 5,000 requests/day. Rate limit is a ceiling on court record enrichment throughput.

---

## Work In Progress

- **Go-live decision:** Tonight vs Sunday 10/5 11 AM MT — awaiting Ted's call
- **Equity fix commit:** 2026-10-04 fix (show pct for unverified rows with a value) needs to be committed and pushed to offramp-website main — currently only in server memory after pm2 restart

*Note: The equity fix to `app/index.html` line 1075 was applied and server restarted but has not yet been committed to Git as of this writing. Commit it.*

---

## Recently Completed (2026-10-03 — 2026-10-04)

- **Equity display fix:** unverified rows with a stored pct now show the number; n/a reserved for truly blank rows (2026-10-04)
- **README:** full project README written and pushed (commit 447f95a)
- **UAT:** 713/715 passing (from 17 failures at session start)
- **Gzip JSON responses** (perf, 2026-10-03): responses >1KB gzipped at origin
- **Prefetch Leaflet** (perf): map tiles load earlier
- **Security headers** (2026-10-03): HSTS, X-Frame-Options, CSP (report-only), referrer policy
- **CSP report endpoint** `/api/csp-report` for monitoring
- **Password reset flow** (2026-10-03)
- **Login gate on `_folder` routes** (2026-10-03)
- **Negative equity display** (2026-10-03): shows signed number, not "Unverified"
- **$5 document pull** (2026-10-03): Stripe one-time checkout, manual fulfilment
- **30-day trial** (2026-10-03): `OFFRAMP_TRIAL_DAYS=30` at launch, converts to Pro
- **Terms acceptance gate** (2026-10-03)
- **JIT lien fill on lead open** (2026-10-03): no bulk pulls, scarcity mode
- **Skip-trace router** (2026-10-03): DM 40711 → Tracerfy → REAPI → DM 23501 (23501 dropped)
- **CourtListener RECAP** (2026-10-03): bankruptcy lookup on deal detail
- **Obituary cron** (2026-10-03): deceased_flag from obit name/city match
- **Funnel events + report** (2026-10-03)
- **Service worker v46**

---

## Launch Blockers

- **Go-live approval:** Ted has not confirmed launch date/time. No technical blockers remain.

---

## Non-Blocking Issues (safe after launch)

- Apple Developer enrollment (requires human action on Apple's side — Mon PM per Ted)
- Play Console setup (iris@revivebuyers.com) — Android not a launch requirement
- DataTree sign-up ($5 JIT document source — Ted must complete checkout manually)
- OAuth denylist (security hardening post-launch)
- CSP flip from REPORT_ONLY to enforced (after monitoring period)
- Desktop two-column layout (mockup pending Ted sign-off)
- Postcards feature (awaiting Ted GO)
- FOUNDING promo code retirement
- Seat cap for founding members
- 76 deceased flag review/GO

---

## Where to Begin if Continuing Development

1. Read this file and `docs/ROADMAP.md`
2. Check `git log --oneline -10` to see what just shipped
3. Confirm any uncommitted changes: `git status`
4. Run `node scripts/app_smoke.mjs` to verify baseline
5. Check Ted's Telegram messages for most recent direction
