# Land-Developer Parcel Finder — Opportunity-Scoring Plan

Started 2026-07-31 (Ted). Use case: land developers want **5+ acre developable parcels along the Wasatch Front growth corridor — south Utah County (Santaquin/Payson/Spanish Fork) up through Provo/Orem, Lehi/Point-of-the-Mountain, Salt Lake County, Davis, to north Ogden (Weber)** — buyable at a discount.

**The core reframe (Ted's point):** distress tells you *who might sell cheap*; location/highest-and-best-use tells you *which parcels are actually worth buying*. A distressed 5-acre parcel in the middle of nowhere is worthless to a developer; a well-located 5-acre parcel with a motivated seller is the whole game. So we don't rank on distress — we rank on **Location value × Seller motivation**, and weight location the heavier of the two.

---

## 1. Define the universe (the buy-box filter)

Pull every parcel in the corridor that could physically be developed:
- **Acreage** ≥ 5 (gross), track buildable acres separately (net of slope/floodplain/wetland).
- **Land use / type:** vacant land, agricultural/greenbelt, OR improved parcels with a very low improvement-to-land value ratio (teardown / underutilized — the house is incidental to the dirt).
- **Not already carved up:** exclude platted subdivisions and built-out lots.
- **Geography:** the I-15 corridor counties above (Utah, Salt Lake, Davis, Weber — Utah County and north Weber are the bookends Ted named).

Sources: REAPI PropertySearch (acreage, land-use, owner, absentee flag) for the universe; **county assessor/parcel GIS** is ground truth for acreage + zoning.

## 2. Score LOCATION / Highest-and-Best-Use (the value axis — weight ~60%)

These are the "location has valuable variables" pieces Ted flagged. Ranked roughly by how much they move raw-land value:
1. **Utility access** — sewer + culinary water at or adjacent to the parcel. This is the #1 cost/value driver in Utah land: inside a sewer district = near shovel-ready premium; septic-only / no sewer = cheap to hold, expensive to develop. (County/city GIS sewer-district layers.)
2. **Zoning & entitlement status** — already zoned for density vs. needs a rezone; inside a city vs. unincorporated county; annexation potential into a growth boundary.
3. **Road frontage / access** — paved arterial frontage vs. landlocked/easement-only.
4. **Buildable ratio** — slope, FEMA floodplain, wetlands, geologic hazard → gross acres vs. net developable acres.
5. **Path-of-growth proximity** — distance to freeway interchange, existing rooftops/retail, jobs, and the leading edge of new development. (GIS distance calcs — very doable.)
6. **Comps / $-per-acre trend** — recent nearby raw-land and finished-lot sales; is the submarket appreciating?
7. **School district / amenities** for residential HBU.

## 3. Score SELLER MOTIVATION (the discount axis — weight ~40%)

Public-record signals that a seller may take a discount, strongest first:
- **Death / estate** — obituary + probate filings (estate liquidating). *We already scrape obits + probate.*
- **Tax delinquency** — behind on property tax; approaching tax-sale. (County treasurer delinquent lists — strong, underused.)
- **Foreclosure / NOD** on the parcel or the owner's other holdings. *We already have this pipeline.*
- **Bankruptcy** — Ch 7/11/13 (PACER).
- **Divorce** — family-court filings, inter-spousal quitclaims, lis pendens.
- **Lawsuits / judgments / liens** — civil suits, mechanic's/judgment/tax liens.
- **Tired landholder (motivation, not distress)** — long-tenure elderly owner, out-of-state/absentee owner, ag land facing greenbelt-rollback tax. Historically the biggest source of discounted land and easy to compute from data we already hold.

## 4. Combine → ranked opportunity list

`Opportunity = (0.6 × LocationScore) × (0.4-weighted SellerMotivationScore)` — multiplicative so a zero on either axis kills it (bad location OR no reason to sell = skip). Surface the top N per sub-market. Then enrich the winners: skip-trace the owner (Tracerfy, `property_owner:false` for household) → phone/mail → deliver as a **new "Land / Developer" pipeline** in the CRM (list + map layer), same detail-view pattern as Hit List.

---

## 5. Build plan — phased, ship v1 fast on data we already own

**Phase 1 (days, mostly existing assets):** REAPI parcel pull for the corridor (acreage ≥5 + vacant/ag) → new table `crm.land_parcels`. Layer in the distress signals we ALREADY produce — obits, probate, foreclosure, absentee/elderly/out-of-state owner. Ship a first ranked list weighted on location proxies we can get cheap (interchange/rooftop distance, acreage, absentee). This alone gives developers a usable target list.

**Phase 2 (per-county GIS):** wire county assessor/GIS layers for zoning + sewer-district + floodplain — one county at a time (Utah → Salt Lake → Davis → Weber). This is what turns "5 acres somewhere" into "5 zoned, sewer-adjacent, path-of-growth acres." Highest-leverage upgrade to the score.

**Phase 3 (court/records depth):** add tax-delinquent lists (county treasurer), then bankruptcy (PACER) and divorce/lawsuit dockets where the county court system is scrapeable. Diminishing-returns depth; add as demand justifies.

**Architecture:** same cache-once model as the OffRamp pro forma — pull the regional parcel universe into `crm.land_parcels`, refresh incrementally, serve all customers from our DB. Fixed regional cost, not per-customer API spend. Same moat.

---

## Reality checks / what's easy vs. hard
- **Have already:** acreage, owner, absentee, obits, probate, foreclosure. Phase 1 is mostly wiring existing pipes to a new table + score.
- **Medium lift:** county GIS zoning/sewer/floodplain (per-county formats), recorder liens, tax-delinquent lists.
- **Harder / patchy:** divorce & civil-lawsuit dockets (per-county court systems, many not online), precise entitlement status.
- **Blocker:** REAPI wallet is drained (402) — a corridor-wide parcel pull needs a top-up first, same as the spec-builder job.

## Open questions for Ted
1. Price/deal model for the developer customers (subscription to the list? per-lead? finder's fee / JV on closings?).
2. Residential-subdivision HBU vs. commercial/industrial vs. both — changes the location weighting.
3. How many developers, and do they want the whole corridor or specific sub-markets first (e.g., start Utah County or start Weber)?
