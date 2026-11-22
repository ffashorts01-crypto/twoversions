#!/usr/bin/env bash
# On a failed job: DM the owner via the Telegram bot if TELEGRAM_OWNER_CHAT_ID is set (else GitHub's own failure e-mail is the alert).
[ -n "${TELEGRAM_OWNER_CHAT_ID:-}" ] && [ -n "${TELEGRAM_BOT_TOKEN:-}" ] || { echo "no owner chat id - relying on GitHub e-mail"; exit 0; }
msg="twoversions: ${GITHUB_WORKFLOW} failed (run ${GITHUB_RUN_ID}). ${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}"
tail -5 logs/auto_run.log 2>/dev/null | grep -q . && msg="$msg
$(tail -3 logs/auto_run.log | cut -c1-200)"
curl -s -o /dev/null -w "telegram notify: %{http_code}\n" "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
  --data-urlencode "chat_id=${TELEGRAM_OWNER_CHAT_ID}" --data-urlencode "text=${msg}" || true
