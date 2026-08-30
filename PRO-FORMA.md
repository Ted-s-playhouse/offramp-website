# OffRamp — Launch Pro Forma & Cost-to-Serve Model

Started 2026-07-31 (Ted). Goal: model hard costs of bringing the OffRamp data product to market and maintaining it as paying subscribers scale (10 → 100 → 1,000 → 10,000 active users), then find break-even vs. price.

**Bottom line up front:** the single variable that decides whether this business works is **cost per active user's DATA consumption**, and the lever that controls it is a **shared data cache** (pull the regional universe once, serve all subscribers from our DB) vs. **naive pass-through** (every user's clicks hit REAPI/Tracerfy live). Cache = operating leverage + the moat. Pass-through = frozen margin + data spend that balloons linearly forever.

> ⚠️ Numbers below marked **[ASSUMPTION]** need Ted to confirm: (1) subscription price point, (2) real REAPI per-record + Tracerfy per-lookup rates, (3) expected data pulls per active user/month. Everything recomputes off those three.

---

## 1. Cost structure

### Fixed infra (steps up with scale, not per-user)
| Item | ≤10 users | 100 | 1,000 | 10,000 |
|---|---|---|---|---|
| VPS (cortextos-prod-01, 8GB) | $60 | $60 | $200 (bigger box) | $1,500 (cluster) |
| Supabase (DB) | $25 | $60 | $600 (Team+compute) | $6,000 (dedicated) |
| Vercel (hosting/bandwidth) | $20 | $40 | $300 | $3,000 |
| Cloudflare + domains | $20 | $20 | $50 | $200 |
| Workspace/email/misc | $25 | $40 | $150 | $800 |
| **Fixed subtotal** | **~$150** | **~$260** | **~$1,300** | **~$11,500** |

