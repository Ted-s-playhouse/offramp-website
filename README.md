# OffRamp REI

Foreclosure deal room for real estate investors. Pulls active pre-auction listings from REAPI, shows equity estimates, and lets subscribers skip-trace, export, and analyze deals.

**Prod:** https://offramprei.com  
**Staging:** https://offramp-staging.americahomerestoration.com  
**Repo:** github.com/Ted-s-playhouse/offramp-website (branch: `main`)

---

## Key files

| Path | What it is |
|---|---|
| `server.py` | Python HTTP server — serves static files + all `/api/*` routes |
| `app/index.html` | The entire deal room app (single-file, inline JS/CSS) |
| `app/sw.js` | Service worker — bump `VERSION` on every app push |
| `scripts/app_smoke.mjs` | Smoke test — run before every app push |
| `scripts/detail_parity.mjs` | Card/detail parity test |
| `tests/uat/run_uat.mjs` | Full UAT suite |
| `seo_pages.py` | Public SEO deal pages (served at `/deals/<slug>`) |
| `ios-wrapper/` | Capacitor iOS wrapper (builds to TestFlight via GitHub Actions) |

---

## Running locally

```bash
cd orgs/AHR/sites/offramp
python3 server.py
# Server on 127.0.0.1:8092
```

Secrets are read from `~/.cortextos/secrets/`. The server degrades gracefully if optional keys (Stripe, REAPI, DealMachine) are missing.

---

## Before pushing to main

1. **Smoke test** (always):
   ```bash
   node scripts/app_smoke.mjs
   ```
2. **Bump service worker version** in `app/sw.js` — forces cache refresh for all users.
3. **UAT** (for any server.py or search logic changes):
   ```bash
   node tests/uat/run_uat.mjs --states=UT
   ```

---

## Deploying to staging

```bash
OFFRAMP_TRIAL_LIVE=1 OFFRAMP_TRIAL_DAYS=30 pm2 restart offramp-staging --update-env
```

Staging is behind Cloudflare proxy. Changes are live immediately after restart. Verify with:
```bash
curl -s https://offramp-staging.americahomerestoration.com/ -o /dev/null -w "%{http_code}"
```

---

## Branches

| Branch | Purpose |
|---|---|
| `main` | Production — what offramprei.com and staging both run |
| `perf-2026-10-03` | Performance work (merged) |
| `sec-hardening-2026-10-03` | Security headers (merged) |
| `uat-fixes-2026-10-03` | UAT runner fixes (merged) |

Feature branches: name them `<topic>-YYYY-MM-DD`. Merge to `main` when UAT passes.

---

## iOS

GitHub Actions auto-builds to TestFlight on every push to `main` that touches `ios-wrapper/`. Requires secrets: `ASC_KEY_ID`, `ASC_ISSUER_ID`, `ASC_KEY_P8`, `APPLE_TEAM_ID`, `MATCH_PASSWORD`, `MATCH_GIT_TOKEN`.

Apple Developer enrollment must be completed manually (requires human approval).

---

## Database

Supabase project `scnpwjyjbcmbjzwgivlu`. Main tables:

- `crm.hit_list` — active foreclosure leads (the deal feed)
- `crm.offramp_users` — subscriber accounts
- `crm.offramp_lookups` — cached REAPI property detail lookups
- `crm.offramp_funnel_events` — conversion funnel beacons

Service role key: `~/.cortextos/secrets/supabase-service-role-key`
