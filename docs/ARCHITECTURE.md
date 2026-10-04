# Architecture

*Status: VERIFIED from server.py, app/index.html, and git history as of 2026-10-03.*

---

## System Overview

OffRamp REI is a single-process Python application. One `server.py` process handles all concerns: static file serving, API routing, authentication, database queries, and external API proxying. There is no application framework.

```
Browser / iOS app
      │
      ▼
Cloudflare (proxy + TLS)
      │
      ▼
server.py on 127.0.0.1:8092 (ThreadingHTTPServer)
      │
      ├── Static files: index.html, sw.js, public pages
      ├── /api/* — authenticated deal room API
      ├── /api/auth/* — login / signup / SSO / reset
      ├── /deals/* — public SEO listing pages (seo_pages.py)
      └── /_<folder>/* — admin-gated deliverables
            │
            ├── Supabase (PostgreSQL via PostgREST + management API)
            ├── REAPI — property data, auctions, liens
            ├── DealMachine / Tracerfy — skip-trace
            ├── Stripe — billing
            ├── Resend — transactional email
            ├── Google Maps API — geocoding
            └── CourtListener RECAP — court records
```

---

## Backend

**Language:** Python 3, stdlib only. No Flask, FastAPI, Django, or external HTTP frameworks.

**Process model:** `ThreadingHTTPServer` — one thread per request, bounded by the OS. Suitable for the current user scale; a single GIL-bound process becomes the ceiling under concurrent load.

**File:** `server.py` (~135K bytes as of 2026-10-03). All routes, business logic, and config in one file.

**Port:** 8092 (configurable via `OFFRAMP_PORT`). Binds to localhost; Cloudflare proxies inbound HTTPS.

**Config:** Environment variables + secrets read from `~/.cortextos/secrets/` at startup. Optional keys (Stripe, REAPI, etc.) degrade gracefully — features disable if keys are absent.

---

## Frontend

**File:** `app/index.html` — the entire deal room SPA. All JavaScript and CSS are inline (no `<script src>` for app code, no separate CSS files, no build step).

**Why single-file:** Zero build toolchain, fast iteration, easy deployment (serve the file). Tradeoff: the file grows large; refactoring is harder without module boundaries.

**Map:** Leaflet.js loaded from unpkg CDN. OpenStreetMap tiles. Address-pinned; no browser geolocation used.

**Service worker:** `app/sw.js`. Caches static assets for offline/PWA use. `VERSION` constant must be bumped on every push that changes app behavior; mismatch causes stale UI for returning users.

**iOS:** Capacitor wrapper in `ios-wrapper/`. The WebView loads the same `app/index.html` served from the live server. GitHub Actions (`ios-testflight.yml`) builds and uploads to TestFlight on every push to `main` that touches `ios-wrapper/`.

---

## Database

**Provider:** Supabase (PostgreSQL). Project ID: `scnpwjyjbcmbjzwgivlu`.

**Access pattern:** Two paths:
1. **PostgREST** (`/rest/v1`) — simple CRUD and filtered reads. Used for the deal feed, user management, and most API routes.
2. **Supabase management API** (`/v1/projects/.../database/query`) — raw SQL for aggregates PostgREST cannot express (GROUP BY is disabled on this project: PGRST123). Used for state counts and analytics.

**Schema:** All application tables live in the `crm` schema. See [DATA_MODEL.md](DATA_MODEL.md).

**Service role key:** Used server-side; never sent to the browser.

---

## Authentication

**Primary:** HMAC-SHA256 signed session cookie. 30-day TTL. Verified on every authenticated request.

**SSO:** Google OAuth 2.0 (authorization-code flow). Reuses the `revivebuyers` web OAuth client. Apple OAuth is stubbed (code present, not wired to real Apple credentials as of 2026-10-03).

**Session storage:** Cookie only — no server-side session store yet. This means individual sessions cannot be revoked without rotating the SESSION_SECRET (all sessions). INFERRED: this is an acknowledged gap from SECURITY-CONSIDERATIONS.md item 1.

**Password reset:** HMAC-signed single-use token, 30-minute TTL, delivered via Resend.

---

## API Request Flow (authenticated deal room call)

