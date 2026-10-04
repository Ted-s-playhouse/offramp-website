# Testing

*VERIFIED from scripts/ and tests/ directories as of 2026-10-03.*

---

## Test Suites

### 1. App Smoke Test (`scripts/app_smoke.mjs`)

**Purpose:** Verify that the inline JS in `app/index.html` can execute without crashing, loads the search function, and renders cards against live or synthetic rows.

**Run before every app push.** Added 2026-10-03 after the "Searching…" outage (a variable reference was removed from the JS but the search function still called it — every search threw before rendering).

```bash
node scripts/app_smoke.mjs
# Uses http://127.0.0.1:8092/api/search?state=AZ&limit=200 if server is running
# Falls back to 3 synthetic rows if not
# Set OFFRAMP_SMOKE_COOKIE=<session cookie> for an authenticated live call
```

**Prerequisites:** Node.js. Server does not need to be running (falls back to synthetic rows).

**Exit codes:** 0 = pass, 1 = fail.

---

### 2. Detail Parity Test (`scripts/detail_parity.mjs`)

**Purpose:** Verify that the fields shown on a lead card match the fields shown in lead detail for the same row. Catches regressions where card and detail diverge.

```bash
node scripts/detail_parity.mjs
```

**Prerequisites:** Requires a running staging server and a valid session cookie.

---

### 3. UAT Suite (`tests/uat/run_uat.mjs`)

**Purpose:** Full end-to-end behavioral test suite covering search, filtering, equity display, skip-trace behavior, billing gates, auth flows, and detail rendering.

```bash
# Run against UT state (fastest, most data)
node tests/uat/run_uat.mjs --states=UT

# Run against multiple states
node tests/uat/run_uat.mjs --states=UT,ID,AZ
```

**Prerequisites:** Requires live staging server (`http://localhost:8092`) and a valid test session cookie (set via env var — see test runner header for the expected variable name).

**Current results (2026-10-03):** 713 pass, 2 fail. The 2 failures are a REAPI data gap for one specific test lead — not a code bug. Accepted.

---

## Pre-Push Checklist

For any change to `app/index.html` or `app/sw.js`:

1. `node scripts/app_smoke.mjs` — must pass (exit 0)
2. Bump `VERSION` constant in `app/sw.js`
3. Optionally: `node scripts/detail_parity.mjs`

For any change to `server.py` search/equity logic:

1. All three tests above
2. `node tests/uat/run_uat.mjs --states=UT`

---

## Test Data Requirements

- The UAT suite uses live data from the staging server's Supabase database.
- No separate test database exists as of 2026-10-03. Tests run against the live `crm.hit_list` table.
- INFERRED risk: if a lead used as a test fixture is archived or modified, test assertions may fail. This is the likely cause of the 2 remaining UAT failures.

---

## Known Skips / Accepted Failures

| Test | Reason | Status |
|---|---|---|
| 2 UAT assertions on one lead | REAPI data gap — no property facts for that APN | Accepted, not a code bug |

---

## External Provider Limitations in Tests

- **REAPI:** Real API calls during UAT (no mock). If REAPI is down, property detail tests will fail.
- **DealMachine:** Skip-trace UAT assertions may fail if DM is down or credits are exhausted.
- **Stripe:** Billing flow tests should use `OFFRAMP_STRIPE_MODE=test` to avoid real charges. Never run billing UAT against live Stripe unless intentional.
