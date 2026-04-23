# ISSUE-5c8831: Per-kind habitat deep-dives (follow-up to ISSUE-71e905)

**Status:** Closed
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

- [✓] Rewrite `docs/plugins.md` as the toolkit deep dive
- [✓] Author `docs/agents.md` (new, subagent deep dive)
- [✓] Author `docs/channels.md` (new, channel deep dive)
- [✓] Rewrite `docs/jobs.md` with coherent structure around `dispatch_type`
- [✓] Leave `docs/subagents.md` as a stub redirecting to `docs/agents.md`
- [✓] Update `docs/habitats.md` "Cross-links" table to point at the new pages
- [✓] Update `mkdocs.yml` nav to include the new kind-level pages
- [✓] `uv run mkdocs build --strict` still green (pre-existing warnings cleaned up in [[ISSUE-2e903d]]; new pages must not reintroduce them)
- [✓] `make check` green
- [✓] `/finish-issue` → merged close commit on main

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

## Implementation Log
<!-- issue-task:log-append -->

### 2026-04-23 18:57 - LLM Implementation
**Action**: Authored new docs/agents.md and docs/channels.md as kind-level deep-dives; rewrote docs/plugins.md as toolkit-only + back-compat section; rewrote docs/jobs.md with dispatch_type as a first-class section; stub docs/subagents.md pointing at agents.md; habitats.md Cross-links + mkdocs.yml nav updated; straggler fixes in skills.md, architecture.md, claude-code-setup.md.
**Files Modified**:
- `docs/agents.md`
- `docs/channels.md`
- `docs/plugins.md`
- `docs/jobs.md`
- `docs/subagents.md`
- `docs/habitats.md`
- `docs/skills.md`
- `docs/architecture.md`
- `docs/claude-code-setup.md`
- `mkdocs.yml`
**Commands Run**: `uv run mkdocs build --strict`, `make check` (both green)
**Result**: Success — 4 pages shipped coherently, `--strict` still green on main.

**Reflection** (via pre-close-verifier):
- Verdict: APPROVE
- Coverage: 9/9 implemented tasks addressed (`/finish-issue` itself is the 10th).
- Shortcuts found: none.
- Scope drift: none. `@register` still appears in `docs/skills.md` lines 18/110/219 — flagged as a minor pre-existing straggler that the writer deliberately left for ISSUE-3c1534 Phase 5 alias removal.
- Stragglers: none. All legacy vocabulary contained in the `plugins.md` "Back-compat aliases" section as designed.
- Spot-checks of source claims (`ChannelPlugin`, `ChannelCapabilities`, `SubagentHabitat.discover_all`, `_marcel_ext_channels`, `backup` sentinel rejection) all accurate against `src/marcel_core/`.
- Non-blocking out-of-scope finding: `docs/habitats.md:111-130` uses `ChannelPlugin(...)` call-style as if the Protocol were constructible — inconsistent with the class-based example in the new `docs/channels.md`. Pre-existing from ISSUE-71e905, not in this diff. Candidate for a follow-up fixup.
<!-- Append entries here when performing development work on this issue -->

## Lessons Learned

### What worked well
- **New pages before rewrites.** Authoring `docs/agents.md` and `docs/channels.md` first meant the `docs/plugins.md` rewrite could simply link into them as "sections moved here → there" — no temporary duplicated content during the transition. The four-page rewrite stayed coherent in one commit because cross-references resolved in the direction of the work.
- **Verifying source claims before the rewrite.** Spot-checking `ChannelCapabilities` fields, `ChannelPlugin` Protocol members, and `CHANNEL.md` wiring in `src/marcel_core/` before writing the new pages caught a false claim up front — the issue body said "CHANNEL.md format-hint injection" as if it were automatic, but the kernel actually reads `<data_root>/channels/<name>.md` + bundled `channel_prompts/<name>.md`. Documenting the real resolution order instead of the aspirational one saved a reviewer from filing a bug against accurate code.
- **Tight Implementation Approach before the first line of prose.** The 7-step outline (new pages → rewrites → habitats.md cross-links + nav) in the plan survived verbatim. When the plan is the page structure, writing is mechanical.

### What to do differently
- **Issue-task log helper needs the sections to exist.** The template embeds `## Implementation Log` / `## Lessons Learned` inside the `markdown` code fence, so `/new-issue`-generated files have them, but issues written by hand (or edited early by Edit) can lack them. When the log helper fails with "section not found", add the two sections from the template and re-run rather than filling in by hand. Cost: +1 Edit.
- **The legacy stub nav entry matters.** `Subagents (legacy): subagents.md` in `mkdocs.yml` nav is what keeps the stub page reachable via the sidebar for users who bookmarked the old URL before the rename. Forgetting this entry would have made the stub discoverable only by direct URL — defeating its reason for existing.

### Patterns to reuse
- **Stub-in-place redirect for renamed pages.** When renaming a doc page to match a taxonomy (here `subagents.md` → `agents.md`), keep the old slug alive as a 3–5 line stub pointing at the new one, and keep a legacy nav entry. Old bookmarks stay live without history rewrites, and `mkdocs --strict` keeps passing.
- **Back-compat section for vocabulary migrations.** Collecting every legacy alias (`integrations/`, `integration.yaml`, `@register`, `integration(id=...)`, `IntegrationHandler`) in one table at the bottom of the canonical page gives Phase 5 alias removal exactly one section to delete — no scavenger hunt across the docs tree.