```
1. Browser sends GET /api/search?state=UT&min_equity=20
2. server.py reads session cookie → HMAC verify → load user from crm.offramp_users
3. Plan check: enforce lookup credits, plan tier, feature gates
4. Build PostgREST query against crm.hit_list (active=true, auction feed rows)
5. Fetch all matching rows via PostgREST (paginated internally)
6. Post-filter: equityView() logic runs server-side for /api/export; client-side for /api/search
7. Return JSON (gzip if >1KB, OFFRAMP_GZIP=1 by default since 2026-10-03)
8. app/index.html renders cards; equityView() runs client-side to calculate displayed equity
```

**Equity calculation** runs client-side in `equityView()` (app/index.html). It nets all recorded liens against AVM, not just the first mortgage. This ensures the displayed equity matches the server filter (which uses `_client_equity_pct()` in server.py for the `min_equity` filter). See [DATA_MODEL.md](DATA_MODEL.md) for the formula.

---

## Caching

| Layer | What | TTL |
|---|---|---|
| `cache/photos/` | Street View / satellite images | Permanent (by address hash) |
| `crm.offramp_lookups` | REAPI PropertyDetail responses | Permanent (JIT, paid call on first open) |
| In-memory `_state_counts_cache` | State chip counts | 10 minutes |
| Service worker | Static assets (HTML, SW itself) | Until VERSION bumped |

No Redis or external cache. In-memory caches are per-process and reset on restart.

---

## Billing

**Provider:** Stripe (live mode since 2026-09-27).

**Flow:** Stripe Checkout (hosted payment page) → webhook → `crm.offramp_users.plan` updated → Telegram alert to operator.

**Plans:** Free, Pro ($49/mo), Premium ($99/mo), 30-day trial (converts to Pro on day 31).

**Overage:** Pro/Premium users who exceed their lookup allocation are billed $0.35/credit via Stripe invoice item on the next billing cycle.

**Document pulls:** $5 one-time Stripe Checkout (mode=payment), fulfilled manually by operator within 24h.

**Test mode:** `OFFRAMP_STRIPE_MODE=test` switches to test-mode Stripe credentials. pm2 never sets this; it is for local billing development only.

---

## Background Processes

No in-process background threads for data fetching. Data pipeline runs are external:

- **REAPI auction feed:** Daily delta pull (cortextos daemon cron `reapi-auction-feed-daily`, 12:00 UTC)
- **Enrichment worker:** `services/enrichment-worker/worker.py` (separate process, enriches hit_list rows with REAPI PropertyDetail)
- **Obituary match cron:** Daily — matches obit names against hit_list owners, sets `deceased_flag`
- **doc-order-fulfil cron:** Every 2h 8am-8pm MT — alerts operator to pending document orders

---

## Security Boundaries

- **Cloudflare:** TLS termination, DDoS protection. Server never sees raw TLS.
- **Localhost bind:** server.py binds to `127.0.0.1:8092` only. No direct internet access.
- **CSP:** Shipped as `Content-Security-Policy-Report-Only` at launch. Reports to `/api/csp-report`. Flip `CSP_REPORT_ONLY = False` in server.py when reports are quiet.
- **`_` folders:** All `/_<name>/` routes require admin login unless listed in `public_folders.json` (checked every 30s without restart). `ALWAYS_GATED` set overrides the allowlist.
- **Secrets:** Never in the repo. Read from `~/.cortextos/secrets/` at startup.
- **Service role key:** Server-side only. PostgREST anon key is not used — all DB calls use the service role key from the server process.

---

## Deployment Topology

```
DNS: offramprei.com → Cloudflare (proxied)
     offramp-staging.americahomerestoration.com → Cloudflare (proxied)
          │
          ▼
     VPS (Linux)
          │
     pm2: offramp-staging (server.py, port 8092)
          │
     Supabase cloud (PostgreSQL)
```

Both `offramprei.com` and `offramp-staging.americahomerestoration.com` point to the same pm2 process. "Staging" is the process name; prod and staging share a server. Production isolation is via Cloudflare routing.

**INFERRED:** No separate prod/staging environment. This is a risk: a bad restart affects prod immediately. The pm2 process restart is atomic (old process stays up until new one is ready).
