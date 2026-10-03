# OffRamp REI site + deal room


## Before any app push
Run `node scripts/app_smoke.mjs [rows.json]` (loads the real inline JS from app/index.html, calls search() against live rows, fails if the list never renders). Added 2026-10-03 after the Searching outage. Then bump the service worker VERSION in app/sw.js.
