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
