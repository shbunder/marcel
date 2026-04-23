# ISSUE-5c8831: Per-kind habitat deep-dives (follow-up to ISSUE-71e905)

**Status:** Open
**Created:** 2026-04-23
**Assignee:** Unassigned
**Priority:** Low
**Labels:** docs, follow-up

## Capture

**Original request:** Implicit follow-up from [[ISSUE-71e905]] — the canonical `docs/habitats.md` + cross-reference vocabulary updates shipped, but the per-kind deep-dives were deferred to keep that issue session-sized.

**Resolved intent:** Land the four per-kind deep-dive pages that `docs/habitats.md`'s "Cross-links to per-kind deep dives" table currently points at placeholder existing pages for:

1. `docs/plugins.md` — **full rewrite** as the toolkit deep dive. Covers `toolkit.yaml` schema, `@marcel_tool` decorator, in-process vs UDS isolation modes, `provides:` namespace rule, `scheduled_jobs:` declaration. Vocabulary is fully modernised (no more "integration habitat" as the primary name; historical aliases in a single "back-compat" section at the bottom).
2. `docs/agents.md` — **new**, split from the existing `docs/subagents.md`. Subagent frontmatter schema, model selection / tool allowlist / `max_requests` / `timeout_seconds`, how the main agent invokes via `delegate`, why subagents are not containerised today.
3. `docs/channels.md` — **new**, split from the existing `docs/channels/telegram.md`. Channel bidirectional architecture (inbound webhook router + outbound push methods), `channel.yaml` capabilities schema, `CHANNEL.md` format-hint injection, UDS roadmap (future ISSUE-931b3f).
4. `docs/jobs.md` — **full rewrite** with the `dispatch_type` table (currently an inline section added in ISSUE-71e905) promoted to a first-class part of the page structure; retry chain + backoff chain + notify policy documented coherently; `scheduled_jobs:` from toolkit habitats vs explicit `jobs/<name>/template.yaml` files explained side-by-side.

## Description

### Scope
- Four pages authored as a coherent PR — not piecemeal — so cross-references between them can't drift mid-rewrite.
- Update `docs/habitats.md` "Cross-links" table to point at the new dedicated pages (currently points at the placeholder existing pages).
- Update `mkdocs.yml` nav to include `agents.md` and `channels.md` at the kind-level (keep `channels/telegram.md` as a nested example under the new top-level `Channels` entry).

### Pre-existing mkdocs `--strict` warnings — **done, not in this scope**

Originally captured as a task here. Carved out to [[ISSUE-2e903d]] and shipped separately: 10 out-of-`docs/` relative-path warnings in `claude-code-setup.md` and `web.md` were converted to `github.com/shbunder/marcel/...` URLs, and `mkdocs.yml` flipped to `strict: true`. `uv run mkdocs build --strict` is now green on main. New pages authored under this issue must keep it that way.

### Non-scope

- `mkdocs.yml`'s `strict` setting and pre-existing `--strict` warnings — already addressed in [[ISSUE-2e903d]].
- Moving `docs/subagents.md` to `docs/agents.md` via `git mv`. Parallel naming to the other `docs/<kind>.md` pages is the goal, but a rename would break external bookmarks. Prefer: author `docs/agents.md` fresh, leave `docs/subagents.md` as a stub pointing at it.

## Tasks

- [ ] Rewrite `docs/plugins.md` as the toolkit deep dive
- [ ] Author `docs/agents.md` (new, subagent deep dive)
- [ ] Author `docs/channels.md` (new, channel deep dive)
- [ ] Rewrite `docs/jobs.md` with coherent structure around `dispatch_type`
- [ ] Leave `docs/subagents.md` as a stub redirecting to `docs/agents.md`
- [ ] Update `docs/habitats.md` "Cross-links" table to point at the new pages
- [ ] Update `mkdocs.yml` nav to include the new kind-level pages
- [ ] `uv run mkdocs build --strict` still green (pre-existing warnings cleaned up in [[ISSUE-2e903d]]; new pages must not reintroduce them)
- [ ] `make check` green
- [ ] `/finish-issue` → merged close commit on main

## Relationships

- Follows: [[ISSUE-71e905]] (canonical `docs/habitats.md` + cross-reference vocabulary updates — shipped).
- Unblocks: ISSUE-3c1534's Phase 5 alias removal — clean docs (no live `integration habitat` references outside a single back-compat section) are the precondition that Phase 5's straggler grep requires.
