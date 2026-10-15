#!/bin/sh
# SP12 step 4: hits the public address every 5 s and logs each change between up and down (UTC), so the time to
# come back after sleep or a Wi-Fi change can be read off. Usage: sh probe.sh https://<name>.ts.net > probe.log
last=""
while true; do
  code=$(curl -s -m 8 -o /dev/null -w '%{http_code}' "$1/api/health")
  state=$([ "$code" = 200 ] && echo up || echo "down($code)")
  if [ "$state" != "$last" ]; then echo "$(date -u +%H:%M:%S) $state"; last=$state; fi
  sleep 5
done
