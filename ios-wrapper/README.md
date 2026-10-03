# OffRamp REI — iOS wrapper (Capacitor 6)

Master Task File item 17. A thin native shell that loads https://offramprei.com/app/ (see
`capacitor.config.json` → `server.url`) so the App Store build always runs the live web app.
Push notifications and App Store presence are the reasons it exists; all product code stays in `../app/`.

## Status 2026-10-03
- Scaffolded on the VPS: `ios/App/App.xcworkspace` generated (`npx cap add ios`).
- NOT built or uploaded. Blocked on two human steps (Ted):
  1. Apple Developer Program enrollment ($99/yr) — needed for signing, TestFlight, App Store, push certificates.
  2. Xcode on the Mac (`macbook-pro` has only Command Line Tools; no CocoaPods either).

## Build steps (on the Mac, once Xcode + the developer account exist)
```bash
sudo gem install cocoapods            # or: brew install cocoapods
rsync -a --exclude node_modules vps:/home/cortextos/cortextos/orgs/AHR/sites/offramp/ios-wrapper/ ~/offramp-ios/
cd ~/offramp-ios && PATH=/opt/homebrew/bin:$PATH npm install && npx cap sync ios
npx cap open ios                      # Xcode: set Team + bundle id com.offramprei.app, Signing & Capabilities:
                                      #   + Push Notifications, + Background Modes (remote notifications)
# Product > Archive > Distribute (TestFlight first).
```
Push: add `@capacitor/push-notifications`, register the device token to `/api/push/register` (to be built), send via APNs.
Item 19 (external checkout link) is already in the web app: inside the native shell the upgrade screen links to
`https://offramprei.com/app/?checkout=pro|premium` instead of using in-app purchase — this needs Apple's
External Link Account Entitlement request in the developer account (reader-app style) or the US external
purchase link entitlement; file it when the account exists.
Item 18 (Small Business Program, 15% commission): App Store Connect → Business → Small Business Program, after enrollment.
Item 20 (Android) skipped per the Master Task File.

## Cloud build (added 2026-10-03 — Mac is on macOS 15.7 with 26 GB free; Xcode 26 needs macOS 26.6, so the Mac is out of the path)
`.github/workflows/ios-testflight.yml` builds on a `macos-26` GitHub runner and uploads to TestFlight via `fastlane ios beta`.
Repo secrets needed (Settings → Secrets → Actions): `ASC_KEY_ID`, `ASC_ISSUER_ID`, `ASC_KEY_P8` (full .p8 text), `APPLE_TEAM_ID`,
`MATCH_PASSWORD` (vault: offramp-ios-match-password), `MATCH_GIT_TOKEN` (a GitHub PAT with repo access to Ted-s-playhouse/offramp-certs).
First run creates the app record (produce), the distribution cert + profile (match, stored encrypted in offramp-certs), builds, uploads.
