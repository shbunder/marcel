#!/bin/zsh
cd "$(dirname $0)"
B=/Applications/Blender.app/Contents/MacOS/Blender
while pgrep -f "style clay" >/dev/null; do sleep 20; done
for spec in "toon 0" "clay 24"; do
  set -- ${=spec}
  rm -rf out/anim/$1/working
  $B -b -P marcel.py -- --style $1 --state working --frames 12 --res 400 --samples $2 --out out/anim/$1/working/ > out/anim/$1-working-fix.log 2>&1
  img2webp -loop 0 -lossy -q 88 -d 133 out/anim/$1/working/*.png -o out/anim/$1-working.webp >> out/anim/$1-working-fix.log 2>&1
  echo "$1 working re-rendered $(date +%H:%M:%S)"
done
