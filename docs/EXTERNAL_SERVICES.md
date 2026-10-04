# External Services

*VERIFIED from server.py, skiptrace_router.py, and configuration files as of 2026-10-04.*

---

## Supabase

**Purpose:** Primary database (PostgreSQL). All persistent data storage.

**Endpoints used:**
- PostgREST REST API at `https://scnpwjyjbcmbjzwgivlu.supabase.co/rest/v1/` — all CRUD operations
- Management API at `https://api.supabase.com/v1/projects/{ref}/database/query` — raw SQL for state-count aggregation (read-only, uses PAT token)

**Auth:** Service-role key (writes, user data reads) + anon key (public routes). PAT for management API.

**Costs:** Subscription. No per-query cost.

**Caching:** In-memory state count cache (10-min TTL) reduces PostgREST calls.

**Rate limits:** None known for service-role key at current volume.

**Failure behavior:** Supabase errors propagate as HTTP 5xx to the client. State count cache prevents total failure on short Supabase downtime.

**Required for core functionality:** YES — application cannot function without it.

---

## REAPI (RealEstateAPI)

**Purpose:** Property data (AVM, owner, mortgage, liens), foreclosure auction listings, skip-trace.

**Plan:** Growth ($1,545/mo, ~150k records/month). Live since 2026-09-13.

**Endpoints used:**
- `POST /v2/PropertyDetail` — full property record with AVM, liens, owner
- Property search and auction-feed endpoints (used by pipeline services, not server.py directly)

**Auth:** `x-api-key` header (key file: `secrets/realestateapi-key.json`).

**Costs:** Growth plan subscription. Overage at per-record rate. ~0.4% of monthly quota used as of 2026-10-03.

**Caching:** `crm.offramp_lookups` — enrich-once per address (permanent). `crm.reapi_calls` / `crm.reapi_detail_index` — pipeline cache used as fallback (avoids re-pulling data already fetched by the pipeline).

**Rate limits:** Not published; Growth plan has generous quota.

**Failure behavior:** `reapi_lookup()` returns `(None, error)` on HTTP errors; server returns `{"error": "..."}` to client. Property facts block falls back silently if unavailable.

**Skip-trace:** REAPI is the third fallback in the waterfall (DealMachine → Tracerfy → REAPI → DM org 23501).

**Required for core functionality:** YES — property data and auction listings depend on it.

---

## DealMachine

**Purpose:** Skip-trace (phone and email lookup for property owners). Two org credentials.

**Orgs:**
- **Org 23501** ("Ted Sanders's Team") — Pro Classic, 30k/mo, resets 7th. Key: `secrets/dealmachine-skiptrace-api-key`. *Being cancelled as of 2026-10-03; moved to last-resort fallback.*
- **Org 40711** ("Sell Your House Fast…") — Pro Plus Classic, 60k/mo, resets 15th. Key: `secrets/dealmachine-v2-key`. This is the primary skip-trace org.

**Endpoints used:**
- `POST https://api.v2.dealmachine.com/v1/enrichment/address` — owner contact enrichment

**Costs:** Per-skip-trace credit consumption. Check `/v1/usage` on both orgs before batch operations.

**Caching:** Results written back to `hit_list.phones`, `hit_list.emails`, `hit_list.skip_traced_at`. Skip-trace is not re-run if `skip_traced_at` is set and `skiptrace_attempts` < cap.

**Rate limits:** Monthly credit pool per org (30k / 60k).

**Failure behavior:** `skiptrace_router` waterfall — if DealMachine fails or is out of credits, falls to Tracerfy.

**Required for core functionality:** NO — app works without skip-trace; contact info simply won't appear.

---

## Tracerfy

**Purpose:** Skip-trace fallback (second in waterfall after DealMachine org 40711).

**Costs:** Per-lookup. Credit-based (separate account). Was returning 402 (insufficient credits) in late September 2026 — requires credit top-up.

**Failure behavior:** 402 errors skip to next waterfall leg (REAPI).

**Required for core functionality:** NO.

