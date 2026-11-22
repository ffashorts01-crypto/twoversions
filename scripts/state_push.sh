#!/usr/bin/env bash
# Save state as ONE encrypted file in the orphan 'state' branch (force-pushed: no history growth). Never stores plaintext.
set -euo pipefail
: "${STATE_KEY:?STATE_KEY secret is not set}"
test -f data/articles.db || { echo "no db - nothing to save"; exit 0; }
python3 -c "import sqlite3;c=sqlite3.connect('data/articles.db');c.execute('PRAGMA wal_checkpoint(TRUNCATE)');c.close()"
files=$(while read -r f; do [ -n "$f" ] && [ -e "$f" ] && echo "$f"; done < scripts/state_files.txt)
tar -czf /tmp/state.tar.gz $files
openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass env:STATE_KEY -in /tmp/state.tar.gz -out /tmp/state.tar.gz.enc
rm -rf /tmp/statebr && mkdir /tmp/statebr && cd /tmp/statebr
git init -q -b state && cp /tmp/state.tar.gz.enc . && git add state.tar.gz.enc
git -c user.name=bot -c user.email=bot@users.noreply.github.com commit -qm "state $(date -u +%FT%TZ)"
git push -q -f "https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git" state
echo "state saved ($(stat -c %s /tmp/state.tar.gz.enc) bytes)"
