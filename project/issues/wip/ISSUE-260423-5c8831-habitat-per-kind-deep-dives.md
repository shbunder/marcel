# ISSUE-5c8831: Per-kind habitat deep-dives (follow-up to ISSUE-71e905)

**Status:** WIP
**Created:** 2026-04-23
**Assignee:** Claude
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

## Implementation Approach

Four pages shipped coherently in one branch so cross-references cannot drift mid-rewrite. Order: new pages first (so the rewrites can cross-link into them), then rewrites, then the nav + habitats.md cross-link update.

**1. `docs/agents.md` (new)** — kind-level deep dive. Covers: what *is* a subagent habitat, directory layout (`<zoo>/agents/<name>.md` and `<data_root>/agents/<name>.md` with data-root-wins override), frontmatter schema (name/description/model/tools/disallowed_tools/max_requests/timeout_seconds plus clawcode aliases), model tier sentinels (`fast`/`standard`/`power`/`fallback`), tool allowlist and admin-vs-user role gating, discovery mechanics, default subagents shipped in the zoo (`explore`/`plan`/`power`), recursion guard. Invocation mechanics (the `delegate` tool contract, cost/safety, scope limits v1) also land here since `docs/subagents.md` becomes a stub.

**2. `docs/channels.md` (new)** — kind-level deep dive. Covers: bidirectional architecture (inbound FastAPI router + outbound `send_message` / `send_photo` / `send_artifact_link`), the `ChannelPlugin` Protocol from `marcel_core.plugin.channels`, `channel.yaml` schema, `CHANNEL.md` format-hint injection (search for usage in the kernel — it's referenced in plugins.md today), `ChannelCapabilities` fields (`markdown` / `rich_ui` / `streaming` / `progress_updates` / `attachments`), discovery + error isolation, `_marcel_ext_channels.<name>` private-namespace loading + relative-imports requirement, kernel-native surfaces (websocket/cli/app/ios/macos) vs habitat transports, UDS roadmap (future ISSUE-931b3f). Telegram stays as the sole concrete example under a nested nav entry.

**3. `docs/plugins.md` rewrite** — collapse to toolkit-only deep dive. Today it's the original umbrella page that also documents channel/job/subagent habitats. After this rewrite: `docs/plugins.md` = toolkit habitat + the shared `marcel_core.plugin` API surface (`credentials` / `paths` / `models` / `rss`). Sections removed (now live on their own pages): "Channel habitat" → `channels.md`; "Job habitat" (the template-file discussion) → `jobs.md` templates section; "Subagent habitat" → `agents.md`. Historical aliases (`integration`, `@register`, `integration.yaml`, `integrations/`) live in a single bottom "Back-compat" section. The `scheduled_jobs:` block stays — it's the toolkit → job surface — positioned as a subsection of the toolkit schema with a prominent cross-link to `jobs.md`.

**4. `docs/jobs.md` rewrite** — promote `dispatch_type` from its current mid-page "## Dispatch type" section to a first-class part of the page structure. New outline:
   1. Concept (what is a job, headless-turn model, system-scope vs per-user)
   2. Storage layout (JOB.md + state.json + runs/)
   3. Triggers (the `TriggerSpec` table with cron/interval/event/oneshot, timezone handling)
   4. Dispatch types (the `agent` / `tool` / `subagent` table, validator rules, per-dispatch-type behavior, retry semantics)
   5. Executor (retry chain with error classification, backoff schedule, notify policy enforcement)
   6. Templates vs `scheduled_jobs:` (side-by-side — templates are conversational starting points, `scheduled_jobs:` in a toolkit is declarative always-on work)
   7. Agent tools
   8. Legacy layout migration (kept at the bottom; it's transitional)

**5. `docs/subagents.md` stub** — 3–5 lines. Title + one-sentence explanation + link to `docs/agents.md`. Old external bookmarks to `subagents.md` still resolve; mkdocs still renders the page.

**6. `docs/habitats.md` cross-link table** — currently points Subagent → `subagents.md`, Channel → `channels/telegram.md`. Update to `agents.md` and `channels.md` respectively. Also update the `discover_all` placeholder links at the top of the page — they currently say "(The `discover_all` links are placeholders until the per-kind deep-dives land)" and this is the issue that lands them.

**7. `mkdocs.yml` nav** — replace `Subagents: subagents.md` with `Agents: agents.md`; promote `Channels:` to a group with an `Overview: channels.md` entry plus the existing `Telegram: channels/telegram.md` nested.

**Verification:**
- `uv run mkdocs build --strict` green (no warnings — `strict: true` landed in ISSUE-2e903d).
- `make check` green — no code touched, but any mkdocstrings auto-rendered block in a rewritten page must still resolve.
- Grep for `subagents.md` across `docs/`, `README.md`, `CLAUDE.md`, `.claude/`, and the zoo checkout (`~/.marcel/` or `$MARCEL_ZOO_DIR`) — every link must still work (the stub keeps the old URL alive).
- Grep for `integration habitat` / `integrations/` / `@register` / `integration.yaml` in `docs/` — these should only appear inside the plugins.md "Back-compat" section.

**Files touched (kernel only — no zoo changes):**
- `docs/agents.md` — new
- `docs/channels.md` — new
- `docs/plugins.md` — rewrite
- `docs/jobs.md` — rewrite
- `docs/subagents.md` — stub
- `docs/habitats.md` — cross-link table + placeholder note removal
- `mkdocs.yml` — nav
