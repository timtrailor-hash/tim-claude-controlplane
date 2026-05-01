#!/bin/zsh
PASS=$(cat ~/.keychain_pass)
security unlock-keychain -p "$PASS" ~/Library/Keychains/login.keychain-db
security set-keychain-settings ~/Library/Keychains/login.keychain-db
