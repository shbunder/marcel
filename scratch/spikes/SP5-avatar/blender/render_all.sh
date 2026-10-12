#!/bin/zsh
# Renders every style x state as PNG frames, then packs each loop into an animated WebP.
B=/Applications/Blender.app/Contents/MacOS/Blender
cd "$(dirname $0)"
style=$1; samples=$2
for state in idle thinking working happy; do
  n=12; [ $state = idle ] && n=24
  $B -b -P marcel.py -- --style $style --state $state --frames $n --res 400 --samples $samples --out out/anim/$style/$state/ > out/anim/$style-$state.log 2>&1
  img2webp -loop 0 -lossy -q 88 -d 133 out/anim/$style/$state/*.png -o out/anim/$style-$state.webp >> out/anim/$style-$state.log 2>&1
  echo "$style $state done $(date +%H:%M:%S)"
done
