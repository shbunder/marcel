"""A small, strict YAML subset loader for the fake claude's scenario files.

The shim runs as a plain `python3` script (on `PATH`, outside any venv), so it can only use the
standard library. Scenario files use the block subset of YAML:

- block mappings (`key: value`, `key:` followed by an indented block);
- block sequences (`- value`, `- key: value` with more keys indented under it);
- scalars: `null`/`~`, `true`/`false`, integers, floats, 'single' or "double" quoted strings, and
  plain strings; `[]` and `{}` for empty collections;
- `#` comments.

Anything outside the subset (flow collections, anchors, block scalars, a plain value that holds
`: `) is a readable `YamlError` naming the line, never a silent misread.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

KEY = re.compile(r'^([A-Za-z0-9_][A-Za-z0-9_.-]*)\s*:(?:\s+(.*))?$')
INT = re.compile(r'^[-+]?\d+$')
FLOAT = re.compile(r'^[-+]?(\d+\.\d*|\.\d+|\d+(\.\d*)?[eE][-+]?\d+)$')
RESERVED = tuple('[]{}&*!|>%@`')


class YamlError(ValueError):
    pass


@dataclass
class _Line:
    indent: int
    text: str
    number: int


def loads(text: str) -> Any:
    lines = _lines(text)
    if not lines:
        return None
    value, i = _block(lines, 0, lines[0].indent)
    if i < len(lines):
        raise YamlError(f'line {lines[i].number}: unexpected indentation')
    return value


def _lines(text: str) -> list[_Line]:
    out: list[_Line] = []
    for number, raw in enumerate(text.splitlines(), 1):
        if '\t' in raw[: len(raw) - len(raw.lstrip())]:
            raise YamlError(f'line {number}: indent with spaces, not tabs')
        stripped = _strip_comment(raw).rstrip()
        if stripped.strip():
            out.append(_Line(len(stripped) - len(stripped.lstrip(' ')), stripped.strip(), number))
    return out


def _strip_comment(raw: str) -> str:
    quote = ''
    i = 0
    while i < len(raw):
        ch = raw[i]
        if quote:
            if ch == '\\' and quote == '"':
                i += 1  # skip the escaped character
            elif ch == quote and quote == "'" and raw[i + 1 : i + 2] == "'":
                i += 1  # '' is an escaped quote
            elif ch == quote:
                quote = ''
        elif ch in '"\'' and (i == 0 or raw[i - 1] in ' :-'):
            quote = ch
        elif ch == '#' and (i == 0 or raw[i - 1] == ' '):
            return raw[:i]
        i += 1
    return raw


def _is_item(text: str) -> bool:
    return text == '-' or text.startswith('- ')


def _block(lines: list[_Line], i: int, indent: int) -> tuple[Any, int]:
    if _is_item(lines[i].text):
        return _seq(lines, i, indent)
    return _map(lines, i, indent)


def _seq(lines: list[_Line], i: int, indent: int) -> tuple[list[Any], int]:
    out: list[Any] = []
    while i < len(lines) and lines[i].indent == indent and _is_item(lines[i].text):
        line = lines[i]
        rest = line.text[1:].lstrip(' ')
        if not rest:
            i += 1
            if i < len(lines) and lines[i].indent > indent:
                value, i = _block(lines, i, lines[i].indent)
            else:
                value = None
        elif KEY.match(rest):
            inner = indent + len(line.text) - len(rest)
            lines[i] = _Line(inner, rest, line.number)
            value, i = _map(lines, i, inner)
        else:
            value, i = _scalar(rest, line.number), i + 1
        out.append(value)
    return out, i


def _map(lines: list[_Line], i: int, indent: int) -> tuple[dict[str, Any], int]:
    out: dict[str, Any] = {}
    while i < len(lines) and lines[i].indent == indent:
        line = lines[i]
        match = KEY.match(line.text)
        if not match:
            raise YamlError(f'line {line.number}: expected `key: value`, got {line.text!r}')
        key, rest = match.group(1), match.group(2)
        if key in out:
            raise YamlError(f'line {line.number}: duplicate key {key!r}')
        i += 1
        if rest:
            out[key] = _scalar(rest, line.number)
        elif i < len(lines) and (
            lines[i].indent > indent or lines[i].indent == indent and _is_item(lines[i].text)
        ):
            out[key], i = _block(lines, i, lines[i].indent)
        else:
            out[key] = None
    if i < len(lines) and lines[i].indent > indent:
        raise YamlError(f'line {lines[i].number}: unexpected indentation')
    return out, i


def _scalar(text: str, number: int) -> Any:
    if text.startswith('"'):
        try:
            value = json.loads(text)
        except json.JSONDecodeError as e:
            raise YamlError(f'line {number}: bad double-quoted string: {e.msg}') from None
        return value
    if text.startswith("'"):
        if len(text) < 2 or not text.endswith("'"):
            raise YamlError(f'line {number}: unterminated single-quoted string')
        return text[1:-1].replace("''", "'")
    if text in ('[]', '{}'):
        return [] if text == '[]' else {}
    if text in ('null', '~', 'Null', 'NULL'):
        return None
    if text in ('true', 'True', 'TRUE'):
        return True
    if text in ('false', 'False', 'FALSE'):
        return False
    if INT.match(text):
        return int(text)
    if FLOAT.match(text):
        return float(text)
    if text.startswith(RESERVED) or ': ' in text or text.endswith(':'):
        raise YamlError(f'line {number}: quote this value: {text!r}')
    return text
