#!/usr/bin/env python3
"""Make recorded Claude Code transcripts safe to commit, keeping every event's shape.

Context attachments (system prompt snapshot, skill/agent/tool listings, CLAUDE.md
instructions, environment, MCP instructions) carry the owner's personal setup. Their
payload fields are replaced by "<elided: N chars>" — keys, `type`, ordering and every
non-context event stay byte-for-byte the same shape. The owner's email and account/org
UUIDs are pseudonymised.

    python3 sanitize.py <in.jsonl|in.json> <out>
"""

import json
import re
import sys

CONTEXT_ATTACHMENTS = {
    'prompt_snapshot', 'skill_listing', 'instructions', 'environment', 'deferred_tools_delta',
    'deferred_tools_record', 'mcp_instructions_delta', 'agent_listing_delta', 'session_context',
    'model', 'credential_org',
}
SWAPS = [
    (re.compile(r'shaun\.bundervoet@gmail\.com'), 'owner@example.com'),
    (re.compile(r'0694de27-1cff-4f4d-988b-907774572641'), '00000000-0000-4000-8000-00000000acc7'),
    (re.compile(r'5704a743-3ad5-4675-820b-ac08e32011d2'), '00000000-0000-4000-8000-0000000000e9'),
]


def elide(v):
    s = json.dumps(v)
    return f'<elided: {len(s)} chars>'


def clean_event(d: dict) -> dict:
    a = d.get('attachment')
    if isinstance(a, dict) and a.get('type') in CONTEXT_ATTACHMENTS:
        d['attachment'] = {k: (v if k == 'type' else elide(v)) for k, v in a.items()}
        if 'rendered' in d:
            d['rendered'] = elide(d['rendered'])
    return d


def scrub(text: str) -> str:
    for pat, rep in SWAPS:
        text = pat.sub(rep, text)
    return text


def main(src: str, dst: str) -> None:
    raw = open(src).read()
    if src.endswith('.jsonl'):
        out = [json.dumps(clean_event(json.loads(l)), ensure_ascii=False) for l in raw.splitlines() if l.strip()]
        text = '\n'.join(out) + '\n'
    else:
        text = raw
    open(dst, 'w').write(scrub(text))


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
