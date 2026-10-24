#!/bin/zsh
D=/Users/shaunbundervoet/Developer/projects/marcel-src/scratch/spikes/SP11-mac-services/la
cd /Users/shaunbundervoet/Developer/projects/marcel-src/scratch/spikes/SP11-mac-services
echo "usage run $(date -u +%FT%TZ)" >> $D/usage.log
/Users/shaunbundervoet/.local/bin/claude -p "/usage" >> $D/usage.log 2>&1; echo "exit=$?" >> $D/usage.log
