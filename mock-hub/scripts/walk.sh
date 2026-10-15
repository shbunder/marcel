#!/usr/bin/env bash
# Walk the whole scenario against a running mock hub (start it with `make mock-hub`):
# flows 1-3: pair, send a message, watch the events, approve the card, watch the task finish;
# flows 4-6: the map, a new project, an energy thread filed in the inbox and moved to its project,
# the project room and its board, then the item's v2 after the approval;
# buddies (app-api 0.8.0): Marcel and Pip, a second phone that starts on Pip, switching buddy, a look
# change, relay push; chat clarity (0.7): a result that quotes an older message, and mentions.
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

# ---- buddies (app-api 0.8.0) and chat clarity (0.7) --------------------------------------------

PIP=agt_01JA3E2
THIS_PHONE="$(curl -fsS "${AUTH[@]}" "$API/me" | json "d['device_id']")"

step "buddies: one person, several buddies"
curl -fsS "${AUTH[@]}" "$API/me" | json "d['name'] + ' has ' + ', '.join(a['name'] + ' (' + a['look']['color'] + ' ' + a['animal'] + ')' for a in d['agents'])"

step "a pairing code for a second phone that starts on Pip"
CODE="$(curl -fsS -X POST "${AUTH[@]}" "${JSON[@]}" "$API/pairing-codes" -d "{\"agent_id\":\"$PIP\"}" | json "d['code']")"
echo "code $CODE"
curl -fsS -X POST "${JSON[@]}" "$API/pair" -d "{\"code\":\"$CODE\",\"device_name\":\"iPad\"}" | json "'paired ' + d['device_id']"
curl -fsS "${AUTH[@]}" "$API/devices" | json "'\n'.join('  ' + x['name'] + ' shows ' + x['agent_id'] + (' (this phone)' if x.get('current') else '') for x in d)"

step "switch buddy: this phone shows Pip, then Pip's main conversation"
: >"$LOG"
HUB="$HUB" UNTIL=device.updated uv run --quiet python scripts/ws_listen.py >"$LOG" &
LISTENER=$!
for _ in $(seq 50); do grep -q connected "$LOG" && break; sleep 0.1; done
curl -fsS -X PATCH "${AUTH[@]}" "${JSON[@]}" "$API/devices/$THIS_PHONE" -d "{\"agent_id\":\"$PIP\"}" |
  json "d['name'] + ' now shows ' + d['agent_id']"
finish
grep device.updated "$LOG"
PIP_MAIN="$(curl -fsS "${AUTH[@]}" "$API/agents/$PIP" | json "d['main_conversation_id']")"
curl -fsS "${AUTH[@]}" "$API/conversations/$PIP_MAIN/messages" |
  json "'\n'.join('  ' + m['role'] + ': ' + m['text'] for m in d['items'] if m.get('text'))"

step "Pip gets a new look"
: >"$LOG"
HUB="$HUB" UNTIL=agent.updated uv run --quiet python scripts/ws_listen.py >"$LOG" &
LISTENER=$!
for _ in $(seq 50); do grep -q connected "$LOG" && break; sleep 0.1; done
curl -fsS -X PATCH "${AUTH[@]}" "${JSON[@]}" "$API/agents/$PIP" \
  -d '{"look":{"animal":"panda","color":"teal","personality":"cozy"}}' | json "d['name'] + ' looks like a ' + d['look']['color'] + ' ' + d['animal']"
finish
cat "$LOG"

step "push through the relay: registered, and Device.push says so"
curl -fsS -X PUT "${AUTH[@]}" "${JSON[@]}" "$API/devices/$THIS_PHONE/push" \
  -d '{"kind":"relay","relay_url":"https://push.marcel-bot.com","handle":"AAAAAAAAAAAAAAAAAAAAAA","send_secret":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","device_key":"ccccccccccccccccccccccccccccccccccccccccccA"}' |
  json "d['name'] + ' pushes via ' + d['push']"

step "back to Marcel"
curl -fsS -X PATCH "${AUTH[@]}" "${JSON[@]}" "$API/devices/$THIS_PHONE" -d '{"agent_id":"agt_marcel"}' |
  json "d['name'] + ' shows ' + d['agent_id']"

step "ask something, then say something else while it works"
FIRST="$(curl -fsS -X POST "${AUTH[@]}" "${JSON[@]}" "$API/conversations/cnv_main_marcel/messages" \
  -d '{"text":"Can you fix the flaky login test again?"}' | json "d['id']")"
APPROVAL="$(wait_for_approval)"
curl -fsS -X POST "${AUTH[@]}" "${JSON[@]}" "$API/conversations/cnv_main_marcel/messages" \
  -d '{"text":"And remind me about the dentist tomorrow."}' | json "d['id'] + ': ' + d['text']"
curl -fsS -X POST "${AUTH[@]}" "${JSON[@]}" "$API/approvals/$APPROVAL" -d '{"decision":"approve"}' >/dev/null
for _ in $(seq 100); do
  curl -fsS "${AUTH[@]}" "$API/me" | json "d['agents'][0]['state']['kind']" | grep -q idle && break
  sleep 0.2
done

step "the result quotes the first message, not the latest"
curl -fsS "${AUTH[@]}" "$API/conversations/cnv_main_marcel/messages?limit=200" |
  json "next(m['milestone']['event'] + ' answers ' + m['reply_to_message_id'] + (' (the first message, $FIRST)' if m['reply_to_message_id']=='$FIRST' else '') for m in reversed(d['items']) if m.get('kind')=='milestone')"

step "mentions: @ finds quests and items, # finds projects"
curl -fsS "${AUTH[@]}" "$API/mentions?q=login" | json "'\n'.join(m['kind'] + ' ' + m['id'] + ': ' + m['title'] for m in d[:3])"
curl -fsS "${AUTH[@]}" "$API/mentions?q=energy&kind=project" | json "', '.join(m['title'] for m in d)"
curl -fsS -X POST "${AUTH[@]}" "${JSON[@]}" "$API/conversations/cnv_main_marcel/messages" \
  -d '{"text":"Anything new on #energy?","mentions":[{"kind":"project","id":"prj_01JA0A1","title":"energy"}]}' |
  json "'stored in ' + d['project_id'] + ', mentions ' + d['mentions'][0]['title']"

step "walk finished"
