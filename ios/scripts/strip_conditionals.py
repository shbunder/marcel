"""Copy an OpenAPI document without its `if`/`then` validation rules, for swift-openapi-generator.

The contract uses JSON Schema conditionals ("`url` is required when `kind` is `pr`"). They only tighten
validation; they never add a field. swift-openapi-generator cannot read them and turns the whole schema
into opaque `value1, value2, ...` containers. This drops them so the generated types keep their fields.
The contract itself is never changed.

Usage: uv run --with pyyaml python strip_conditionals.py <in.yaml> <out.json>
"""

import json
import sys

import yaml

CONDITIONAL_KEYS = {"if", "then", "else"}


def is_conditional(node: object) -> bool:
    return isinstance(node, dict) and bool(node) and set(node) <= CONDITIONAL_KEYS


def strip(node: object, in_properties: bool = False) -> object:
    """Drop conditionals from every schema; `in_properties` keeps a field that happens to be named `if`."""
    if isinstance(node, list):
        return [strip(item) for item in node]
    if not isinstance(node, dict):
        return node
    out = {}
    for key, value in node.items():
        if not in_properties and key in CONDITIONAL_KEYS:
            continue
        if key == "allOf" and isinstance(value, list) and all(is_conditional(item) for item in value):
            continue
        out[key] = strip(value, in_properties=key == "properties" and not in_properties)
    return out


def main() -> None:
    source, target = sys.argv[1], sys.argv[2]
    with open(source, encoding="utf-8") as handle:
        document = yaml.safe_load(handle)
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(strip(document), handle, indent=1, default=str)


if __name__ == "__main__":
    main()
