# Data Model

*VERIFIED from server.py, Supabase PostgREST calls, and pipeline service code as of 2026-10-04.*

---

## Database

Supabase (PostgreSQL). All server-side access goes through the PostgREST REST API at `https://scnpwjyjbcmbjzwgivlu.supabase.co/rest/v1/`. The app uses both the anon key (read-only for public routes) and service-role key (writes, user data). The `crm` schema is the canonical namespace; PostgREST is configured to expose it. All table names below are within `crm`.

---

## Core Tables

### `hit_list`

The main foreclosure auction lead table. Every active listing the app displays comes from here (for the enriched UT/ID state feed) or from `offramp_national_listings` (national auction.com feed). Primary key: `id` (integer).

Key columns:

| Column | Type | Notes |
|---|---|---|
| `id` | int | PK |
| `addr_key` | text | Normalized address key — the dedup/cache anchor across all pipeline tables |
| `property_street`, `property_city`, `property_state`, `property_zip` | text | Property address components |
| `owner_first`, `owner_last`, `owner_full` | text | Owner of record |
| `auction_date` | date | Scheduled trustee sale date |
| `days_to_auction` | int (generated) | Computed from `auction_date`; negative = past due |
| `trustee_sale_number` | text | TSN from the foreclosure notice |
| `opening_bid` | numeric | Lender's opening bid amount |
| `active` | bool | False = delisted, cancelled, or archived |
| `equity_pct` | int | Calculated equity % (AVM minus all liens); Math.round, never decimal |
| `equity_value` | numeric | AVM minus total lien balance |
| `avm` | numeric | Automated valuation model (from REAPI) |
| `lien_count` | int | Number of recorded liens on the property |
| `mortgage_balance` | numeric | Total recorded lien balance (liens-netted, not just first mortgage) |
| `phones` | jsonb | Skip-traced phone numbers (array of objects) |
| `emails` | jsonb | Skip-traced email addresses |
| `skip_traced_at` | timestamptz | When skip-trace was last run |
| `skiptrace_attempts` | int | Number of skip-trace attempts (caps at 2 by default) |
| `court_records` | jsonb | CourtListener/PACER results cached here |
| `deceased_flag` | bool | Flagged if owner appears deceased (requires corroboration — name-only SSA match is not sufficient) |
| `tags` | text[] | Free-form tags (e.g. `reapi-inherited`) |
| `reapi_detail_index` | int | FK to `reapi_detail_index.id` (pipeline REAPI detail cache) |
| `updated_at` | timestamptz | Last modified |

**Equity calculation (VERIFIED):** Equity = `avm - mortgage_balance`. Negative when liens exceed AVM — shown as a signed number (e.g. −12%). Display `n/a` only when `equity_pct` IS NULL (no lien verification possible). Never show `n/a` for a row that has a stored `equity_pct` value, even if it was not independently verified via the live pipeline.

---

### `offramp_users`

Subscriber accounts. Created on signup. Primary key: `id` (UUID).

Key columns:

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | PK |
| `email` | text | Unique; used as login identifier |
| `password_hash` | text | bcrypt; null for SSO-only accounts |
| `name` | text | Display name |
| `plan` | text | `free` \| `pro` \| `premium` |
| `founding` | bool | Founding-member flag (grandfathered price/features) |
| `stripe_customer_id` | text | Stripe Customer object ID |
| `stripe_subscription_id` | text | Active Stripe Subscription ID |
| `stripe_status` | text | `active` \| `trialing` \| `past_due` \| `canceled` etc. |
| `google_sub` | text | Google OAuth subject ID (SSO accounts) |
| `terms_accepted_at` | timestamptz | When user accepted ToS |
| `trial_ends_at` | timestamptz | Trial expiry (set from Stripe or OFFRAMP_TRIAL_DAYS) |
| `skiptrace_credits_used` | int | Monthly skip-trace meter |
| `last_login` | timestamptz | Most recent authenticated request |
| `created_at` | timestamptz | Signup timestamp |

**Plan hierarchy:** `free` < `pro` < `premium`. Founding members get Premium-equivalent features at the founding price tier.

---

### `offramp_lookups`

Enrich-once cache for REAPI PropertyDetail. Keyed on `address_key`. When a paid user opens a lead, the app calls REAPI once and stores the full response here. Subsequent opens read from cache. Free users get the pipeline-cached data from `reapi_detail_index` / `reapi_calls` instead.

Key columns: `id`, `address_key`, `payload` (jsonb, full REAPI PropertyDetail response), `source` (`reapi`), `body` (raw), `fetched_at`.

---

### `reapi_calls` + `reapi_detail_index`

Pipeline REAPI cache maintained by `services/reapi-auction-feed/`. `reapi_calls` stores every raw REAPI response. `reapi_detail_index` maps `addr_key` → `reapi_property_id` for fast lookups. Expression index `reapi_calls_data_id_idx` on `response->data->>id` (added 2026-10-03) enables join without a FK.

The server reads from this pipeline cache for free-tier and for the property facts block before falling back to a live REAPI call.

---

### `offramp_doc_orders`

One row per $5 document pull purchase. Written by the Stripe checkout webhook.

Key columns: `id`, `user_id`, `user_email`, `hit_list_id` (nullable — the property it was ordered for), `doc` (document type key), `status` (`paid`), `stripe_session_id`, `amount_cents`, `property_label`, `created_at`.

Orders are fulfilled manually by Ted within 24h; Telegram pings on each new order.

---

### `offramp_funnel_events`

Conversion funnel tracking. Events: `signup`, `trial_start`, `paid`, `view_lead`, `skiptrace`, etc. Columns: `event`, `user_id`, `path` (attribution), `created_at`.

---

### `offramp_national_listings`

National auction.com foreclosure feed. Separate from the enriched `hit_list`. Columns mirror `hit_list` search columns so the app can merge both sources. Key filtering: `status_group = 'ACTIVE'` and `delisted_at IS NULL`. Not skip-traced; no REAPI detail cache.

---

## Important Derived/Calculated Values

| Value | Where | Formula |
|---|---|---|
| Equity % | `hit_list.equity_pct` | `ROUND((avm - mortgage_balance) / avm * 100)` — integer, never decimal |
| Days to auction | `hit_list.days_to_auction` | Generated column: `auction_date - CURRENT_DATE` |
| Credit meter | `offramp_users.skiptrace_credits_used` | Incremented in `meter_credit()` in server.py; resets monthly |
| Overage billing | Stripe invoice items | Each credit over limit = $0.35 Stripe invoice item on next invoice |

---

## Caching Layers (summary)

1. `offramp_lookups` — enrich-once REAPI PropertyDetail for paid users (permanent, per address)
2. `reapi_calls` / `reapi_detail_index` — pipeline REAPI cache for free users and the facts block
3. In-memory state counts (`_STATE_CACHE`, `_NATIONAL_CACHE`) — 10-minute TTL, refreshed by background thread
4. In-memory promotion code count (`_PROMO_CACHE`) — 60-second TTL
