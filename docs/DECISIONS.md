# Architecture Decision Records

*Significant decisions that shaped the system. Trivial implementation choices are not recorded here.*

---

## ADR-001: Single-file SPA (app/index.html)

**Date:** 2026-07 (INFERRED from early commits)  
**Decision:** The entire deal room app is one HTML file with all JS/CSS inline.  
**Context:** Needed fast iteration without build tooling overhead.  
**Options considered:** React/Next.js SPA, Vue, plain HTML with separate JS files.  
**Decision made:** Single inline file.  
**Why:** Zero build step, zero npm dependency chain, instant deploy (just edit the file and restart). Fast to iterate during product discovery phase.  
**Consequences:** File grows large (~135KB as of 2026-10-03). No module system makes large refactors harder. Service worker caching is all-or-nothing for the app (must bump VERSION on every meaningful change).

---

## ADR-002: Python stdlib-only server

**Date:** 2026-07 (INFERRED)  
**Decision:** `server.py` uses only Python 3 stdlib — no Flask, FastAPI, or external HTTP library.  
**Context:** Simplicity, minimal dependencies, easy to read.  
**Options considered:** Flask, FastAPI, Django.  
**Decision made:** Raw `http.server.ThreadingHTTPServer`.  
**Why:** No pip installs, no virtual env, no framework version drift. The server does not need middleware, ORM, or framework routing — all routes are simple conditionals.  
**Consequences:** No automatic request parsing, no framework security middleware, no async. All features implemented by hand. Concurrency ceiling is OS thread count (no async I/O). Acceptable for current user scale.

---

## ADR-003: Supabase as database

**Date:** 2026-07 (INFERRED)  
**Decision:** Supabase (hosted PostgreSQL + PostgREST) as the database.  
**Context:** Needed a database that could be set up quickly with a REST API layer.  
**Options considered:** Self-hosted PostgreSQL, PlanetScale, Firebase.  
**Decision made:** Supabase.  
**Why:** Fast setup, PostgREST provides a REST API without writing routes for simple CRUD, hosted = no DBA work, generous free tier.  
**Consequences:** PostgREST has limitations (aggregates disabled on this project: PGRST123). Complex queries require the management API (raw SQL endpoint) or server-side processing. Supabase pricing scales with usage.

---

## ADR-004: REAPI auction-only feed (not NOD/pre-foreclosure)

**Date:** 2026-10-03 (Ted, Telegram)  
**Decision:** OffRamp pulls only `auction:true` rows from REAPI. Pre-foreclosure / NOD lists are explicitly excluded.  
**Context:** Ted's standing order: "OffRamp feed = REAPI auction:true only; never pre_foreclosure lists; foreclosure.com RETIRED."  
**Why:** Auction listings are actionable (known sale date, known venue). NOD lists are noisier and generate leads too far from the auction to be immediately actionable for the target investor.  
**Consequences:** Feed is smaller than a full foreclosure list but higher quality. Some early-stage deals are missed.

---

## ADR-005: equityView() runs client-side

**Date:** 2026-10-03 (INFERRED from code)  
**Decision:** Equity calculation (AVM minus all recorded liens) runs in the browser in `equityView()`.  
**Context:** The server also has `_client_equity_pct()` for the `min_equity` search filter, which mirrors the same logic.  
**Why:** Keeps the card display and the search filter in sync using the same business rule. Server-side filter can't use the same per-row lien netting without materializing the calculation in the DB.  
**Consequences:** Business logic lives in two places (server.py `_client_equity_pct()` and app/index.html `equityView()`). Must be kept in sync manually. A bug in one place will cause search/card discrepancies.

---

## ADR-006: CSP shipped as Report-Only at launch

**Date:** 2026-10-03  
**Decision:** Content-Security-Policy header is sent as `Content-Security-Policy-Report-Only` at launch, not enforced.  
**Context:** The app uses inline scripts and styles throughout (ADR-001 consequence). A strict CSP would break functionality.  
**Why:** Gather violation reports via `/api/csp-report` first. Once violations are quiet for several days, flip `CSP_REPORT_ONLY = False` in server.py.  
**Consequences:** CSP is not enforced at launch. XSS is not mitigated by the header until the flip. Other security headers (HSTS, X-Frame-Options, etc.) are enforced.

---

## ADR-007: Stripe live mode from day one

**Date:** 2026-09-27  
**Decision:** Stripe was flipped to live mode before any public subscribers.  
**Context:** Ted's direction to be ready to accept real payments at launch.  
**Why:** Avoids test-mode/live-mode migration risk. Test-mode product/price/coupon IDs were created fresh in live account.  
**Consequences:** Any billing code path runs against real money. Test-mode files kept on disk for rollback reference. `OFFRAMP_STRIPE_MODE=test` env var available for local billing development.

---

## ADR-008: Skip-trace is JIT on paid tap only

**Date:** 2026-10-03 (Ted, Telegram)  
**Decision:** Skip-trace runs only when a subscriber explicitly taps the skip-trace button (Pro/Premium feature). No bulk pre-enrichment.  
**Context:** Ted's standing order: JIT mode, scarcity, no bulk pulls.  
**Why:** Avoids burning DealMachine/Tracerfy credits at scale. Each credit costs real money ($0.02-$0.05).  
**Consequences:** Subscribers see empty contact info until they tap. First load is slower (API call happens on tap). Result is cached on the row so repeat taps are free.

---

## ADR-009: Shared prod/staging process

**Date:** UNKNOWN (INFERRED from deployment config)  
**Decision:** One pm2 process (`offramp-staging`) serves both offramprei.com and offramp-staging.americahomerestoration.com via Cloudflare routing.  
**Why:** INFERRED — simplicity, single server.  
**Consequences:** No true staging environment. A bad restart or code bug affects prod immediately. Acknowledged risk; acceptable at current scale.
