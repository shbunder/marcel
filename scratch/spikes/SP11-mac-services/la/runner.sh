#!/bin/zsh
# Stand-in for Marcel's runner under launchd: start one background session, then idle.
D=/Users/shaunbundervoet/Developer/projects/marcel-src/scratch/spikes/SP11-mac-services/la
cd /Users/shaunbundervoet/Developer/projects/marcel-src/scratch/spikes/SP11-mac-services
echo "runner start $(date -u +%FT%TZ) pid=$$ $(ps -o pid,ppid,pgid,sess -p $$ | tail -1)" >> $D/runner.log
if ! /Users/shaunbundervoet/.local/bin/claude agents --json 2>/dev/null | grep -q '"name": "sp11-probe"'; then
  /Users/shaunbundervoet/.local/bin/claude --bg --name sp11-probe "Reply with the single word OK. Do nothing else." >> $D/runner.log 2>&1
fi
/Users/shaunbundervoet/.local/bin/claude agents --json >> $D/agents.json 2>&1
while true; do sleep 3600; done
