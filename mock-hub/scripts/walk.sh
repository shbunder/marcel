#!/usr/bin/env bash
# Walk the whole scenario against a running mock hub (start it with `make mock-hub`):
# flows 1-3: pair, send a message, watch the events, approve the card, watch the task finish;
# flows 4-6: the map, a new project, an energy thread filed in the inbox and moved to its project,
# the project room and its board, then the item's v2 after the approval.
set -euo pipefail

HUB="${HUB:-http://127.0.0.1:7499}"
API="$HUB/api"
cd "$(dirname "$0")/.."
AUTH=(-H 'Authorization: Bearer mrc_walk')
JSON=(-H 'Content-Type: application/json')
json() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }
step() { printf '\n== %s\n' "$*"; }
LOG="$(mktemp)"
trap 'kill "${LISTENER:-}" 2>/dev/null || true; rm -f "$LOG"' EXIT

listen() {
  : >"$LOG"
  HUB="$HUB" uv run --quiet python scripts/ws_listen.py >"$LOG" &
  LISTENER=$!
  for _ in $(seq 50); do grep -q connected "$LOG" && break; sleep 0.1; done
}

wait_for_approval() {  # $1: the task title the approval belongs to
  local id=""
  for _ in $(seq 100); do
    id="$(curl -fsS "${AUTH[@]}" "$API/approvals" | json "next((a['id'] for a in d \
      if a['state']=='open' and a['id'].startswith('apr_mock')), '')")"
    [ -n "$id" ] && break
    sleep 0.2
  done
  [ -n "$id" ] || { echo "no approval appeared" >&2; exit 1; }
  echo "$id"
}

finish() {
  wait "$LISTENER" || { echo "the event stream ended early"; cat "$LOG"; exit 1; }
  LISTENER=""
}

step "health"
curl -fsS "$API/health"; echo

step "pair (any code works)"
curl -fsS -X POST "$API/pair" "${JSON[@]}" \
  -d '{"code":"ABC123","device_name":"walk.sh"}' | json "'device ' + d['device_id']"

step "me: the agent starts idle"
curl -fsS "${AUTH[@]}" "$API/me" | json "d['agents'][0]['name'] + ' is ' + d['agents'][0]['state']['kind']"

# ---- flows 1-3: chat, the thread, the approval -------------------------------------------------

step "open the event stream"
listen

step "send a message to Marcel"
curl -fsS -X POST "${AUTH[@]}" "${JSON[@]}" "$API/conversations/cnv_main_marcel/messages" \
  -d '{"text":"Can you fix the flaky login test?","client_id":"walk-1"}' | json "d['id'] + ': ' + d['text']"

step "wait for the approval card"
APPROVAL="$(wait_for_approval)"
echo "open approval: $APPROVAL"

step "approve it"
curl -fsS -X POST "${AUTH[@]}" "${JSON[@]}" "$API/approvals/$APPROVAL" \
  -d '{"decision":"approve"}' | json "d['id'] + ' is ' + d['state']"

step "wait for the task to finish and the avatar to go idle"
finish

step "events the app saw, in order"
cat "$LOG"

step "the result over REST"
curl -fsS "${AUTH[@]}" "$API/tasks" | json "', '.join(t['title'] + ' (' + t['state'] + ')' for t in d['items'] if t['id'].startswith('tsk_mock'))"
curl -fsS "${AUTH[@]}" "$API/artifacts?kind=pr" | json "', '.join(a['title'] + ' ' + a['url'] for a in d['items'] if a['id'].startswith('art_mock'))"

# ---- flows 4-6: the map, a project room, an item's v2 ------------------------------------------

step "the map: live projects, inbox last"
curl -fsS "${AUTH[@]}" "$API/projects?agent_id=agt_marcel" | json "'\n'.join(p['name'] + ' (' + p['id'] + '): ' + str(p['counts']) for p in d)"

step "create a project"
listen
curl -fsS -X POST "${AUTH[@]}" "${JSON[@]}" "$API/projects" \
  -d '{"agent_id":"agt_marcel","name":"Garden","goal":"Plan the spring planting."}' |
  json "d['name'] + ' (' + d['slug'] + '), board ' + d['board_artifact_id']"