---

## Stripe

**Purpose:** Subscription billing, checkout, webhooks, overage invoice items. Live mode active since 2026-09-27.

**Features used:**
- Checkout Sessions (subscription + payment)
- Subscriptions + Subscription Items
- Customer objects (linked to `offramp_users.stripe_customer_id`)
- Promotion Codes (founding-member discount tracking)
- Invoice Items (overage billing — $0.35/credit over limit)
- Billing Portal
- Webhooks: `checkout.session.completed`, `customer.subscription.updated`, `customer.subscription.deleted`

**Auth:** Secret key (`secrets/stripe-secret-offramp-live`). Webhook signature verified via `stripe-webhook-offramp-secret-live`.

**Costs:** Stripe transaction fees on all payments.

**Caching:** Promotion code redemption count cached 60s in memory.

**Failure behavior:** Webhook failures are retried by Stripe. Failed Stripe API calls propagate as 5xx to client.

**Test mode:** Controlled by `OFFRAMP_STRIPE_MODE=test` env var; uses separate keys and IDs file. Default is live mode.

**Required for core functionality:** Required for paid subscriptions. Free tier works without it.

---

## Resend

**Purpose:** Transactional email (password reset, signup confirmation).

**Auth:** API key from `secrets/resend-key` (INFERRED — not verified in server.py directly, but `FROM = "OffRamp REI <hello@offramprei.com>"` confirms Resend is the sending provider).

**Costs:** Resend pricing tier (INFERRED).

**Required for core functionality:** Required for password reset flow. SSO users unaffected.

---

## Telegram (Bot API)

**Purpose:** Operator notifications (new signups, doc orders, billing events, skip-trace alerts).

**Endpoints used:** `POST https://api.telegram.org/bot{token}/sendMessage`

**Auth:** Bot token from `secrets/telegram-offramp-bot-token` (INFERRED from `notify_telegram()` in server.py).

**Costs:** Free.

**Failure behavior:** `notify_telegram()` catches exceptions and logs `[notify] telegram deferred`. Non-blocking — failures do not affect the user-facing response.

**Required for core functionality:** NO — operator notification only.

---

## Google (OAuth / SSO)

**Purpose:** Google Sign-In for subscribers.

**Endpoints used:** Google OAuth 2.0 token verification (INFERRED from `google_sub` column and SSO signup flow).

**Auth:** Google OAuth client credentials (INFERRED — client ID/secret in secrets directory).

**Costs:** Free.

**Required for core functionality:** NO — email/password auth works without it.

---

## Cloudflare

**Purpose:** DNS, reverse proxy, TLS termination, DDoS protection.

**Both domains route through Cloudflare to the same `server.py` process:**
- `offramprei.com` (production)
- `offramp-staging.americahomerestoration.com` (staging)

**Required for core functionality:** YES for public access. Direct IP:port access works for local/internal use.

---

## CourtListener / PACER

**Purpose:** Bankruptcy and court record lookup for property owners.

**Endpoints used:** CourtListener API (INFERRED from `court_records` column and `court_records` fetch logic in server.py).

**Costs:** CourtListener is free for most queries. PACER has per-page fees (INFERRED).

**Caching:** Results stored in `hit_list.court_records` (jsonb) permanently.

**Required for core functionality:** NO — court records are a supplemental data point.

---

## Summary Table

| Service | Purpose | Core? | Costs money? |
|---|---|---|---|
| Supabase | Database | YES | Subscription |
| REAPI | Property data / auctions | YES | Subscription + overage |
| DealMachine | Skip-trace (primary) | NO | Per-credit |
| Tracerfy | Skip-trace (fallback) | NO | Per-credit |
| Stripe | Billing | For paid plans | Transaction fees |
| Resend | Transactional email | For password reset | Subscription |
| Telegram | Operator alerts | NO | Free |
| Google | SSO | NO | Free |
| Cloudflare | DNS/TLS/proxy | For public access | Subscription |
| CourtListener | Court records | NO | Free/PACER fees |
