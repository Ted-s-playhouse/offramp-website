# OffRamp App — Build Considerations (security & production-readiness)

Running list of requirements for the OffRamp app build. Ted is feeding these in as a series (started 2026-07-31), mostly from The Faction Group LLC's "what your AI-built app got wrong" videos. Each item = a threat/gap + the requirement it drives. Framed as "security" but spans auth, accessibility, and production hardening.

---

## 1. Session management / token lifecycle
**Source:** FB video from The Faction Group LLC (facebook.com/share/v/1D76QogJuy), 2026-07-31.

**The gap:** AI-built authentication frequently ships without real session management. Sessions never expire; tokens live forever. Result: a user who logged in six months ago — or on a laptop lost/stolen at a coffee shop three months ago — is *still logged in* on that stranger's screen right now. "Your AI built authentication. It never built session management."

**Requirement for OffRamp:**
- Enforce token TTL (short-lived access tokens + refresh tokens), not infinite sessions.
- Server-side session store so sessions can be **revoked** (logout, logout-all-devices, admin kill).
- Idle/absolute session timeouts.
- Re-authentication for sensitive actions (payment, account/email change).
- On password reset, invalidate all existing sessions/tokens.

---

## 2. Accessibility (ADA / WCAG)
**Source:** FB video from The Faction Group LLC (facebook.com/share/v/1CHjL9mqNA), 2026-07-31.

**The gap:** AI-built UIs assume every user has a mouse, perfect vision, and two working hands. They skip screen-reader support, keyboard navigation, and color contrast — locking out ~1.3B people. ADA web-accessibility lawsuits topped 4,000 last year, so this is legal exposure, not just UX.

**Requirement for OffRamp:**
- Target **WCAG 2.1 AA**.
- Full keyboard navigation (no mouse-only flows); visible focus states.
- Semantic HTML + ARIA labels so screen readers work; alt text on images.
- Color-contrast ratios meeting AA; don't encode meaning in color alone.
- Accessible forms (labels tied to inputs, error messages announced).
- Run an automated a11y audit (axe/Lighthouse) in the build + a manual screen-reader pass before launch.

---

## 3. Infrastructure ceiling / scalability
**Source:** FB video from The Faction Group LLC (facebook.com/share/v/18BjKDiSVy), 2026-07-31.

**The gap:** The AI-picked "bundled stack" isn't wrong — it's the right foundation for your first ~10 customers. But it has a customer/scale ceiling. Enterprise scale isn't the next customer; it's several evolutions away. Teams get blindsided pitching capacity they can't actually serve.

**Requirement for OffRamp:**
- Document the current stack's realistic ceiling (concurrent users, data volume, throughput) — know who we can serve *today*.
- Don't over-build for enterprise scale prematurely; match infra to the near-term customer.
- Define the scaling path / next evolution triggers (when to move off the bundled stack) so growth isn't a surprise.
- Have this written down before any pitch that implies bigger capacity than we have.

---

## 4. Guiding principle — AI gives speed, not quality (AI-Directed Engineering)
**Source:** FB reel from The Faction Group LLC (facebook.com/share/r/1CttR2P1pe), 2026-07-31.

**The point (thesis of the series):** LLMs were never designed to turn a $20/mo subscription into a commercial product. They give *speed*, not *quality* — and the gap between the two is **engineering judgment**. That deliberate review layer over AI output is "AI-Directed Engineering."

**Requirement for OffRamp:**
- Treat AI-generated code as a first draft, not production. Every AI-built piece gets a human engineering-judgment review before it ships.
- The items in this doc (sessions, a11y, infra ceiling, + the rest) ARE that review layer — the checklist that closes the gap between "it runs" and "it's production-grade."
- Don't confuse "the AI shipped it fast" with "it's done."

---

## 5. Dunning / failed-payment recovery
**Source:** FB video from The Faction Group LLC (facebook.com/share/v/181JzMQA47), 2026-07-31.

**The gap:** Cards expire and charges fail constantly. AI-built subscription apps do nothing about it — no retry, no email, no notification — so the subscription silently dies and MRR bleeds out invisibly. This is "dunning," and it's usually missing entirely.

**Requirement for OffRamp** (has a monthly subscription, so this is live revenue risk):
- Automated **retry schedule** on failed charges (e.g. smart retries over several days).
- **Failed-payment email sequence** to the customer (card-expired / update-payment prompts).
- A **grace period** before access is revoked, not instant cutoff.
- Notifications/dashboard so we can *see* failing payments instead of losing them silently.
- Handle card-expiry proactively (pre-expiry reminders) where the processor supports it.

---

## 6. Data retention vs. deletion compliance
**Source:** FB video from The Faction Group LLC (facebook.com/share/v/1Bwfz1LhQW), 2026-07-31.

