#!/bin/sh
# SP13: append the hook's JSON input, one line, to logs/hooks.jsonl next to the plugin.
dir="$(dirname "$0")/../../logs"
mkdir -p "$dir"
printf '{"kind":"%s","at":"%s","input":%s}\n' "$1" "$(date -u +%FT%TZ)" "$(cat)" >> "$dir/hooks.jsonl"
