#!/bin/bash
# rollback.sh — revert to previous commit and redeploy
set -euo pipefail
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_DIR"
echo "Current HEAD: $(git log --oneline -1)"
echo "Will revert to: $(git log --oneline -1 HEAD~1)"
read -p "Proceed? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    git revert HEAD --no-edit
    bash "$REPO_DIR/deploy.sh"
    echo "Rolled back and redeployed"
else
    echo "Cancelled"
fi
