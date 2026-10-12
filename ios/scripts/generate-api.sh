#!/bin/sh
# Regenerates MarcelKit's API client (Sources/MarcelAPI) from contracts/app-api.yaml.
# Run it after the contract changes: `make -C ios generate`. The output is checked in.
set -eu
here=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

swift build -c release --package-path "$here/Tools/APIGenerator" --product swift-openapi-generator
uv run --quiet --python 3.12 --with pyyaml python "$here/scripts/strip_conditionals.py" \
  "$here/../contracts/app-api.yaml" "$work/app-api.json"
rm -f "$here"/MarcelKit/Sources/MarcelAPI/*.swift
"$here/Tools/APIGenerator/.build/release/swift-openapi-generator" generate \
  --config "$here/MarcelKit/openapi-generator-config.yaml" \
  --output-directory "$here/MarcelKit/Sources/MarcelAPI" \
  "$work/app-api.json"
