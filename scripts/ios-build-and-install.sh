#!/bin/bash
# ios-build-and-install.sh — build an iOS app on Mac Mini and push the
# resulting .app to a paired wireless device. Intended for dev iteration
# when TestFlight would be too slow (or isn't set up yet for this app).
#
# Requirements:
#   - Xcode installed at /Applications/Xcode.app.
#   - Target device paired in Xcode previously (pairing persists across
#     Xcode restarts; Xcode GUI does not need to be running to build+install).
#   - Apple Developer account signed into Mac Mini keychain.
#
# Usage:
#   ios-build-and-install.sh PrinterPilot
#   ios-build-and-install.sh ClaudeCode
# Override device:
#   TARGET_DEVICE="Tim's Iphone" ios-build-and-install.sh PrinterPilot

set -euo pipefail

APP_NAME="${1:?app name required (e.g. PrinterPilot)}"
TARGET_DEVICE="${TARGET_DEVICE:-Tim\'s Iphone}"
TS=$(date +%Y%m%d-%H%M%S)
LOG=/tmp/ios-build-"${APP_NAME}"-"${TS}".log
DERIVED=/tmp/DerivedData-"${APP_NAME}"-"${TS}"

APP_REPO="/Users/timtrailor/code/${APP_NAME}"
if [ ! -d "$APP_REPO" ]; then
    echo "ios-build: no such repo: $APP_REPO" >&2
    exit 2
fi

PROJECT_FILE=$(ls "${APP_REPO}"/*.xcodeproj 2>/dev/null | head -1)
if [ -z "$PROJECT_FILE" ]; then
    echo "ios-build: no .xcodeproj in $APP_REPO" >&2
    exit 2
fi

echo "ios-build: building $APP_NAME → '$TARGET_DEVICE'"
echo "ios-build: log → $LOG"

cd "$APP_REPO"
/usr/bin/xcodebuild \
    -scheme "$APP_NAME" \
    -destination "platform=iOS,id=00008140-000519461162801C" \
    -configuration Debug \
    -derivedDataPath "$DERIVED" \
    -allowProvisioningUpdates \
    clean build > "$LOG" 2>&1

RC=$?
if [ $RC -ne 0 ]; then
    echo "ios-build: xcodebuild failed (rc=$RC). See $LOG"
    tail -40 "$LOG"
    exit $RC
fi

# Locate the built .app
APP_BUNDLE=$(find "$DERIVED/Build/Products" -name "${APP_NAME}.app" -type d | head -1)
if [ -z "$APP_BUNDLE" ]; then
    echo "ios-build: build succeeded but .app bundle not found under $DERIVED" >&2
    exit 3
fi
echo "ios-build: built $APP_BUNDLE"

# devicectl expects the device identifier from `xcrun devicectl list devices`
DEVICE_ID=$(/usr/bin/xcrun devicectl list devices 2>/dev/null | awk -v name="$TARGET_DEVICE" '
    $0 ~ name {
        # Identifier is the 3rd or 4th column depending on spacing; match UUID pattern
        for (i=1; i<=NF; i++) {
            if ($i ~ /^[A-F0-9]{8}-[A-F0-9]{4}-[A-F0-9]{4}-[A-F0-9]{4}-[A-F0-9]{12}$/) {
                print $i; exit
            }
        }
    }
')
if [ -z "$DEVICE_ID" ]; then
    echo "ios-build: could not find device ID for '$TARGET_DEVICE'. Run: xcrun devicectl list devices" >&2
    exit 4
fi

echo "ios-build: installing to $DEVICE_ID..."
/usr/bin/xcrun devicectl device install app --device "$DEVICE_ID" "$APP_BUNDLE" | tee -a "$LOG"

echo "ios-build: done. $APP_NAME is on ${TARGET_DEVICE}."
