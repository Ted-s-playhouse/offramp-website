# Roadmap

*Source: MASTER-TASK-FILE.md + Ted's Telegram direction through 2026-10-03. Last updated: 2026-10-04.*

---

## P0 — Launch Blockers

| Item | Status | Notes |
|---|---|---|
| Go-live approval | **Awaiting Ted** | Tonight vs Sun 10/5 11 AM MT |

No technical P0 blockers remain. UAT is at 713/715.

---

## P1 — Launch Requirements (complete or near-complete)

All Phase 1-5 items from MASTER-TASK-FILE.md that were required for launch are DONE as of 2026-10-03. Key items:

- ✅ Deal feed (REAPI auction-only, national)
- ✅ Equity display (AVM minus all liens, unverified handling)
- ✅ Auth (signup, login, Google SSO, password reset)
- ✅ Stripe billing (Free/Pro/Premium/Trial tiers, live mode)
- ✅ Skip-trace (DM 40711 → Tracerfy → REAPI waterfall)
- ✅ Deal analysis calculator
- ✅ CSV export (Pro/Premium)
- ✅ $5 document pull
- ✅ Mobile nav (pinned search, bottom-sheet state picker, filter sheet)
- ✅ Service worker / PWA install
- ✅ Security headers
- ✅ Terms acceptance gate
- ✅ iOS TestFlight CI (GitHub Actions)
- ✅ SEO deal pages
- ✅ CourtListener court records on deal detail

---

## P2 — Post-Launch (approved, sequenced)

### iOS Distribution
- [ ] Apple Developer enrollment (requires human, Mon PM per Ted)
  - Individual enrollment ($99/yr) to unblock TestFlight
  - D-U-N-S for LLC in parallel (up to 30 days)
- [ ] Apple Small Business Program enrollment (15% App Store commission)
- [ ] Play Console setup for Android (iris@revivebuyers.com) — INFERRED low priority

### Desktop Deal Room
- [ ] Two-column layout at ≥900px (list + detail side by side)
  - Requires mockup → Ted sign-off → build (LOCKED WIREFRAME SCOPE rule)
  - Ted mentioned sending a project plan for this

### Data Sources
- [ ] DataTree sign-up for $5 JIT recorded document source (Ted must complete checkout)
- [ ] MLS-active flag source for lead type chips
- [ ] Liens & judgments Premium row (REAPI $0.75/match on tap)

### Security
- [ ] OAuth denylist (block known-bad redirect URIs)
- [ ] Flip CSP from REPORT_ONLY to enforced (after monitoring period quiet)
- [ ] Server-side session revocation (acknowledged gap — single-secret cookie model)

### Billing
- [ ] Dunning / failed-payment recovery (retry schedule, email sequence)
- [ ] Founding promo code retirement
- [ ] Founding seat cap enforcement

---

## P3 — Future / Experimental (not yet approved)

- [ ] Postcards feature (PostGrid, "Send postcard" on card) — awaiting Ted GO
- [ ] Postcard + email allowance per plan tier
- [ ] Ramp Bid / Gavel Odds / Reversion Meter / Surplus Chaser metrics (PARKED — calibrated but not for launch per Ted 2026-10-03)
- [ ] Android app (after revenue justifies)
- [ ] Team tier ($199/mo, seats)
- [ ] Scout integration (tenant-specific deal routing)
- [ ] OffRamp as workspace for CRM (PARKED post-launch per Ted)

---

## Backlog Items (awaiting Ted GO)

- 76 deceased flags review
- Rufus email (Buckwalter conflict)
- Salem 42 E Center St sale date
- Quo kill/keep decision
- Gusto / QBO export
- BatchLeads purge (deadline 2026-10-07)
