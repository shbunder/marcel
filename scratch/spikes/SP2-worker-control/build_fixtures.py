#!/usr/bin/env python3
"""Assemble SP2's fixtures from the live run (~/.claude) into fixtures/, sanitised.

Run once, right after the SP2 sessions finish. Re-running overwrites.

    python3 build_fixtures.py
"""

import json
import os
import shutil
from pathlib import Path

from sanitize import CONTEXT_ATTACHMENTS, clean_event, scrub

HERE = Path(__file__).parent
FX = HERE / 'fixtures'
HOME = Path.home()
PROJ = HOME / '.claude/projects'
JOBS = HOME / '.claude/jobs'
SLUG = '-home-shbunder-projects-marcel-src-scratch-spikes-SP2-worker-control-sandbox-'

T1 = 'bf4bd301-d2e6-4d07-aaaf-a5926cd40ca8'
T2 = 'b18d7c1c-1008-4ee3-8dbf-1d988f51b36b'
T3 = '7aa6a32d-dc9c-43e3-b170-ee90995ba343'
COPY = '4d14e24f-8933-4c3b-bc9e-472b9a1749d6'
SUB = 'agent-a918dd7c41e7ee8b8'


def read_jsonl(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def write_jsonl(p: Path, rows: list[dict]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(scrub('\n'.join(json.dumps(clean_event(r), ensure_ascii=False) for r in rows) + '\n'))


def write_json(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(scrub(json.dumps(obj, indent=2, ensure_ascii=False)) + '\n')


def content_types(d: dict) -> list[str]:
    c = (d.get('message') or {}).get('content')
    return [x.get('type') for x in c] if isinstance(c, list) else ['str']


def main() -> None:
    # transcripts (whole sessions)
    t1 = read_jsonl(PROJ / (SLUG + 'wt-t1') / f'{T1}.jsonl')
    t2 = read_jsonl(PROJ / (SLUG + 'wt-t2') / f'{T2}.jsonl')
    t3 = read_jsonl(PROJ / (SLUG + 'wt-t3') / f'{T3}.jsonl')
    cp = read_jsonl(PROJ / (SLUG + 'wt-t1') / f'{COPY}.jsonl')
    sub = read_jsonl(PROJ / (SLUG + 'wt-t1') / T1 / 'subagents' / f'{SUB}.jsonl')
    write_jsonl(FX / 'transcripts/t1-happy-path.jsonl', t1)
    write_jsonl(FX / f'transcripts/t1-happy-path/subagents/{SUB}.jsonl', sub)
    shutil.copy(PROJ / (SLUG + 'wt-t1') / T1 / 'subagents' / f'{SUB}.meta.json',
                FX / f'transcripts/t1-happy-path/subagents/{SUB}.meta.json')
    write_jsonl(FX / 'transcripts/t2-permission-approved.jsonl', t2)
    write_jsonl(FX / 'transcripts/t3-permission-denied.jsonl', t3)
    write_jsonl(FX / 'transcripts/t1-copy-resume-of-live-session.jsonl', cp)

    # daemon job files (final state); t3-blocked was captured live while the prompt was open
    for name, short in (('t1-done', 'bf4bd301'), ('t2-done', 'b18d7c1c'), ('t3-denied', '7aa6a32d'),
                        ('t1-copy-done', '4d14e24f')):
        for f in ('state.json', 'timeline.jsonl'):
            dst = FX / 'jobs' / name / f
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(scrub((JOBS / short / f).read_text()))
    for f in (FX / 'jobs/t3-blocked').iterdir():
        f.write_text(scrub(f.read_text()))

    # agents --json snapshots, one per observed state
    snaps = {}
    for line in (FX / 'agents-lifecycle.jsonl').read_text().splitlines():
        rec = json.loads(line)
        for a in rec['agents']:
            key = (a.get('status'), a.get('state'), 'pid' in a)
            snaps.setdefault(key, a)
    blocked_raw = json.loads((FX / 'agents-snapshot-blocked.raw.json').read_text())
    t3_row = next(a for a in blocked_raw if a.get('name') == 't3')
    names = {
        (None, 'working', False): 'starting',          # dispatched, no process yet
        ('busy', 'working', True): 'working',
        ('waiting', 'blocked', True): 'blocked-permission',
        ('idle', 'done', True): 'done-idle',
        (None, 'done', False): 'done-stopped',         # after `claude stop`: no pid/status
        (None, 'done', True): 'respawning',
    }
    for key, row in snaps.items():
        if key in names:
            write_json(FX / f'agents/{names[key]}.json', row)
    write_json(FX / 'agents/blocked-permission-t3.json', t3_row)
    write_json(FX / 'agents/full-list-while-t3-blocked.json', blocked_raw)
    (FX / 'agents-snapshot-blocked.raw.json').unlink()
    os.replace(FX / 'agents-lifecycle.jsonl', FX / 'agents/lifecycle.jsonl')

    # one fixture per normalised event type
    ev = FX / 'events'

    def first(rows, pred):
        return next(r for r in rows if pred(r))

    write_json(ev / 'assistant_text.json', first(t1, lambda d: d['type'] == 'assistant' and content_types(d) == ['text']))
    write_json(ev / 'assistant_thinking.json', first(t1, lambda d: d['type'] == 'assistant' and content_types(d) == ['thinking']))
    write_json(ev / 'tool_use_bash.json', first(t1, lambda d: d['type'] == 'assistant' and content_types(d) == ['tool_use']
                                                 and d['message']['content'][0]['name'] == 'Bash'))
    write_json(ev / 'tool_result_bash.json', first(t1, lambda d: d['type'] == 'user' and isinstance(d.get('toolUseResult'), dict)
                                                    and 'stdout' in d['toolUseResult']))
    write_json(ev / 'diff_edit_tool_use.json', first(t1, lambda d: d['type'] == 'assistant' and content_types(d) == ['tool_use']
                                                      and d['message']['content'][0]['name'] == 'Edit'))
    write_json(ev / 'diff_edit_tool_result.json', first(t1, lambda d: isinstance(d.get('toolUseResult'), dict)
                                                         and 'structuredPatch' in d['toolUseResult']))
    write_json(ev / 'subagent_spawn_tool_use.json', first(t1, lambda d: d['type'] == 'assistant' and content_types(d) == ['tool_use']
                                                           and d['message']['content'][0]['name'] == 'Agent'))
    write_json(ev / 'subagent_spawn_tool_result.json', first(t1, lambda d: d['type'] == 'user' and content_types(d) == ['tool_result']
                                                              and 'agentId' in json.dumps(d['message']['content'])))
    write_json(ev / 'subagent_handback_queue.json', [d for d in t1 if d['type'] == 'queue-operation'])
    write_json(ev / 'subagent_handback_user_message.json', first(t1, lambda d: d['type'] == 'user' and content_types(d) == ['str']
                                                                  and 'agent-message' in d['message']['content']))
    write_json(ev / 'subagent_meta.json', json.loads((PROJ / (SLUG + 'wt-t1') / T1 / 'subagents' / f'{SUB}.meta.json').read_text()))
    write_json(ev / 'turn_end.json', first(t1, lambda d: d['type'] == 'system' and d.get('subtype') == 'turn_duration'))
    write_json(ev / 'user_prompt.json', first(t1, lambda d: d['type'] == 'user' and content_types(d) == ['str']))
    # permission: pending is visible only outside the transcript; approval/denial show up as below
    write_json(ev / 'permission_pending.json', {
        'agents_json_row': t3_row,
        'jobs_state_json': json.loads((FX / 'jobs/t3-blocked/state.json').read_text()),
        'note': 'While a prompt is open the transcript has NO entry for it (the tool_use is not even flushed yet).',
    })
    t2_use = first(t2, lambda d: d['type'] == 'assistant' and content_types(d) == ['tool_use'])
    t2_res = first(t2, lambda d: d['type'] == 'user' and content_types(d) == ['tool_result'])
    write_json(ev / 'permission_approved.json', {'tool_use': t2_use, 'tool_result': t2_res,
                                                 'note': 'No explicit approval record; only the tool_use→tool_result timestamp gap.'})
    t3_use = first(t3, lambda d: d['type'] == 'assistant' and content_types(d) == ['tool_use'])
    t3_res = first(t3, lambda d: d.get('toolDenialKind'))
    t3_int = first(t3, lambda d: d['type'] == 'user' and content_types(d) == ['text']
                   and 'interrupted' in d['message']['content'][0]['text'])
    write_json(ev / 'permission_denied.json', {'tool_use': t3_use, 'tool_result': t3_res, 'interrupt': t3_int})
    for p in ev.glob('*.json'):
        p.write_text(scrub(p.read_text()))
    print('context attachment types elided:', sorted(CONTEXT_ATTACHMENTS))


if __name__ == '__main__':
    main()
