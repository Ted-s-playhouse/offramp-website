# Changelog

Meaningful product changes by date. Does not duplicate raw Git history — see `git log` for commit-level detail.

---

## 2026-10-04

### Fixed
- **Equity display:** Cards with `equity_unverified=true` but a stored `equity_pct` now show the percentage normally instead of "n/a / equity unverified". Only rows with no pct value at all show n/a. Nationally reduces visible n/a from ~5,497 to ~1,350. Idaho improves from 122 → 21 n/a rows.

### Docs
- README expanded to full governance standard
- `/docs/` baseline created (ARCHITECTURE, CURRENT_STATE, ROADMAP, DECISIONS, CHANGELOG, TESTING, DEPLOYMENT, DATA_MODEL, EXTERNAL_SERVICES)

---

## 2026-10-03

### Added
- **$5 Document pull:** "Get it · $5" button on lead detail for mortgage/NOD/lis pendens/bankruptcy. Stripe one-time checkout, result fulfiled manually by operator within 24h. Order written to `crm.offramp_doc_orders`, Telegram alert sent.
- **30-day trial:** Trial converts to Pro ($49/mo) on day 31. `OFFRAMP_TRIAL_DAYS=30` set in pm2 env at launch. Gated behind `OFFRAMP_TRIAL_LIVE=1`.
- **Terms acceptance gate:** All users (including SSO) must accept Terms on first entry. `terms_accepted_at` + `terms_version` stored on user record. Bump `TERMS_VERSION` when /terms changes.
- **CourtListener RECAP:** Bankruptcy lookup on deal detail (Pro). Free API, 5,000 requests/day. Results cached on `crm.hit_list.court_records` jsonb.
- **Obituary → deceased flag cron:** Daily name+city match against hit_list. Sets `deceased_flag`, `deceased_source='obituary'`, `obit_url`, `date_of_death`.
- **Funnel events:** Conversion beacons (public_view, app_open, signup, trial_start, paid, lead). Funnel report at `/_funnel-report-3d7b/`.
- **Skip-trace router:** Waterfall — DM 40711 → Tracerfy ($0.02) → REAPI ($0.05). Per-vendor benching on 401/402 + Telegram alert. Mobile-only. Result cached on row.
- **Lead type badges:** Deceased pre-probate, MLS-active, Absentee owner, Next of kin, Pre-foreclosure chips.
- **Login gate on `_folder` routes:** All `/_<name>/` routes require admin login unless in `public_folders.json`.

### Changed
- **Equity display:** Negative equity shows signed number (−12% / −$34,000) instead of "Unverified" or "Negative".
- **Liens tile:** Tax liens + judgments only (Premium). First/Second mortgage labeled as mortgages, not "liens."
- **JIT lien fill:** Opening a lead fills its own mortgages + liens from REAPI detail (no bulk pre-pulls). Identical-amount lien deduplication.
- **Upgrade CTAs:** Every locked row/tile has an "Unlock · Pro/Premium ›" button into Stripe checkout.
- **Header credit meter:** Shows "N credits left" or plan name.
- **Skip-trace org priority:** DM org 23501 dropped; org 40711 is now primary.

### Infrastructure
- **Gzip JSON responses:** All JSON responses >1KB gzipped at origin (`OFFRAMP_GZIP=1`).
- **Prefetch Leaflet:** Map tiles load earlier.
- **Security headers:** HSTS, X-Frame-Options DENY, X-Content-Type-Options nosniff, Referrer-Policy, Permissions-Policy, CSP (Report-Only).
- **Service worker v46.**
- **Stripe → live mode** (2026-09-27): All billing runs against real Stripe account.

---

## 2026-09 (summary)

- Initial Stripe subscription wiring (Free / Pro / Premium tiers)
- Deal room v1: card list, deal detail, search, filters
- Skip-trace button (DealMachine)
- Deal analysis calculator
- CSV export
- SEO deal pages
- State chip navigation
- Mobile nav (pinned toolbar, bottom-sheet)
- Google SSO
- iOS Capacitor wrapper + TestFlight CI
- REAPI auction feed daily cron
- Enrichment worker (REAPI PropertyDetail backfill)

---

## 2026-07 — 2026-08 (summary)

- Project started
- Initial server.py and app/index.html scaffolded
- Supabase schema established
- REAPI integration (property lookup, auction feed)
- Basic auth (email/password)
- Public landing page
- Early SEO pages
