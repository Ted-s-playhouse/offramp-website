# Deployment

*VERIFIED from pm2 config, server.py, and git history as of 2026-10-03.*

---

## Hosting

| Layer | Provider |
|---|---|
| Server | Linux VPS (INFERRED — process is on-prem or rented Linux) |
| Process manager | pm2 |
| Proxy / TLS | Cloudflare |
| Database | Supabase (cloud, no self-hosted component) |

---

## Processes

| pm2 name | Command | Port |
|---|---|---|
| `offramp-staging` | `python3 server.py` | 8092 |

Both production (`offramprei.com`) and staging (`offramp-staging.americahomerestoration.com`) route through Cloudflare to this one process. There is no separate prod vs. staging process.

---

## Environment Variables (pm2)

Set in the pm2 ecosystem config or via `--update-env` on restart. **Never put secret values in documentation.**

| Variable | Purpose | Required |
|---|---|---|
| `OFFRAMP_TRIAL_LIVE` | Set to `1` to enable the 30-day trial CTA | No (trial hidden if absent) |
| `OFFRAMP_TRIAL_DAYS` | Trial length in days (default 7; set to `30` at launch) | No |
| `OFFRAMP_PORT` | Server port (default 8092) | No |
| `OFFRAMP_GZIP` | Set to `0` to disable gzip (default enabled) | No |
| `OFFRAMP_ORIGIN` | Canonical origin for email links (default `https://offramprei.com`) | No |
| `OFFRAMP_STRIPE_MODE` | Set to `test` for Stripe test mode (never set in pm2 for prod) | No |
| `RESEND_API_KEY` | Resend transactional email key | Yes (email disabled if absent) |
| `BOT_TOKEN` | Telegram bot token for operator alerts | Yes (alerts disabled if absent) |

---

## Secret Files (`~/.cortextos/secrets/`)

These files are read at server startup. **Never commit their contents to Git.**

| File | Purpose |
|---|---|
| `supabase-service-role-key` | Database access (all DB calls) |
| `offramp-session-secret` | HMAC signing key for session cookies |
| `realestateapi-key.json` | REAPI property data + auction feed |
| `dealmachine-skiptrace-api-key` | DealMachine org 23501 (being cancelled) |
| `dealmachine-v2-key` | DealMachine org 40711 (primary skip-trace) |
| `stripe-secret-offramp-live` | Stripe live secret key |
| `stripe-webhook-offramp-secret-live` | Stripe live webhook signing secret |
| `stripe-offramp-ids-live.json` | Stripe product/price/coupon IDs (live) |
| `google-maps-api-key` | Google Maps geocoding |
| `google-oauth-revivebuyers-client-id` | Google SSO OAuth client ID |
| `google-oauth-revivebuyers-client-secret` | Google SSO OAuth client secret |
| `supabase-pat` | Supabase management API token (for raw SQL aggregates) |
| `offramp-seo-flush-token` | Token for flushing SEO page cache |

---

## Deployment Commands

### Restart (standard deploy)

```bash
OFFRAMP_TRIAL_LIVE=1 OFFRAMP_TRIAL_DAYS=30 pm2 restart offramp-staging --update-env
```

### Verify after restart

```bash
# HTTP 200 check
curl -s https://offramp-staging.americahomerestoration.com/ -o /dev/null -w "%{http_code}"

# App smoke test
node /home/cortextos/cortextos/orgs/AHR/sites/offramp/scripts/app_smoke.mjs

# pm2 status
pm2 list
```

### Check logs

```bash
pm2 logs offramp-staging --lines 50
```

---

## Before Every Deploy

1. `node scripts/app_smoke.mjs` — must pass
2. Bump `VERSION` in `app/sw.js` if app behavior changed
3. `git status` — confirm what you're actually deploying
4. `git push origin main` first, then restart

---

## Rollback Procedure

pm2 restart is atomic — the old process stays up until the new one binds the port. If the new process fails to start:

```bash
# Check what went wrong
pm2 logs offramp-staging --lines 100

# If the problem is in server.py, fix and restart
# If the problem is a missing secret, restore the secret file and restart

# Emergency: revert the last commit and restart
cd /home/cortextos/cortextos/orgs/AHR/sites/offramp
git revert HEAD --no-edit
OFFRAMP_TRIAL_LIVE=1 OFFRAMP_TRIAL_DAYS=30 pm2 restart offramp-staging --update-env
```

---

## Database Migrations

No migration framework. Schema changes are applied directly to Supabase via the Supabase dashboard or management API SQL endpoint.

**Before any schema change:**
- Verify the change in a query tool first
- Confirm impact on existing rows (nullable vs. NOT NULL, index changes)
- No rollback mechanism — test carefully

---

## Production Smoke Tests (post-deploy)

```bash
# 1. Server responds
curl -s https://offramprei.com/ -o /dev/null -w "%{http_code}"
# Expected: 200

# 2. API auth endpoint responds
curl -s -X POST https://offramprei.com/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"test@test.com","password":"wrong"}' | python3 -m json.tool
# Expected: 401 with {"error": "..."} (not a 500)

# 3. State counts load (used by state chips)
curl -s "https://offramprei.com/api/state-counts" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d), 'states')"
# Expected: 30+ states
```

---

## DNS Architecture

| Domain | DNS | Target |
|---|---|---|
| `offramprei.com` | Cloudflare (proxied A record) | VPS 127.0.0.1:8092 via proxy |
| `offramp-staging.americahomerestoration.com` | Cloudflare (proxied) | Same VPS process |

Both domains serve the same pm2 process. The server does not distinguish between them (no virtual hosting). `APP_ORIGIN` is set to `https://offramprei.com` for email links.

---

## iOS / App Store

GitHub Actions (`ios-testflight.yml`) builds and uploads to TestFlight automatically on every push to `main` that touches `ios-wrapper/`. Required GitHub secrets: `ASC_KEY_ID`, `ASC_ISSUER_ID`, `ASC_KEY_P8`, `APPLE_TEAM_ID`, `MATCH_PASSWORD`, `MATCH_GIT_TOKEN`.

**Apple Developer enrollment** must be completed manually (requires human approval from Apple). Individual enrollment ($99/yr) recommended to unblock TestFlight first.
