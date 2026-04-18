# iOS Build Pipeline — Mobile-First Delivery

**Status:** infrastructure only; not yet activated. Blocked on Apple-side credential bootstrap that only Tim can do (requires his Apple ID login in Safari, one-time).

## Why this exists

PrinterPilot and ClaudeCode are iOS apps. Right now, getting a source change onto Tim's iPhone requires Xcode.app running on a Mac with a user logged in. That violates the mobile-first principle — if Tim has a code change to test, he needs a desktop.

The target end state: every push to `main` in an iOS app repo triggers a GitHub Actions build that uploads to **TestFlight**. Tim's iPhone receives a notification from the TestFlight app when a new build lands, one tap to install. No desktop involved.

Until this is bootstrapped, development builds fall back to `xcodebuild` CLI on Mac Mini targeting a **paired wireless device** (Tim's iPhone must be on the same LAN / Tailscale network). See `scripts/ios-build-and-install.sh`.

## Bootstrap — one-time, from Tim's phone Safari (no desktop)

The bootstrap requires Tim to produce three Apple artifacts and paste them into Mac Mini keychain. Apple's portal works fine on iPhone Safari.

### 1. App Store Connect API key
- Open https://appstoreconnect.apple.com/access/users → **Keys** tab.
- Click "Generate API Key", role **App Manager**.
- Download the `.p8` file (Safari saves to Files). Note the **Key ID** and **Issuer ID** shown on the page.
- Paste into keychain via `/rotate-token APPLE_APP_STORE_CONNECT_API_KEY <p8 body>` (skill auto-extracts the encoded key).
- Store the key ID + issuer ID:
  ```
  security add-generic-password -a APPLE_API_KEY_ID     -s tim-credentials -w '<KEY_ID>'   -U
  security add-generic-password -a APPLE_API_ISSUER_ID  -s tim-credentials -w '<ISSUER>'   -U
  ```

### 2. Distribution certificate + provisioning profile (via fastlane match)
- Create a new **private** GitHub repo `timtrailor-hash/ios-certs` (empty, no README).
- Pick a strong match passphrase. Store it: `security add-generic-password -a MATCH_PASSWORD -s tim-credentials -w '<pass>' -U`.
- Let the CI workflow run `fastlane match appstore` on first push — it generates the distribution cert + AppStore profile and encrypts them into `ios-certs`. Subsequent runs just decrypt.

### 3. Apple Team ID
- Find in App Store Connect → Users and Access → Integrations → Team details. Or in any existing Xcode project → Signing tab.
- `security add-generic-password -a APPLE_TEAM_ID -s tim-credentials -w '<10-char ID>' -U`.

## What the CI workflow does (once activated)

See `.github/workflows/ios-testflight.yml` in each app repo (to be added per-repo in a follow-up).

```
on: push to main
steps:
  - checkout
  - setup-xcode (macOS runner)
  - restore fastlane match (decrypt from ios-certs repo)
  - xcodebuild archive + export .ipa
  - fastlane pilot upload (→ TestFlight)
  - notify via APNs (reuses conversation server's _send_push_notification)
```

The workflow reads all secrets from repo-level GitHub Secrets, which are populated from the Mac Mini keychain by a controlplane job (see `scripts/sync-ios-secrets-to-github.sh`, also follow-up).

## What the fallback CLI script does (available now)

`scripts/ios-build-and-install.sh <app-name>` runs on Mac Mini:

1. `xcodebuild clean build` targeting the paired device by name.
2. Uses devicectl to install the resulting .app onto the paired phone.
3. Logs to `/tmp/ios-build-<app>-<ts>.log`.

Requirements:
- Phone on same LAN/Tailscale (paired in Xcode once, pairing persists).
- Xcode installed on Mac Mini (already is).
- Apple Developer account logged in on Mac Mini (already is — used for current manual builds).

This is good for dev iteration; **not** good for long-distance deploys (phone not on home network) — TestFlight covers that case.

## Scope kept out

- **App Store submission** (not TestFlight) — remains a manual Xcode Organizer step. Rare; bounded; acceptable as a true desktop task.
- **Code-sign certificate rotation** — uses `fastlane match nuke` + regeneration. Once the `ios-certs` repo is set up, this is all CI-driven, but the re-bootstrap is a separate skill.
- **Simulator builds** — xcodebuild -destination "platform=iOS Simulator,..." works from CLI, not touched here.
