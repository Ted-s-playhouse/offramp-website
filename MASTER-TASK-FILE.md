# Off-Ramp REI — Master Task File


## Phase 1: SEO Foundation (this week)
1. Auction.com scrape — run one more scrape using the existing pipeline. Save the raw file as auction_YYYYMMDD.jsonl with a timestamp. Expand the GraphQL query to explicitly request opening bid, foreclosing attorney name, attorney firm, and attorney contact fields if the API exposes them. Compare the new file's fields against the September 27 file field-by-field and report any new fields that appeared. Specifically check for venue name, venue address, registration time, auction time, opening bid, and foreclosing attorney. If a field is absent from the response, log it as absent — never invent it. Report the total row count and whether it matches the stored table. Verify: row count matches the stored table, and the field comparison report is committed to the repo.
2. County normalization — build FIPS table, dedupe county lists, fix san-bernadino slug, reject log for unmatched values. Verify: fetch a state page, count duplicates.
3. All-50 state list on homepage with listing counts, including empty states.
4. Deal-room link back to public SEO pages.
5. Search Console submission — human task, skip it.


## Phase 2: Monetization (week two)
6. Stripe subscription wiring: free tier 25 lookups, Pro $49/mo 200 credits, premium $99/mo with next-of-kin and PACER tracking. Overage $0.35/credit.
7. Pro gate on the card, not bottom of scroll.
8. One-tap call button at top of owner contact section.
9. Demo hint moved to landing page, removed from product.
10. Real search — the box must search owner, street, city, zip, not just city filter.
11. Save, hide, contacted, note actions so leads stick between logins.


## Phase 3: Data Moat (weeks three to four)
12. Daily diff SEO cron — ids-only search at zero credits, PropertyDetailBulk for new/changed IDs only, regenerate affected pages, dynamic sitemap ping to Search Console. Verify: credit usage log shows near-zero on repeat runs.
13. Trustee-notice pipeline — separate table, Utah/Idaho/Montana first, venue name, address, registration time, auction time, deposit, cash-only flag, cancellation status. Scrapers blocked until a real notice URL is stored.
14. Bid pages — national overview plus state pages with cited statutes. Idaho pulled, Utah pending. No swapped-variable templates.
15. Estimated credit bid in deal room — static math from stored loan balance, labeled Est., no live REAPI call.


## Phase 4: iOS App (month two)
16. PWA first — manifest and service worker so users can install from browser.
17. Native iOS wrapper via Capacitor for push alerts and App Store presence.
18. Apple Small Business Program enrollment for 15% commission.
19. External link option on upgrade screen for web checkout.
20. Android skipped until revenue justifies it.


## Timeline
- Phase 1: complete by end of day today
- Phase 2: complete by end of day tomorrow
- Phase 3: complete by end of day day three
- Phase 4: complete by end of day day seven


Rules: if a phase is not done by its deadline, report it as overdue in the heartbeat and move to the next unlocked item. Do not extend deadlines. Do not skip ahead to a later phase while an earlier one is incomplete.


## Standing Rules
- Commit before starting and after each completed task with a descriptive message.
- Never invent data — hide absent fields, never show placeholders.
- Public pages make zero live API calls.
- Paid API calls only on user click, cached after.
- Verification check required before marking any task done.
---
Source: Ted via Telegram + Google Doc "Task" (Drive id 1ETnFhRjEfCZWxnFB8hEE8ef32p3rx1AQWjbF8G79xR0), received 2026-10-03 08:46 UTC. Day 1 = 2026-10-03 (America/Denver).

## Addendum 2026-10-03 (Ted, Telegram) — Deal-room list view for national scale
1. Search box is the primary entry point. State chips move below search as a secondary "browse by state" section (or dropdown); they remain a quick-tap shortcut, not the main navigation.
2. Empty state: a state with no deals shows "no auctions in <state> right now — see nearby states" with links to adjacent states that have listings. Never a blank list.
3. Header credit meter reads "N credits left" or the plan name, not a raw regional number.
4. Bottom nav "Lookup" tab renamed "Search".
Rule: commit before starting and after each completed task.

## Addendum 2 — 2026-10-03 (Ted, Telegram) — Free-user gray-out layer on deal detail
Model on the Revive CRM lead layout, not a new screen. Order: owner + address, beds/baths/sqft, Contact (phone, email, next of kin), Evaluation (equity %, equity $, AVM, loan balance), Engage.
Free: address, photo, beds/baths, status, sale date clear; phone, email, next of kin, equity $, loan balance blurred + "Pro" label; tap opens the upgrade sheet; never "no phones on file" for locked data.
Pro: same layout, values clear, skip trace runs on the phone row; if not run, the CRM empty state. Do not gray the whole card. Do not invent phones.


## Phase 5 — 2026-10-03 (Ted, Telegram): Data quality, UI/UX, SEO re-audit, data sources, iOS
Source: Ted 2026-10-03 10:30–10:45Z Telegram ("develop the next phase: the UI/UX. Go back through SEO and make sure that's optimized as per prompt. We need to develop a new master task list. ... iOS Apple Developer account"). Lead types named: deceased owner pre-probate, actively listed on the MLS, absentee owner, next of kin. Day 1 = 2026-10-03.