**The gap:** A user clicks "delete my account," the AI-built app hard-deletes everything — and that can *break federal law*. Some industries require multi-year retention (e.g. 7 years) after the relationship ends, which collides with GDPR/CCPA "right to be forgotten." AI has no idea this conflict exists.

**Requirement for OffRamp:**
- Build a **retention policy engine**: map what data must be *kept* (legal/financial/tax retention) vs. what must be *deleted* on request.
- On account deletion, honor deletion rights for what's eligible but **retain + lock** legally-required records (don't blanket-delete).
- Maintain an **audit trail** of deletion/retention actions.
- Figure out OffRamp's actual obligations (payment/financial records especially) *before* the first user asks to leave.

---

## 7. API design (security + product + contract surface)
**Source:** FB video from The Faction Group LLC (facebook.com/share/v/1DHN82kyQ8), 2026-07-31.

**The point:** Your API is simultaneously a **security** surface (attack vector), a **product** surface (what integrators build on), and a **contract** surface (what you promise not to break). AI-built APIs over-expose data and leak enumeration risk.

**Requirement for OffRamp** — run a full API security + design audit; per endpoint:
- List every returned field; **flag over-exposed data** (return only what's needed — data minimization; don't leak internal fields/PII).
- **Identify sequential ID usage** → switch to non-guessable IDs (UUIDs) to kill enumeration / IDOR.
- **Check for missing auth** on every endpoint (authn + authz, not just "is logged in").
- Implement **API versioning** with a `/v1/` prefix so future changes don't break clients.
- Create an **API changelog** and a public **documentation page**.

**Orchestration prompt (verbatim from the video, to hand our AI):** "Perform a complete API security and design audit. For each endpoint: list returned fields, flag over-exposed data, identify sequential ID usage, check for missing auth. Implement API versioning with /v1/ prefix. Create an API changelog and public documentation page."

---

## 8. Serverless timeout / heavy-workload routing
**Source:** FB video from The Faction Group LLC (facebook.com/share/v/1H33Y5tH2J), 2026-07-31.

**The gap:** AI picks a serverless function for everything, but serverless has a hard execution ceiling (~60s timeout). Some jobs run for minutes (report generation, bulk imports, media processing, skip-trace batches). The function times out mid-job and the work silently fails. AI never accounts for which workloads exceed the serverless limit.

**Requirement for OffRamp:**
- **Inventory the workloads** — identify every job that could run longer than the serverless timeout (~60s): report builds, bulk data pulls/imports, PDF/media generation, email batches, any external-API fan-out.
- **Containerize the heavy/long-running jobs** — move them off the serverless function into a container or worker that has no short timeout.
- **Route by job type** — add a lane (queue/worker) for heavy work; keep serverless for the fast request/response path. This is *adding a lane, not a full migration* — don't rip out the whole stack.
- Ties to #3 (infra ceiling): know which jobs will blow the serverless limit *before* they fail in production.

_("Full fix action prompt" for this one lives in The Faction Group's free community, per the video.)_

---

## 9. Serverless vs. containers — cost/FinOps at scale
**Source:** FB video from The Faction Group LLC (facebook.com/share/v/1DDVeapPbX), 2026-07-31.

**The gap:** Serverless cost is predictable and cheap at ~10 users, but the bill *climbs* as you scale to ~1,000 (you pay per-invocation, and invocations explode). Containers cost less per unit at volume but cost you *operationally* (you run/patch/scale them yourself). AI defaults to all-serverless and never revisits the economics as usage grows. This is the FinOps companion to #8 (timeouts) and #3 (infra ceiling).

**Requirement for OffRamp:**
- **Track cost-per-user / cost-per-invocation** as usage grows — don't let the serverless bill climb invisibly (ties to the dunning/MRR visibility theme in #5, but for *our* cloud spend).
- **Plan the hybrid** — most production systems run *both*: serverless for spiky/low-volume paths, containers for steady high-volume or heavy workloads. Decide per-workload, driven by *our actual business reality* (user count, request volume, margin), not a blanket choice.
- Define the **trigger point** — at what user/volume level does a given path flip from serverless-cheaper to container-cheaper. Write it down alongside the #3 scaling triggers.
- Don't prematurely containerize everything (operational tax) — but don't stay all-serverless past the crossover either.

---

_(more considerations to be appended as Ted sends them)_

---

### Note — non-consideration items in the series
- **facebook.com/share/r/1JTcY3oQ36 (2026-07-31):** The Faction Group promo/credibility reel — "one of our builders shipped a fleet-management dashboard (130 vehicles, route optimization, fuel monitoring), orchestrated across all 13 layers, real production software." Not a build gap → no OffRamp requirement. Captured only as context: their framework brands itself as **13-layer "AI-Directed Engineering"** (the review-layer thesis from #4). If Ted wants, we can try to reconstruct what those 13 layers are as a checklist scaffold — this SECURITY-CONSIDERATIONS doc is effectively building that layer stack from their videos.
