#!/usr/bin/env bash
# Restore state (DB, cards, Telegram/dedup/hot_check state, cost log) from the encrypted file in the orphan 'state' branch.
set -euo pipefail
: "${STATE_KEY:?STATE_KEY secret is not set}"
if git ls-remote --exit-code --heads origin state >/dev/null 2>&1; then
  git fetch -q --depth 1 origin state
  git show origin/state:state.tar.gz.enc > /tmp/state.enc
  openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass env:STATE_KEY -in /tmp/state.enc | tar -xzf -
  mkdir -p logs; echo "state restored: $(stat -c %s data/articles.db) bytes db"
else
  echo "no 'state' branch - seed it first (deploy/make_repo.sh)"; exit 1
fi