step "ask for something that fits no project yet"
curl -fsS -X POST "${AUTH[@]}" "${JSON[@]}" "$API/conversations/cnv_main_marcel/messages" \
  -d '{"text":"Compare the energy offers and switch if it is cheaper"}' | json "d['id'] + ': ' + d['text']"
APPROVAL="$(wait_for_approval)"
TASK="$(curl -fsS "${AUTH[@]}" "$API/approvals" | json "next(a['task_id'] for a in d if a['id']=='$APPROVAL')")"
curl -fsS "${AUTH[@]}" "$API/tasks/$TASK" | json "d['title'] + ' is filed in ' + d['project_id'] + ', ' + d['state']"
ITEM="$(curl -fsS "${AUTH[@]}" "$API/artifacts?task_id=$TASK" | json "d['items'][0]['id']")"
curl -fsS "${AUTH[@]}" "$API/artifacts/$ITEM" | json "'YOU GOT: ' + d['title'] + ' v' + str(d['current_version'])"

step "move the thread to Energy contracts"
curl -fsS -X POST "${AUTH[@]}" "${JSON[@]}" "$API/tasks/$TASK/move" \
  -d '{"project_id":"prj_01JA0A1"}' | json "d['title'] + ' is now in ' + d['project_id']"

step "the project room: board, quests and items"
BOARD="$(curl -fsS "${AUTH[@]}" "$API/projects/prj_01JA0A1" | json "d['board_artifact_id']")"
curl -fsS "${AUTH[@]}" "$API/projects/prj_01JA0A1" | json "d['name'] + ': ' + str(d['counts'])"
curl -fsS "${AUTH[@]}" "$API/artifacts/$BOARD" | json "'board v' + str(d['current_version'])"
curl -fsS "${AUTH[@]}" "$API/artifacts/$BOARD/content" |
  json "'\n'.join('  ' + w['title'] + ': ' + ('; '.join(i['text'] for x in w['widgets'] for i in x['items']) or w['empty_text']) for w in d['widgets'])"
curl -fsS "${AUTH[@]}" "$API/tasks?project_id=prj_01JA0A1" | json "'quests: ' + ', '.join(t['title'] + ' (' + t['state'] + ')' for t in d['items'])"
curl -fsS "${AUTH[@]}" "$API/artifacts?project_id=prj_01JA0A1" | json "'items: ' + ', '.join(a['title'] + ' v' + str(a['current_version']) for a in d['items'])"

step "approve the switch request"
curl -fsS -X POST "${AUTH[@]}" "${JSON[@]}" "$API/approvals/$APPROVAL" \
  -d '{"decision":"approve"}' | json "d['id'] + ' is ' + d['state']"
finish

step "events the app saw, in order"
cat "$LOG"

step "the item got a v2; the board moved on"
curl -fsS "${AUTH[@]}" "$API/artifacts/$ITEM/versions" | json "'\n'.join('  v' + str(v['version']) + ': ' + v.get('summary', '') for v in d)"
echo "v1: $(curl -fsS "${AUTH[@]}" "$API/artifacts/$ITEM/content?version=1" | tail -1)"
echo "v2: $(curl -fsS "${AUTH[@]}" "$API/artifacts/$ITEM/content" | tail -1)"
curl -fsS "${AUTH[@]}" "$API/artifacts/$BOARD" | json "'board v' + str(d['current_version'])"

step "a bad limit is a 400, not a 500"
curl -sS "${AUTH[@]}" "$API/tasks?limit=ten" -w ' (HTTP %{http_code})\n'

step "replay: reconnect with since=0 sees the first run again"
REPLAY="$(HUB="$HUB" uv run --quiet python scripts/ws_listen.py 0)"
printf '%s\n' "$REPLAY" | sed -n '1,4p'
echo "... $(printf '%s\n' "$REPLAY" | grep -c .) lines in all"

step "walk finished"
