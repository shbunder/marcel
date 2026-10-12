#!/bin/zsh
cd "$(dirname $0)"
B=/Applications/Blender.app/Contents/MacOS/Blender
$B -b -P dot.py -- --still out/dot-hero.png --bg dark --res 720 --samples 96 > out/anim/dot-hero.log 2>&1
for state in idle thinking working happy; do
  n=12; [ $state = idle ] && n=24
  $B -b -P dot.py -- --state $state --frames $n --res 400 --samples 48 --out out/anim/dot/$state/ > out/anim/dot-$state.log 2>&1
  img2webp -loop 0 -lossy -q 88 -d 133 out/anim/dot/$state/*.png -o out/anim/dot-$state.webp >> out/anim/dot-$state.log 2>&1
  echo "dot $state done $(date +%H:%M:%S)"
done