### 5A. Data quality (city/state QA "all the way down")
21. DONE 10/3 — split city out of street on 48,337 foreclosure.com rows; scrapers fixed at source; county "County County" suffix stripped (2,156 rows); 4,654 duplicate rows retired with archived_reason pointing at the surviving row.
22. REAPI basic append of every active row lacking an owner (running; 96% match). Then backfill apn + county + normalized city from the cached REAPI payloads (crm.reapi_properties) — fills the 1,242 rows the zip split could not resolve. Verify: active rows with property_city NULL < 100.
23. APN + county as the enforced duplicate key: dedupe on (apn, county) after #22, then a UNIQUE partial index; fc_ingest / foreclosure_com / auction_load consult it before insert. Verify: zero active pairs sharing apn+county.
24. State/zip audit: every active row's state must equal its zip's USPS state (1 mismatch known); fix or reject-log. Public SEO pages and deal cards print city/state from the same columns — spot-check 20 random listing pages after #22.

### 5B. UI/UX (deal room)
25. DONE 10/3 — mobile nav: pinned search toolbar + state chip + Filter, bottom-sheet state picker, filter sheet, safe-area nav.
26. Lead-type chips + filters: Deceased pre-probate, MLS-active, Absentee owner, Next of kin on file, Pre-foreclosure, Auction. Server exposes lead_types[] per row from existing columns (deceased_flag + no probate case; mls_active; absentee_tier; next_of_kin). Verify: each chip filters and the count line updates.
27. Detail page: Contact → Evaluation → Engage order stays; add "Liens & judgments" row (Premium) fed by #31; show skip-trace source + date; "You were not charged" states on vendor errors (done).
28. Desktop pass of the same toolbar/sheets at ≥900px (two-column: list + detail) — mockup first, Ted sign-off, then build.

### 5C. SEO re-audit against the Phase 1 prompt
29. DONE 10/3 — Street View front photo on every listing page + lazy thumbs on county lists (cached once per address, bounded to real listings).
30. Audit each Phase 1 item on the live site: all-50-state list w/ counts (incl. empty states), county slugs normalized (reject log), listing pages carry schema.org RealEstateListing/Place JSON-LD, title/description patterns, canonical, internal links state→county→listing→deal room and back, sitemap freshness + Search Console coverage (API), Core Web Vitals (image dimensions set, no layout shift). Output: one checklist page with pass/fail per item and the fix commits.

### 5D. Data sources / cost (research running 10/3)
31. Vendor cost comparison page (bankruptcy Ch7/13 feeds; liens, judgments, lis pendens, tax liens; skip-trace per match) + the aggregate "where each API call moves" recommendation. Then wire the winners: bankruptcy feed → bankruptcy_* columns; involuntary liens (REAPI add-on $0.75/match or cheaper winner) → Premium row; MLS-active flag source for #26.
32. Skip trace stays JIT on a paid tap: DealMachine credits first (org 40711 pool), REAPI $0.05/match as overflow, result cached on the row (done 10/3).

### 5E. iOS
33. Apple Developer enrollment (human). Two routes: (a) Individual under Ted — $99/yr, no entity paperwork, approved in ~1-2 days, app can be transferred to an org account later; (b) Organization under the LLC — needs a D-U-N-S number for the legal entity (free from Dun & Bradstreet via Apple's D-U-N-S lookup, up to 30 days), legal-entity verification, and Ted as the account holder. Recommendation: enroll Individual now to unblock TestFlight, start the D-U-N-S request for the LLC in parallel. Then items 17/18 resume (Xcode on the Mac, Capacitor build, Small Business Program).

### 5F. Added 2026-10-03 11:05Z (Ted, Telegram)
34. Skip-trace router (`services/lib/skiptrace_router.py`): cache-first (phones or traced <90d → 0 calls) → DealMachine org 40711 → Tracerfy $0.02 → REAPI $0.05 → DealMachine org 23501 after reset; per-vendor stamp on the row so a miss is never re-billed at the same vendor; vendor benched 1h on 401/402 + Telegram ping; mobile-only, DNC kept. Used by /api/skiptrace and the enrichment worker. Verify: bench one vendor by key and the button still returns numbers.
35. CourtListener on the card: free API token (5,000/day); on every append run one RECAP search on the owner name scoped to the property state's bankruptcy + district courts, cache on the row (court_records jsonb); Pro "Court records" row shows chapter / filed / docket link. "Advanced search" tap = nationwide name search. Liens & judgments stay REAPI $0.75 Premium on tap.
36. Obituary → owner match daily cron: Echovita + Legacy national pulls already land in offramp-scout/storage/obits (~4.5k/day); match name + city/state against hit_list owners and contacts, set deceased_flag + deceased_source='obituary' + obit_url + date_of_death, lead type "deceased pre-probate". Verify: daily count of new matches in heartbeat.

37. Trial + tiers (Ted 2026-10-03 11:10Z, awaiting trial length + which plan): Stripe Checkout trial_period_days (card on file) converting to Pro $49; tiers Free / Pro $49 / Premium $99 / Team $199 (seats). Verify: test-mode checkout shows the trial end date; day-8 invoice created.
38. Postcards (Ted 2026-10-03, awaiting GO): PostGrid API, "Send postcard" on the card w/ photo + message template, allowance per plan (Pro 25/mo, Premium 100/mo, overage $1.25) metered on offramp_credit_ledger. Verify: one real postcard to Ted's address.
39. SEO forward plan (Ted asked 2026-10-03; Ron's list pending): city pages, "auctions this week in <state/county>", how-to-bid per state (bid_pages), probate/bankruptcy lead-type pages — all generated from existing data; fold in Ron's ideas and rank once received.

### Timeline
- 5A #22 finishes today; #23/#24 by 2026-10-04.
- 5B #26/#27 by 2026-10-05; #28 mockup by 2026-10-06.
- 5C #30 checklist page by 2026-10-05.
- 5D #31 page today; wiring winners by 2026-10-07.
- 5E blocked on Ted's enrollment; report overdue in heartbeat per the standing rule.
