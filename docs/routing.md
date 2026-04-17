# Session Tier Routing

Marcel classifies each new session as either **FAST** or **STANDARD** on
its first user message, using keyword lists stored in
`~/.marcel/routing.yaml`. The classification is editable at runtime — the
file is re-read on mtime change, so your edits apply on the next turn
without a restart. The four-tier architecture and the precedence rules
live in [model-tiers.md](model-tiers.md); this page documents the
routing config itself.

## Where the file lives

`~/.marcel/routing.yaml` is seeded from
`src/marcel_core/defaults/routing.yaml` the first time Marcel starts. If
you delete it, it is re-created from the default on the next startup. If
you corrupt it (invalid YAML, broken regex), Marcel logs a warning and
falls back to the baked-in defaults — a broken edit never bricks the
router.

The file is **household-level**: one routing config per Marcel install,
not per family member. Dutch and English patterns live side by side and
both run against every message; language autodetection is out of scope.

## File structure

```yaml
fast_triggers:
  en:
    - "\\bwhat(?:'s| is)\\b"
    - "\\btime\\b"
  nl:
    - "\\bwat\\b"
    - "\\bhoe laat\\b"

standard_triggers:
  en:
    - "\\bdebug\\b"
    - "\\banalyze\\b"
    - "```"
  nl:
    - "\\bdebug\\b"
    - "\\banalyseer\\b"

frustration_triggers:
  en:
    - "\\b(wtf|ffs|omfg)\\b"
    - "\\bthis sucks\\b"
  nl:
    - "\\b(verdomme|godverdomme|gvd)\\b"
    - "\\bwaardeloos\\b"

default_tier: standard
```

- `fast_triggers` — patterns that suggest a simple lookup or Q&A. When
  any match on the session's first message, the session is classified
  as FAST.
- `standard_triggers` — patterns that suggest complex work (coding,
  analysis, multi-step planning). When any match, the session is
  classified as STANDARD. **STANDARD wins over FAST** when both fire —
  complexity trumps the lookup signal.
- `frustration_triggers` — patterns that indicate the user is frustrated
  with the current answer. A match on a FAST session bumps the session
  tier to STANDARD and persists the change. A match on a STANDARD session
  is a no-op (POWER is subagent-only — see
  [model-tiers.md](model-tiers.md)).
- `default_tier` — the tier used when no fast/standard trigger matches.
  Must be `fast` or `standard`. Anything else logs a warning and falls
  back to `standard`.

All patterns use Python regex syntax and match case-insensitively. The
YAML layer requires double-escaping: `\\b` in YAML becomes `\b` in the
compiled regex. Patterns that fail to compile are logged with a warning
and dropped; the rest still work.

The `en` / `nl` split is informational — both keys are flattened into one
list per category at load time. You can nest additional language keys
(`de`, `fr`, ...) or drop the nesting entirely and use a flat list; both
shapes parse.

## How classification happens

1. The session's **first user message** runs against `standard_triggers`
   first, then `fast_triggers`. The first match wins; if neither matches,
   `default_tier` is used.
2. The result is saved to the user's
   `~/.marcel/users/{slug}/settings.json` under
   `channel_tiers.{channel}`.
3. Subsequent turns in the same session skip the classifier entirely and
   reuse the stored tier.
4. On every turn (after classification), the message runs against
   `frustration_triggers`. A match on a FAST session bumps the stored
   tier to STANDARD.
5. When a session is idle-summarized (`MARCEL_IDLE_SUMMARIZE_MINUTES`,
   default 60 minutes), `channel_tiers.{channel}` is cleared — the next
   turn re-classifies from scratch.

## Editing the config

```bash
$EDITOR ~/.marcel/routing.yaml
```

Save, then send a message in a fresh channel. The new patterns apply
immediately — the mtime-based reload picks up your change on the next
call. No restart, no redeploy.

### What to add

Patterns Marcel mistakes for complex work (and you want on FAST):

```yaml
fast_triggers:
  en:
    - "\\bping\\b"
    - "\\bquick (?:question|one)\\b"
```

Patterns Marcel mistakes for simple work (and you want on STANDARD):

```yaml
standard_triggers:
  en:
    - "\\brefactor\\b"
    - "\\btrace through\\b"
```

### When to *not* edit

- **Don't add POWER patterns.** POWER is never auto-selected; add the
  capability as a skill (`preferred_tier: power`) or subagent
  (`model: power`) instead. See [model-tiers.md](model-tiers.md).
- **Don't turn off frustration detection** to silence a chatty user.
  Frustration is the cheapest feedback loop you have; if it fires too
  often, tighten the patterns rather than removing them.

## Debugging a misroute

Every classification, bump, and chain advance is logged at INFO:

```
tier_resolved user=shaun channel=telegram tier=fast reason=classified:fast:\bwhat(?:'s| is)\b
tier_resolved user=shaun channel=telegram tier=standard reason=frustration_bump:\bthis sucks\b
tier_resolved user=shaun channel=telegram tier=power reason=skill:developer:power
```

Grep the logs for `tier_resolved` to see which pattern decided a turn's
tier. The `reason` field includes the exact regex that matched, which
points you straight at the line in `routing.yaml` that needs tightening
or broadening.

## Resetting a session tier

The tier is cleared automatically on idle summarization. To clear it
manually (e.g. during development), delete the `channel_tiers` entry from
the user's settings file:

```bash
jq 'del(.channel_tiers.telegram)' ~/.marcel/users/shaun/settings.json \
  > /tmp/settings.json && mv /tmp/settings.json ~/.marcel/users/shaun/settings.json
```

The next message re-runs the classifier.
