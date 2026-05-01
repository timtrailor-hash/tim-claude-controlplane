#!/bin/zsh
# Boot-time service startup — runs via cron @reboot
# LaunchAgents handle all services. Keychain is set to no-timeout
# so no unlock is needed (audit 2026-04-11, keychain migration).

sleep 15

export HOME="/Users/timtrailor"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

# Keychain unlock REMOVED 2026-04-13.
# The login keychain is configured with no-timeout (security set-keychain-settings -u -t 0).
# It auto-unlocks at user login and stays unlocked. The previous approach read a plaintext
# password from ~/.keychain_pass, which contradicted the keychain migration (audit 2026-04-11
# §3.5). Another Claude session recreated the file on 2026-04-12 as part of a "fix" that
# undid the quarantine. The root cause was that this script existed at all.
# If keychain issues recur after reboot, investigate why no-timeout isn't holding rather
# than recreating the plaintext password file.

echo "$(date): start_services.sh done (keychain no-timeout, no unlock needed)" >> /tmp/startup.log