Infra is cheap and near-flat to ~1,000 users. Around 1,000+ it crosses the serverless→container threshold (see SECURITY-CONSIDERATIONS #8/#9): heavy jobs move to a worker lane + a caching layer, which is the step-up above.

### Variable data COGS — the real driver
Per-unit **[ASSUMPTION]**: REAPI record ≈ $0.07; Tracerfy skip-trace ≈ $0.12.
Per active user/month **[ASSUMPTION]**: ~300 property pulls + ~100 skip-traces.
→ Naive data cost ≈ **~$33/active user/month** (300×$0.07 + 100×$0.12). Range $25–60 depending on how hard a user works the data.

---

## 2. Two architectures — the whole ballgame

### A) NAIVE pass-through (every user click hits the paid APIs live)
| Users | Infra | Data (N × $33) | **Total/mo** | Cost/user |
|---|---|---|---|---|
| 10 | $150 | $330 | **$480** | $48 |
| 100 | $260 | $3,300 | **$3,560** | $36 |
| 1,000 | $1,300 | $33,000 | **$34,300** | $34 |
| 10,000 | $11,500 | $330,000 | **$341,500** | $34 |

Cost/user never improves (~$34). Margin is frozen and absolute data spend explodes. One power user can 10x their own COGS. **This model does not scale into a real business.**

### B) SHARED CACHE (pull the regional universe once, refresh incrementally, serve all from our DB)
Data cost becomes ~**fixed regional refresh** (new NODs, sale-status updates, re-skiptrace of actives across our 6-state footprint), independent of user count. Per-user marginal drops to ≈ infra/bandwidth only (~$2).
| Users | Infra | Data refresh (fixed) [ASSUMPTION] | Marginal (N × $2) | **Total/mo** | Cost/user |
|---|---|---|---|---|---|
| 10 | $150 | $1,800 | $20 | **$1,970** | $197 |
| 100 | $260 | $1,800 | $200 | **$2,260** | $23 |
| 1,000 | $1,300 | $2,500 | $2,000 | **$5,800** | $5.80 |
| 10,000 | $11,500 | $4,000 | $20,000 | **$35,500** | $3.55 |

Cost/user **collapses** with scale ($197 → $3.55) — classic SaaS operating leverage. Below ~90 users the fixed refresh makes it pricey per head; above that it crushes the naive model and keeps improving.

**The cache (crm.reapi_properties, already started) is both the margin unlock AND the proprietary-data moat we discussed.** Same asset, two payoffs.

---

## 3. Break-even (cached model)

At the ~100-user stage: fixed ≈ $2,100/mo (infra + refresh), marginal ≈ $2/user.

**Real price anchors (Ted, 2026-07-31): two annualized base tiers — $97/mo and $297/mo.** Consumption upgrades (§6) stack on top; the base is the recurring floor.

| Base price/mo | Contribution/user | **Break-even subs** |
|---|---|---|
| $97 | $95 | ~22 |
| $297 | $295 | ~8 |

Both are trivially reachable; every sub past break-even is ~98% margin (cached).

### ARR — the investor-value engine
The annualized subscription is what an investor underwrites (predictable recurring revenue trades at a multiple; usage/consumption revenue is lumpy and valued lower per dollar). Blended ~$197 avg across the two tiers:

| Subs | ARR (blended ~$197) | All-$97 floor | All-$297 |
|---|---|---|---|
| 100 | ~$236K | ~$116K | ~$356K |
| 1,000 | ~$2.36M | ~$1.16M | ~$3.56M |
| 10,000 | ~$23.6M | ~$11.6M | ~$35.6M |

At a vertical-SaaS multiple with a real data moat, **1,000 subs (~$2.4M ARR blended) ≈ a $10–19M enterprise value BEFORE consumption margin** — that's the number the investor underwrites. Skip-trace/graph revenue (§6) is upside on top.

**Two annual-billing multipliers:**
1. **Cash upfront** — a $97/mo sub billed annually is $1,164 in the door on day one; that cash literally funds building the data cache/moat. Self-funding growth.
2. **Churn crusher** — annual contracts suppress churn, the single biggest driver of the ARR multiple.

(Naive model at $97: contribution ~$64, break-even ~3 subs — but no operating leverage, and at 1,000 users you're bleeding $33k/mo in raw data with no moat and no fixed-COGS story to sell.)

**Read:** with the cache, break-even is 8–22 paying subscribers. Reachable in the near term; everything past it compounds into the ARR the valuation rides on.

---

## 4. Non-negotiables the model implies
1. **Build/finish the shared cache** — converts linear per-user data cost into fixed regional cost. Highest-leverage engineering on the roadmap.
2. **Meter per-user pulls** (credits/quota) so a power user can't force live API calls that blow the unit economics — even with a cache, cap cold-miss live lookups.
3. **Plan the serverless→container step at ~1,000 users** (SECURITY-CONSIDERATIONS #8/#9) — that's where infra stops being trivial.
4. **Instrument data cost per user** from day one so we SEE margin erosion before it hurts (ties to dunning/visibility, #5).

---

---

## 5. Cost-efficiency-at-scale plan — the data-sourcing ladder

Ted's instinct is correct: **per-call APIs (REAPI, Tracerfy) are retail pricing.** They're the cheapest way to start and the most expensive way to run at volume. As query volume grows, you climb the supply chain — from metered calls → to a **flat bulk data license** → to **direct-from-source county feeds**. Each rung turns more of COGS from *variable* (scales with usage) into *fixed* (scales with footprint, not users). This is the same cache logic taken to its endgame: instead of caching individual API responses, you license the whole regional dataset once and run it yourself.

### The ladder (tie each rung to a scale trigger)
| Stage | Users | Property data | Skip-trace | Why |
|---|---|---|---|---|
| **1. Metered + cache** | 0–~100 | REAPI per-record into `crm.reapi_properties` | Tracerfy per-lookup | Lowest total cost while volume is low; no minimums |
| **2. Volume-rate + heavy cache** | ~100–1,000 | Negotiate REAPI volume tier; cache serves most reads; evaluate a **bulk parcel license (Regrid)** for the land vertical | Enterprise Tracerfy rate | Cache absorbs growth; start pricing bulk before you need it |
| **3. Bulk license (the crossover)** | 1,000–10,000 | **Replace metered property data with a flat regional bulk license — ATTOM / CoreLogic (Cotality) / Black Knight — + direct county recorder/assessor feeds** | Enterprise skip-trace contract + a 2nd vendor for redundancy/leverage | COGS goes from variable to fixed; unit cost collapses; strengthens the moat + investor margin story |

### The crossover math (the trigger to instrument now)
You switch to a bulk license the month that **regional refresh cost (metered) > flat license cost**. Illustratively: if the cached refresh is climbing toward $30k+/mo at the 1,000–10,000 range and an ATTOM/CoreLogic regional bulk license lands at a flat $X/mo, you flip the moment X < refresh. Instrument cost-per-region-refresh from day one so we *see* the crossover coming (ties to non-negotiable #4).

### What it costs us to climb (the technology side)
Bulk data isn't free leverage — it moves work from "their API" to "our pipeline":
- An **ETL/normalization worker lane** (ingest ATTOM/Regrid + county bulk files → normalize → our schema). This is exactly the serverless→container worker lane already flagged in SECURITY-CONSIDERATIONS #8/#9 — the pro forma's ~1,000-user infra step-up IS this build.
- A **data-freshness SLA** (how stale can a parcel be before a refresh) and batched skip-trace.
- Vendor redundancy so a single provider can't hold pricing or uptime over us.

### Business-plan read
The flat-license endgame is what makes this a *business*, not a data-reseller with frozen margin: fixed COGS + subscription revenue = real SaaS operating leverage, and "we license + normalize the region ourselves" is a far stronger moat and investor story than "we resell REAPI calls."

**Caveat:** ATTOM / CoreLogic / Black Knight bulk-license pricing is a sales-call number, not published — the $X above is a placeholder. Next step to make this real: get quotes from ATTOM + Regrid (parcels) + one skip-trace enterprise rate, then drop them into the crossover math to find the exact user count where we flip.

---

---

## 6. Monetization model — subscription floor + consumption upgrades (where the money actually is)

Ted's model (2026-07-31): the **monthly subscription is the floor, not the profit.** The real margin is in **metered upgrades** — skip-trace, premium filters/segments, mailing, and a visual layer. This is the right structure, and it does something important: **it makes price track COGS.** The expensive per-unit items (skip-trace, cold-miss live pulls, direct mail) become *revenue lines the user pays for per use*, so a power user stops being a margin leak (the naive-model risk in §2) and becomes your highest-revenue customer. The metering we already listed as cost-control non-negotiable #2 IS the billing engine — same instrument, two jobs.

### Base subscription — tiers (access + retention hook), annualized
- **Basic — $97/mo:** access to the cached universe + core distress filters (pre-foreclosure, pre-equity/high-equity, core segments) + a basic trace-credit allotment.
- **Pro — $297/mo:** full data + expanded/graph skip-trace access + premium filters/segments, mailing tools, priority freshness, larger included allotment.
- **Usage overages:** extra data usage (skip-trace, cold-miss pulls, exports) metered on top of *either* tier once the included allotment is spent.
- **Team / multi-user (seat-based):** an admin sets up a team and adds seats. Per-seat pricing on top of a team base. This is the ACV + NRR engine (see below).

Base sub buys *access + a monthly credit allotment*; it's the recurring floor and the churn anchor. Billed annually → cash upfront + churn suppression (§3).

**Market validation:** Ted personally spends **$400+/mo on DealMachine** — he's the target customer, so that's demonstrated willingness-to-pay. The $297 Pro tier + overages lands *under* a known incumbent's number while offering more (cache, graph/network trace, land vertical, extra data services). The price ladder isn't aggressive — it's a discount to what pros already pay.

### Team / multi-user — the expansion lever (highest-value, both revenue & valuation)
- **Net Revenue Retention >100%.** Seats expand within an account over time (a team grows 3→10 seats) → ARR grows with *zero* new-logo acquisition cost. NRR-driven growth earns the highest SaaS multiples.
- **Higher ACV.** Acquire one admin, they onboard the whole team. A 10-seat team at ~$97–197/seat = $1–2K/mo from a single account — dwarfs a solo sub.
- **Lower CAC + viral.** The admin becomes distribution; they add users to "expand reach and sales capacity" — the product sells itself internally.
- **Stickier = higher multiple.** Shared lead pools, assigned leads, team workflows = high switching cost = low churn.
- **Product implication:** seats need roles/permissions, shared vs. personal lead pools, and per-user activity tracking — the **team-access scope model** (already started in `ahr-crm-mockup/TEAM_ACCESS_SCOPE_MODEL.md`). The team tier is a real feature layer, not just a price change — it's what turns this from a tool into a platform.

### Consumption upgrades — the margin engine (metered, marked up over our COGS)
| Add-on | Our cost (est.) | Sell (illustrative) | Margin |
|---|---|---|---|
| Skip-trace — Basic (1 best contact) | ~$0.12 (Tracerfy) | $0.50–1.00 | 4–8× |
| Skip-trace — **3×3×3 deep-trace** (household graph: 3 phones, 3 emails, 3 relatives/associates) | ~$0.12–0.36 | premium multiple of basic | very high — marginal cost barely moves, value/price jumps |
| Skip-trace — **Network/Farm trace** (owner + associates + neighbors + THEIR associates) | ~10–30× lookups (highest COGS) | top-tier per-credit; margin COMPOUNDS | highest-margin SKU; MUST be metered — a flat plan would get bled dry |

**Skip-trace ladder:** Basic (1 contact) → 3×3×3 (household graph) → Network/Farm (owner + associates + neighbors + their associates). Price and margin climb at each step. The Network/Farm tier doubles as a **lead-gen engine** — neighbor-farming ("I'm buying in your neighborhood") refills the top of the funnel, so it's both a data product and demand generation.

**Compliance gate (build from day one):** tracing non-sellers (associates, neighbors) picks up DNC/TCPA + data-privacy exposure. Tracerfy already returns line-type + DNC status per contact — so mark DNC, separate landline/mobile, and the app must warn before dialing a flagged number. Keeps the premium trace from becoming a liability.
| Premium filter / segment unlock or export | cache read (~$0) | per-unlock or per-export credit | very high |
| Direct-mail / mailing service | postage + print | per-piece markup | print-shop margin |
| Cold-miss live property pull (beyond allotment) | ~$0.07 (REAPI) | per-credit | markup + protects unit economics |
| **Visual distressor** (Street View / Maps imagery on a property) | Google Maps API per-call | tier perk or per-property credit | see caveat |

### Why this is the winning structure
- **Price follows cost.** Every expensive action is a paid action → margin can't invert no matter how hard a user works the data. Directly neutralizes the "one power user 10×'s their COGS" failure mode.
- **Low base = low friction to land** subscribers; margin expands with engagement instead of being capped by a flat fee.
- **The moat stays the cache; the money is the services layered on top of it** — consistent with the "sort/filter is table-stakes, not the moat" read.

### Caveats to design around
- **Visual distressor via Google Maps/Street View:** Google's Static Street View API is per-call *and* their ToS restricts caching/storing the imagery long-term — you generally fetch live per view, not pre-cache tiles. So it's a metered/perk feature with its own COGS, not a free cached asset. Budget it as a variable cost like skip-trace.
- **Included-credit accounting:** each tier should bundle a credit allotment so the base sub feels generous, then overage is pure-margin consumption. Instrument credits-consumed-per-user from day one (ties to §4 + non-negotiable #4).

### Open pricing questions for Ted
1. Base price per tier (Basic $97 / Pro $297) — confirm included-credit allotment in each.
2. Credit pricing: $/skip-trace (Basic/3×3×3/Network), $/mail piece, $/premium-filter unlock, $/extra live pull.
3. Visual distressor — free tier perk vs. metered credit.
4. Team plan: per-seat price + team base.

---

## 7. Go-to-market — guru/affiliate channel (where the scalable growth comes from)

Ted's GTM (2026-07-31): **target REI trainers, coaching groups, and gurus; get their teams/students onto the platform via an affiliate model.** This is the proven playbook in this exact niche — PropStream, BatchLeads, DealMachine, and Privy all grew primarily through coach/influencer partnerships + affiliates, not paid ads.

### Why it's the highest-leverage channel
- **Pre-qualified captive audiences.** Gurus' students already paid to learn wholesaling/flipping and now *need* a data tool to execute — warm, high-intent demand at scale.
- **Near-zero CAC, performance-based.** Affiliate rev-share pays only on conversions; the guru's list becomes our distribution instead of ad spend.
- **Compounds with the team tier (§6).** A guru = a group/team account; students = seats. One guru deal = dozens-to-hundreds of seats, not one sub. Affiliate + multi-user stack.

### Two deal structures
1. **Recurring rev-share** — e.g. 20–30% for the life of the customer (or first 12 mo). Recurring share keeps the guru pushing *renewals*, not just signups.
2. **Co-branded reseller** — the guru is the account admin at a wholesale seat rate and marks it up to their students. Stronger: gives them skin in the game and ownership of the relationship, so they defend the channel.

### The strategic kicker — distribution IS the moat
The feature set isn't defensible (a competitor can clone the sort/filters in a sprint — see the "table-stakes" read). **The distribution is.** Lock up the major REI coaches first and a copycat can build the same product but can't easily pry you out of the channel. Owning the guru relationships is the real barrier to entry — it answers the "anyone could build this" concern directly. (Moat = proprietary cache **+** locked guru channels.)

### Affiliate-tailored strategy tagging (Ted, 2026-07-31) — the stickiness + moat layer
Each guru teaches a specific acquisition **strategy**; the app should **tag every lead with the strategies it fits and give each affiliate's group a view keyed to their model.** Example: Pace Morby's Sub2 (Subject-To) community sees leads pre-tagged "Sub2 opportunity."

- **How it works with data we already have:** `hit_list` stores the signature — equity %, mortgage balance, loan age + type, foreclosure status, occupancy, probate/deceased. A tagging engine maps that signature → the strategies each lead qualifies for. Same universe, guru-specific lens.
- **Strategy → signature examples:**
  - **Sub2 / Subject-To (Pace Morby):** existing (ideally low-rate) loan + motivated/behind seller who wants OUT of the payment; typically lower-to-mid equity. **Qualifier calc (Ted, 2026-07-31):** est. market rent vs. the underlying loan's PITI (built from its amortization table: balance + rate + remaining term → P&I, + taxes + insurance). Rent − PITI = monthly cash flow; positive ⇒ Sub2-viable, rank by the cushion. Inputs we HAVE: loan balance, loan start (`mortgage_recording_date` → amortization schedule), taxes (assessor). Inputs to SOURCE: (1) market rent via a rental AVM (REAPI/ATTOM/RentCast); (2) interest rate (recorded doc / REAPI, else estimate from origination-vintage average rate). The same rent-vs-debt engine powers other strategy tags (seller-finance viability, BRRRR/DSCR rent tests) — one calc, many strategies. Also a premium enrichment in its own right ("we did the Sub2 math for you, ranked by cash flow").
  - **Sub2 + seller carryback / seller-finance:** high-equity *and* motivated → take over the loan AND seller carries the equity gap (this is where a high-equity motivated seller belongs — not classic Sub2).
  - **Wholesale / assignment:** deep discount + fast-close motivation.
  - **Fix-flip / wholetail:** high equity + condition/distress + resale spread.
- **Why it's strategic, not cosmetic:** the app feels *custom-built* for each guru's curriculum — their training maps 1:1 to filter/tag presets, so the tool literally executes what they teach → massive retention and the strongest co-branded-reseller hook. And it's **copy-resistant**: a competitor can clone a filter, not the strategy-fit enrichment tied to your guru relationships. Deepens the distribution moat above.

### Risk to structure around
Channel concentration + guru-audience churn (students who quit REI). Mitigate with **annual billing** (churn suppression, §3) and **spreading across many gurus** so no single affiliate dominates revenue — otherwise a few big affiliates become a dependency an investor will flag.

---

## 8. Platform architecture — one chassis, many verticals (the biggest idea)

Ted's platform thesis (2026-07-31): the same **core system** serves multiple investor verticals. The core — **CRM + data cache + strategy-tagging engine + subscription/consumption billing + team seats + affiliate engine** — is a **reusable chassis.** Each investor type is a **swappable data module + strategy pack** bolted on top.

| Vertical | Data layer / GIS (what changes) | Primary distress/motivation signal | Strategy tags |
|---|---|---|---|
| **Residential REI** (current build) | REAPI/ATTOM property + mortgage, foreclosure, rental AVM, skip-trace | Foreclosure / NOD, probate, equity + motivation | Sub2, seller-finance, wholesale, flip, BRRRR |
| **Commercial investor** | Different data + GIS — zoning/land-use, NOI/cap rate, lease/tenant rolls, CoStar/Crexi-type | **Commercial loan maturities** (maturing CRE debt in a high-rate market = the distress signal) | value-add, distressed-note, maturity-default |
| **Land developer** (scoped in `LAND-DEVELOPER-VERTICAL.md`) | Parcels/acreage, entitlement status, utility/sewer GIS, growth boundaries | Death/probate/tax-delinquency + tired landholder | developable-at-discount, path-of-growth, assemblage |

**What changes per vertical:** the data layer/GIS + the strategy tags. **What stays:** everything else — CRM, cache infra, tagging framework, billing, team seats, affiliate engine.

### Why this is the real prize
- **TAM ×3+** — three markets instead of one, each with its **own guru/affiliate ecosystem** to run the same GTM (§7) against.
- **Low marginal cost per vertical** (reuse the chassis) → the highest-margin kind of expansion.
- **Platform valuation, not point-tool valuation** — this is the "**CoStar / Bloomberg for distressed acquisition across asset classes**" story, a different multiple entirely.

### Sequencing discipline (non-negotiable)
Nail **residential first** — to profitability + a lighthouse guru (Pace) — and prove the playbook end to end. THEN clone the chassis to land, then commercial. Not all three at once, or none reaches escape velocity. The payoff: building residential *right* is exactly what forges the reusable chassis, so verticals 2 and 3 come faster and cheaper.

---

_Recompute this whole model once Ted confirms: real REAPI/Tracerfy rates, pulls-per-active-user, credit/overage pricing, seat price, and affiliate rev-share %._
