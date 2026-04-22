# ISSUE-71e905: Habitat taxonomy documentation rewrite (Phase 4 of 3c1534)

**Status:** Open
**Created:** 2026-04-22
**Assignee:** Unassigned
**Priority:** Medium
**Labels:** docs

## Capture

**Follow-up to [[ISSUE-3c1534]] Phase 4.** With the kernel-side rename shipped and the zoo migration tracked separately, the documentation needs a coherent rewrite so a new contributor reading the repo learns the five-habitat taxonomy in one place, with per-kind deep dives.

**Resolved intent:** Author one canonical taxonomy page (`docs/habitats.md`) + four new per-kind deep dives (`agents.md`, `channels.md`, `jobs.md`, plus a rewritten `plugins.md` refocused on toolkit). Update all cross-reference pages (`skills.md`, `README.md`, `SETUP.md`, `CLAUDE.md`) with the new vocabulary. Rename `.claude/rules/integration-pairs.md` → `toolkit-skill-pairs.md` with updated vocabulary. Update `mkdocs.yml` nav.

One coherent PR — not piecemeal — so cross-references can't drift mid-rewrite.

## Description

### New canonical page — `docs/habitats.md`

The single source of truth for the taxonomy. Sections:

1. **Overview** — the five kinds in one side-by-side comparison table (name, dir, shape, who calls it, what it calls).
2. **Pick your habitat** — "when do I add which kind?" decision flowchart.
3. **Minimal example per kind** — one snippet each, shortest thing that could possibly work.
4. **Composition** — how habitats reference each other (by name, uniformly) + the cross-reference diagram from ISSUE-3c1534.
5. **Cross-links** to per-kind deep dives.

### Per-kind deep dives

| Page | Content |
|---|---|
| `docs/plugins.md` — **rewritten as the toolkit deep dive** | Today's "Integration habitat" section renamed to "Toolkit habitat". Covers `toolkit.yaml` schema, `@marcel_tool` decorator, in-process vs UDS isolation modes, `provides:` namespace rule, `scheduled_jobs:` declaration. References both Pattern 2 (standard via toolkit dispatcher) and Pattern 3 (advanced, as native pydantic-ai tool). |
| `docs/skills.md` — **vocabulary update** | `depends_on: [foo]` resolves against `toolkit/` (not `integrations/`). Documents power-skill (has `depends_on:`) vs soft-skill (pure instruction) framing. Existing examples updated. |
| `docs/agents.md` — **new** (split from plugins.md) | Subagent frontmatter schema. Model selection / tool allowlist / max_requests / timeout_seconds. How the main agent invokes via `delegate`. Why subagents are NOT containerised / subprocess-isolated today. |
| `docs/channels.md` — **new** (split from plugins.md) | Channel habitat bidirectional architecture (inbound webhook router + outbound push methods). `channel.yaml` capabilities schema. `CHANNEL.md` format-hint injection. UDS roadmap via [[ISSUE-931b3f]]. |
| `docs/jobs.md` — **new** | The three `trigger_type` shapes. Retry + backoff chain. `notify` policy. `scheduled_jobs:` from toolkit habitats vs explicit `jobs/<name>/template.yaml` files. |

### Existing page updates

| File | Change |
|---|---|
| `README.md` | Architectural-decisions bullet updated to name the five kinds with current vocabulary. Link to `docs/habitats.md` as entry point. |
| `SETUP.md` | `make zoo-setup` references `toolkit/` not `integrations/`. New "Habitat overview" section linking to `docs/habitats.md`. |
| `CLAUDE.md` | Section "Integration pattern (summary)" → "Habitat taxonomy (summary)" with the five-kind table. |
| `.claude/rules/integration-pairs.md` | Renamed to `toolkit-skill-pairs.md` with updated vocabulary + power/soft skill framing. |
| `mkdocs.yml` | Nav updated: `habitats.md` top-level, per-kind pages nested underneath. |

### Straggler sweep

After the rewrite, grep for:

- `integration` — remaining hits must be either (a) historical references in closed issue files (acceptable), (b) the `integration` tool alias deprecation notes (acceptable), (c) in the ISSUE-3c1534 Phase 5 cleanup issue as "still to remove" (acceptable). Any other live hit is a missed stragger.
- `@register` — only in back-compat tests + deprecation notes. Other hits → miss.

## Tasks

- [ ] Author `docs/habitats.md` (new canonical)
- [ ] Rewrite `docs/plugins.md` as the toolkit deep dive
- [ ] Author `docs/agents.md` — subagent deep dive
- [ ] Author `docs/channels.md` — channel deep dive
- [ ] Author `docs/jobs.md` — trigger_type deep dive
- [ ] Update `docs/skills.md` vocabulary + power/soft framing
- [ ] Update `README.md` architectural-decisions bullet
- [ ] Update `SETUP.md` `make zoo-setup` + add habitat overview section
- [ ] Update `CLAUDE.md` habitat-taxonomy summary
- [ ] `git mv .claude/rules/integration-pairs.md .claude/rules/toolkit-skill-pairs.md` + vocabulary update
- [ ] Update `mkdocs.yml` nav
- [ ] Straggler grep — sweep `integration` / `@register` across all non-historical files
- [ ] `uv run mkdocs build --strict` green
- [ ] `/finish-issue` → merged close commit on main

## Relationships

- Follows: [[ISSUE-3c1534]] (five-habitat taxonomy — Phase 1 shipped)
- Overlaps: [[ISSUE-ea6d47]] (Phase 2 jobs trigger_type — `docs/jobs.md` deep dive is authored here but describes the executor that Phase 2 builds)
- Overlaps: [[ISSUE-d7eeb1]] (Phase 3 zoo rename — docs example snippets show the new names)
- Precedes: [[ISSUE-3c1534]]'s Phase 5 alias removal (clean docs are a precondition for the straggler-grep check Phase 5 requires)
