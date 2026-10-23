#!/usr/bin/env bash
# A message to the operators' Slack channel when something on the server fails unattended: a dump (backup.sh), the
# maintenance window (maintenance.sh), a container that does not start in a deploy (restart.sh). Security audit
# 2026-10-09, BT6: before, failures only went to a log file nobody reads.
#   ./alarm.sh "what failed"
# The address is the app's own SLACK_WEBHOOK_URL in the .env (app/notify.py); it is never printed and goes to curl
# through stdin, not on its command line. Without it (or without curl) the message only goes to stderr, i.e. the log.
set -uo pipefail
cd "$(dirname "$0")"
text="Askurious server $(hostname 2>/dev/null || echo '?'): $*"
echo "ALARM: $*" >&2
url=""
[ -f .env ] && url=$(sed -n 's/^SLACK_WEBHOOK_URL=//p' .env | tail -n 1 | tr -d '\r"'"'")
case "$url" in https://hooks.slack.com/*) ;; *) exit 0 ;; esac
command -v curl >/dev/null || { echo "alarm.sh: no curl, Slack not told" >&2; exit 0; }
# JSON by hand: the text is ours (no secrets), only quotes, backslashes and line ends need escaping
body=$(printf '%s' "$text" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' | tr '\n\r\t' '   ')
printf 'url = "%s"\n' "$url" | curl -sS -m 10 -o /dev/null -K - -H 'Content-Type: application/json' \
  --data "{\"text\": \"$body\"}" || echo "alarm.sh: Slack not reached" >&2
exit 0
