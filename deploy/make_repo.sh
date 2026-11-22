#!/usr/bin/env bash
# Build a ready-to-push git repo for GitHub Actions:  bash deploy/make_repo.sh [/workspace/twoversions-repo]
#   branch 'main'  : code + workflows (no database, no secrets, no runtime state)
#   branch 'state' : ONE file state.tar.gz.enc = AES-256 (openssl, pbkdf2) of the files in deploy/github/scripts/state_files.txt
# The STATE_KEY passphrase lives in <repo>/../twoversions-STATE_KEY.txt (reused if present; put it into the GitHub secret STATE_KEY). Never printed.
# Re-run right before the switch-over (box routines stopped) so the GitHub state continues exactly where the box stopped.
set -euo pipefail
SRC=$(cd "$(dirname "$0")/.." && pwd); OUT=${1:-/workspace/twoversions-repo}
KEYF="$(dirname "$OUT")/twoversions-STATE_KEY.txt"
rm -rf "$OUT"; mkdir -p "$OUT"
cd "$SRC"
EXC=(--exclude='./public' --exclude='./logs' --exclude='./screenshots' --exclude='./.git' --exclude='node_modules' --exclude='__pycache__' --exclude='*.pyc'
     --exclude='./data/*.db' --exclude='./data/*.db-*' --exclude='./data/og_cache' --exclude='./data/*.bak' --exclude='./data/*.tmp' --exclude='./data/tg_feed.json'
     --exclude='./data/update.lock' --exclude='./data/editor/RUNNING' --exclude='./data/editor/_keys.json' --exclude='./server.log' --exclude='./tunnel.log'
     --exclude='./deploy/github' --exclude='./tools/days/out' --exclude='*.bak' --exclude='*.orig' --exclude='.env' --exclude='*.key.txt')
while read -r f; do [ -n "$f" ] && EXC+=(--exclude="./$f"); done < deploy/github/scripts/state_files.txt
tar "${EXC[@]}" -cf - . | tar -xf - -C "$OUT"
cp -r deploy/github/.github deploy/github/scripts deploy/github/SETUP_RU.md "$OUT"/
{ printf 'public/\nlogs/\ndata/*.db\ndata/*.db-*\ndata/og_cache/\ndata/tg_feed.json\ndata/update.lock\ndata/editor/RUNNING\ndata/editor/_keys.json\n__pycache__/\n*.pyc\nnode_modules/\n*.tmp\n'
  sed 's/^logs\/.*//' deploy/github/scripts/state_files.txt | grep . ; } > "$OUT/.gitignore"
cd "$OUT" && git init -q -b main && git add -A && git -c user.name=bot -c user.email=bot@users.noreply.github.com commit -qm "twoversions pipeline (GitHub Actions)"
[ -f "$KEYF" ] || python3 -c "import secrets;print(secrets.token_urlsafe(32))" > "$KEYF"
cd "$SRC"
python3 -c "import sqlite3;c=sqlite3.connect('data/articles.db');c.execute('PRAGMA wal_checkpoint(TRUNCATE)');c.close()"
mkdir -p /tmp/seed
files=$(while read -r f; do [ -n "$f" ] && [ -e "$f" ] && echo "$f"; done < deploy/github/scripts/state_files.txt)
tar -czf /tmp/seed/state.tar.gz $files
STATE_KEY=$(cat "$KEYF") openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass env:STATE_KEY -in /tmp/seed/state.tar.gz -out /tmp/seed/state.tar.gz.enc
rm -f /tmp/seed/state.tar.gz
cd "$OUT" && git checkout -q --orphan state && git rm -rqf . && cp /tmp/seed/state.tar.gz.enc . && git add state.tar.gz.enc \
  && git -c user.name=bot -c user.email=bot@users.noreply.github.com commit -qm "state seed $(date -u +%FT%TZ)" && git checkout -q main
echo "repo: $OUT (branches: $(git -C "$OUT" branch --format='%(refname:short)' | tr '\n' ' ')); state file: $(stat -c %s /tmp/seed/state.tar.gz.enc) bytes; key file: $KEYF"
