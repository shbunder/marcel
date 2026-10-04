#!/usr/bin/env bash
# Walk the whole scenario against a running mock hub (start it with `make mock-hub`):
# pair, send a message, watch the events, approve the card, watch the task finish.
set -euo pipefail

HUB="${HUB:-http://127.0.0.1:7499}"
API="$HUB/api"
cd "$(dirname "$0")/.."
AUTH=(-H 'Authorization: Bearer mrc_walk')
json() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }
step() { printf '\n== %s\n' "$*"; }
LOG="$(mktemp)"
trap 'kill "${LISTENER:-}" 2>/dev/null || true; rm -f "$LOG"' EXIT

step "health"
curl -fsS "$API/health"; echo

step "pair (any code works)"
curl -fsS -X POST "$API/pair" -H 'Content-Type: application/json' \
  -d '{"code":"ABC123","device_name":"walk.sh"}' | json "'device ' + d['device_id']"

step "me: the agent starts idle"
curl -fsS "${AUTH[@]}" "$API/me" | json "d['agents'][0]['name'] + ' is ' + d['agents'][0]['state']['kind']"

step "open the event stream"
HUB="$HUB" uv run --quiet python scripts/ws_listen.py >"$LOG" &
LISTENER=$!
for _ in $(seq 50); do grep -q connected "$LOG" && break; sleep 0.1; done

step "send a message to Marcel"
curl -fsS -X POST "${AUTH[@]}" -H 'Content-Type: application/json' \
  "$API/conversations/cnv_main_marcel/messages" \
  -d '{"text":"Can you fix the flaky login test?","client_id":"walk-1"}' | json "d['id'] + ': ' + d['text']"

step "wait for the approval card"
APPROVAL=""
for _ in $(seq 100); do
  APPROVAL="$(curl -fsS "${AUTH[@]}" "$API/approvals" |
    json "next((a['id'] for a in d if a['state']=='open' and a['id'].startswith('apr_mock')), '')")"
  [ -n "$APPROVAL" ] && break
  sleep 0.2
done
[ -n "$APPROVAL" ] || { echo "no approval appeared"; exit 1; }
echo "open approval: $APPROVAL"

step "approve it"
curl -fsS -X POST "${AUTH[@]}" -H 'Content-Type: application/json' \
  "$API/approvals/$APPROVAL" -d '{"decision":"approve"}' | json "d['id'] + ' is ' + d['state']"

step "wait for the task to finish and the avatar to go idle"
wait "$LISTENER" || { echo "the event stream ended early"; cat "$LOG"; exit 1; }
LISTENER=""

step "events the app saw, in order"
cat "$LOG"

step "the result over REST"
curl -fsS "${AUTH[@]}" "$API/tasks" | json "', '.join(t['title'] + ' (' + t['state'] + ')' for t in d['items'] if t['id'].startswith('tsk_mock'))"
curl -fsS "${AUTH[@]}" "$API/artifacts" | json "', '.join(a['title'] + ' ' + a['url'] for a in d['items'] if a['id'].startswith('art_mock'))"

step "replay: reconnect with since=0 sees the same events"
REPLAY="$(HUB="$HUB" uv run --quiet python scripts/ws_listen.py 0)"
printf '%s\n' "$REPLAY" | sed -n '1,4p'
echo "... $(printf '%s\n' "$REPLAY" | grep -c .) lines in all"

step "walk finished"
