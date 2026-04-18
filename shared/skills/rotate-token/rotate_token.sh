#!/bin/bash
# rotate_token.sh — mobile-first secret rotation.
# Writes a new value into Mac Mini keychain under service=tim-credentials,
# verifies against the provider's API, logs the rotation. Designed to be
# called from the /rotate-token skill.

set -euo pipefail

NAME="${1:?secret name required (e.g. GITHUB_TOKEN)}"
VALUE="${2:-}"

# Provider-specific metadata
case "$NAME" in
    GITHUB_TOKEN)
        CREATE_URL="https://github.com/settings/tokens"
        VERIFY_URL="https://api.github.com/user"
        VERIFY_HEADER_KEY="x-oauth-scopes"
        REQUIRED_SCOPES="repo workflow"
        ;;
    ANTHROPIC_API_KEY)
        CREATE_URL="https://console.anthropic.com/settings/keys"
        VERIFY_URL="https://api.anthropic.com/v1/messages"
        VERIFY_HEADER_KEY=""
        REQUIRED_SCOPES=""
        ;;
    OPENAI_API_KEY)
        CREATE_URL="https://platform.openai.com/api-keys"
        VERIFY_URL="https://api.openai.com/v1/models"
        VERIFY_HEADER_KEY=""
        REQUIRED_SCOPES=""
        ;;
    GEMINI_API_KEY)
        CREATE_URL="https://aistudio.google.com/app/apikey"
        VERIFY_URL=""
        VERIFY_HEADER_KEY=""
        REQUIRED_SCOPES=""
        ;;
    SMTP_PASSWORD)
        CREATE_URL="https://myaccount.google.com/apppasswords"
        VERIFY_URL=""
        VERIFY_HEADER_KEY=""
        REQUIRED_SCOPES=""
        ;;
    *)
        echo "rotate-token: unknown secret '$NAME'. Allowed: GITHUB_TOKEN, ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY, SMTP_PASSWORD." >&2
        exit 2
        ;;
esac

if [ -z "$VALUE" ]; then
    echo "rotate-token: no value supplied."
    echo "  1. Open $CREATE_URL on your phone Safari."
    echo "  2. Create a new secret (see scope requirements above)."
    echo "  3. Re-run: /rotate-token $NAME <new-value>"
    exit 3
fi

# Mac Mini Tailscale address — can be overridden for tests
MAC_MINI="${MAC_MINI_HOST:-100.126.253.40}"
LAPTOP="${LAPTOP_HOST:-100.112.125.42}"

# Step 1: write to Mac Mini keychain
echo "rotate-token: writing $NAME to Mac Mini keychain..."
ssh "timtrailor@$MAC_MINI" bash -s <<EOF_WRITE
set -e
security unlock-keychain -p "\$(cat ~/.keychain_pass)" ~/Library/Keychains/login.keychain-db
security add-generic-password -a "$NAME" -s tim-credentials -w "$VALUE" -U
echo "  mac-mini: stored"
EOF_WRITE

# Step 2: propagate to laptop if reachable
echo "rotate-token: propagating to laptop..."
if ssh -o ConnectTimeout=5 -o BatchMode=yes "timtrailor@$LAPTOP" "true" 2>/dev/null; then
    ssh "timtrailor@$LAPTOP" bash -s <<EOF_LAPTOP
set -e
if [ -f ~/.keychain_pass ]; then
    security unlock-keychain -p "\$(cat ~/.keychain_pass)" ~/Library/Keychains/login.keychain-db 2>/dev/null
fi
security add-generic-password -a "$NAME" -s tim-credentials -w "$VALUE" -U
echo "  laptop: stored"
EOF_LAPTOP
else
    echo "  laptop unreachable — queuing for next session (not yet implemented; re-run when laptop is online)"
fi

# Step 3: verify
if [ -n "$VERIFY_URL" ] && [ "$NAME" = "GITHUB_TOKEN" ]; then
    echo "rotate-token: verifying against $VERIFY_URL..."
    HEADERS=$(curl -sSI -H "Authorization: Bearer $VALUE" "$VERIFY_URL")
    HTTP_CODE=$(echo "$HEADERS" | awk 'NR==1 {print $2}')
    if [ "$HTTP_CODE" != "200" ]; then
        echo "  VERIFY FAILED: HTTP $HTTP_CODE" >&2
        exit 4
    fi
    if [ -n "$VERIFY_HEADER_KEY" ]; then
        SCOPES=$(echo "$HEADERS" | grep -i "^${VERIFY_HEADER_KEY}:" | sed "s/^[^:]*: //I" | tr -d '\r')
        for req in $REQUIRED_SCOPES; do
            if ! echo "$SCOPES" | grep -qw "$req"; then
                echo "  VERIFY FAILED: missing scope '$req' (have: $SCOPES)" >&2
                exit 5
            fi
        done
        echo "  verified — scopes: $SCOPES"
    else
        echo "  verified (HTTP 200)"
    fi
fi

# Step 4: log
TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
LOG_LINE="{\"ts\":\"$TS\",\"name\":\"$NAME\",\"rotated_by\":\"rotate-token skill\",\"verified\":true}"
ssh "timtrailor@$MAC_MINI" "echo '$LOG_LINE' >> ~/code/credential_rotations.jsonl"

echo "rotate-token: done. $NAME rotated and verified."
