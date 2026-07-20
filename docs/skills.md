# Skills

This page covers the **skill habitat** (agent prompting) and its pairing
with the **[connector habitat](connectors.md)** (MCP tools with per-user
auth). See [Habitats](habitats.md) for the full five-kind taxonomy.

A skill teaches the agent *when* and *how* to use tools; the tools
themselves come from connectors. Alongside the connector tools, Marcel
exposes the **`marcel`** utility tool (`search_conversations`, `compact`,
`notify`, `list_models`, `get_model`, `set_model`, `render` — Memory has
its own tools from the Memory capability, FEAT-260718-30d45a) and a
framework-managed **`load_capability`** tool (see
[Deferred-capability disclosure](#deferred-capability-disclosure)).

!!! note "The `toolkit` tool is gone"
    Older skills instructed the model to call
    `toolkit(id="family.action", params={...})`. That dispatcher retired
    with the toolkit habitat (FEAT-260718-c232d9) — skills now instruct the
    model to call a connector's tools directly, by their native MCP names.
    See [Toolkit habitats — retired](plugins.md).

Skill documentation lives in `<root>/skills/<name>/SKILL.md`. A skill is a
folder with a `SKILL.md` following the [agentskills.io](https://agentskills.io)
open standard. Each visible skill becomes a **deferred pydantic-ai
Capability** rather than being pasted wholesale into the system prompt — see
[Deferred-capability disclosure](#deferred-capability-disclosure).

### Where skills are discovered — the three-root chain

Skills are collected **per user** from three roots, in order of increasing
specificity. A skill named the same in a more-specific root **shadows** the
same-named skill in a less-specific one (the shadowing is logged):

1. **`<MARCEL_ZOO_DIR>/skills/`** — global habitats shared by every user
   (source `zoo-global`; skipped when `MARCEL_ZOO_DIR` is unset).
2. **`<MARCEL_ZOO_DIR>/users/<slug>/skills/`** — git-managed per-user
   habitats (source `zoo-user`).
3. **`<MARCEL_DATA_DIR>/users/<slug>/skills/`** — runtime-installed
   per-user skills (source `data-user`).

There is no global `<MARCEL_DATA_DIR>/skills/` root — global skills come from
the zoo. To override a zoo-shipped skill for one user, drop a same-named
skill into that user's `zoo-user` or `data-user` root; being more specific,
it wins. [Connectors](connectors.md) resolve through the same three-root
chain, so a skill and the connector it fronts can ship side by side at any
scope.

## How it works

1. The agent receives a user request (e.g. "what's on my calendar?").
2. Its prompt carries a compact **catalog** of the available skills (each
   skill's name + description, ~100 tokens each) — not the full bodies.
3. It calls `load_capability("icloud")` to pull the `icloud` skill's
   `SKILL.md` body into context as that capability's instructions. Because
   the skill names the `icloud` connector in `metadata.marcel-connectors`,
   the connector's tools activate in the same step.
4. It calls the connector's calendar tool directly — a native MCP tool,
   running against *this user's* credentials.
5. The result is returned to the agent, which answers the user.

## Deferred-capability disclosure

Skills are exposed to the model as **deferred pydantic-ai Capabilities**
(`defer_loading=True`). The framework does not paste every skill body into
the prompt. Instead:

- The prompt carries a compact **catalog** — one entry per visible skill,
  each being the skill's id (its `name`) plus its `description` (~100 tokens
  each).
- The framework adds a managed **`load_capability`** tool. When the model
  calls `load_capability("<skill-name>")`, that skill's `SKILL.md` body is
  returned as the capability's instructions and stays in the message history
  (surviving compaction). Connectors named in the skill's
  `marcel-connectors` metadata activate their tools in the same step.
- Each loaded skill owns a **`read_skill_resource(resource)`** tool scoped to
  its own directory. This tool only appears in the tool list *after* the
  skill is loaded, and only reads files inside that skill's folder (e.g.
  `feeds.yaml`, `components.yaml`, `SETUP.md`).

There is no `# Skills — what you can do` block in the system prompt — the
model loads a skill via `load_capability` before using the tools it fronts.
A `/<skillname>` slash command force-loads that skill's capability eagerly,
so its body is in the prompt from the first request.

### Role gating

A skill whose frontmatter sets `metadata.marcel-role: admin` is dropped from
the catalog for non-admin users — never shown, never loadable. Skills
without a `marcel-role` are visible to everyone. (Connectors have their own
`scope:` gate — see [Connectors](connectors.md).)

## Frontmatter — the agentskills.io standard

A skill's `SKILL.md` opens with YAML frontmatter conforming to the
[agentskills.io](https://agentskills.io) open standard. Exactly two keys are
required:

| Key | Rules |
|---|---|
| `name` | 1–64 chars, lowercase alphanumerics and single hyphens. **Must equal the skill's directory name.** |
| `description` | 1–1024 chars. Shown in the skill catalog; this is what the model reads to decide whether to load the skill. |

Any other **top-level** frontmatter key is tolerated but ignored. Marcel's
own extensions never sit at the top level — they live in the spec-legal
`metadata:` map (a flat string→string map) under `marcel-*` keys:

| `metadata` key | Value (string) | Purpose |
|---|---|---|
| `marcel-connectors` | comma-separated connector names, e.g. `banking,news` | The [connector](connectors.md) habitats this skill fronts. Each named connector activates *with* the skill (its tools ride along in the same `load_capability` step). A discoverable connector counts as satisfied — an unlinked one degrades on its own to a readable "needs setup" entry. A name that resolves to no connector means the skill serves `SETUP.md`. |
| `marcel-tier` | `fast` \| `standard` \| `power` \| `local` | Preferred model tier while this skill is loaded (see [Model tiers](model-tiers.md)). |
| `marcel-default-enabled` | `all` \| `admin` \| `none` | Who a fresh [marketplace install](marketplace.md) seeds the skill's enablement for. Absent means `all`. Seeding only until per-user enforcement lands (FEAT-260707-acb2b6). |
| `marcel-role` | `admin` | Role-gates the skill — dropped from the catalog for non-admin users. |
| `marcel-requires-credentials` | comma-separated keys, e.g. `MY_API_KEY` | Credentials that must exist in the user's store. |
| `marcel-requires-env` | comma-separated vars, e.g. `SOME_ENV_VAR` | Environment variables that must be set. |

Example — a skill that fronts the `banking` connector:

```yaml
---
name: banking
description: Access the user's linked bank accounts — balances, transactions, spending insights
metadata:
  marcel-connectors: banking
---
```

!!! note "Legacy keys — deprecated but tolerated"
    Older skills declared `depends_on:` (a YAML list), `preferred_tier:`, and
    a `requires:` block (`{role, credentials, env}`) as **top-level** keys.
    The loader still migrates these — emitting a deprecation warning — but new
    and migrated skills use `metadata.marcel-*`. The mapping:

    | Legacy top-level key | New `metadata` key |
    |---|---|
    | `depends_on: [banking]` | `marcel-connectors: banking` |
    | `preferred_tier: power` | `marcel-tier: power` |
    | `requires: {role: admin}` | `marcel-role: admin` |
    | `requires: {credentials: [...]}` | `marcel-requires-credentials: ...` |
    | `requires: {env: [...]}` | `marcel-requires-env: ...` |

## The skill shapes

A skill's frontmatter determines how the loader treats it and whether a
`SETUP.md` fallback is meaningful. There are three distinct shapes:

### 1. Standalone — pure teaching material, no requirements

The simplest shape: no `marcel-connectors`, no `marcel-requires-*`. The
`SKILL.md` body is served whenever the skill is loaded because it documents
built-in kernel tools (e.g. the `web`, `memory`, or `jobs` utility action
families) or pure domain knowledge. No `SETUP.md` is needed because there is
nothing to set up.

```yaml
---
name: memory
description: Manage conversation memory — search past conversations, recall facts
---
```

Standalone skills live in `<MARCEL_ZOO_DIR>/skills/<name>/` alongside every other skill habitat. The kernel ships none of them by default.

### 2. Self-contained — `marcel-requires-*`

A skill that needs credentials or environment variables but fronts no
connector — for instance, a skill that teaches the agent how to use a
tool whose credentials are read directly at call time. Declare the
dependencies in `metadata`; they drive SKILL.md → SETUP.md switching.

```yaml
---
name: myservice
description: What myservice does
metadata:
  marcel-requires-credentials: MY_API_KEY
  marcel-requires-env: SOME_ENV_VAR
---
```

### 3. Connector-backed — `marcel-connectors`

The typical case for any skill that fronts external tools. Rather than
duplicating requirements, name the connector(s) the skill fronts:

```yaml
---
name: docker
description: Manage Docker containers
metadata:
  marcel-connectors: docker
---
```

The loader resolves each name against the user's connector catalog. The
skill's aggregated credential keys come from each connector's
`auth.credential_keys` in its `connector.yaml` — the credential list lives
in one place, the connector manifest, and never drifts from the skill doc.
See [Connectors → The manifest](connectors.md#the-manifest).

A *discoverable* connector counts as satisfied even before the user links
an account: an unlinked connector already degrades on its own — its
capability becomes a tool-less "needs setup" entry — so it does not push
the skill into setup mode. Only a name that resolves to **no** connector
(zoo not loaded, park missing or malformed, or scope-filtered for this
user) makes the skill serve `SETUP.md`.

Forms combine: a skill's effective requirements are the union of its
`marcel-requires-*` keys and its connectors' resolvability.

## Skill fallback (SETUP.md)

A skill's `SETUP.md` (shapes 2 and 3) activates when the skill's requirements
are not met. This guides new users through first-time setup rather than
failing silently. Standalone skills (shape 1) do not need a `SETUP.md`.

When all requirements are met — or there are none — loading the skill serves
`SKILL.md`. When any are missing — a missing credential or env var, or a
`marcel-connectors` name that resolves to no connector — loading it serves
`SETUP.md` instead, and the skill's catalog description is suffixed
"— needs setup".

## Pairing a skill with a connector

The two-habitat pattern most real features use: a
[connector park](connectors.md) holds the MCP server and its auth manifest;
the paired skill park teaches the agent when to reach for it.

`<MARCEL_ZOO_DIR>/skills/myservice/SKILL.md`:

```markdown
---
name: myservice
description: Short description of what myservice does
metadata:
  marcel-connectors: myservice
---

# myservice

When the user asks about ..., use the myservice tools.

## Available tools

### status

Reports the current myservice state. Call it with no arguments before
any mutating operation.

### act

Performs the action. Takes `target` (string, required) and `dry_run`
(boolean, default false).
```

And a setup fallback at `<MARCEL_ZOO_DIR>/skills/myservice/SETUP.md`:

```markdown
---
name: myservice
description: Guide the user through setting up myservice
---

The user is asking about myservice, but it is **not yet configured**.

## How to set up myservice

[Step-by-step instructions for the user...]
```

No changes to kernel code are needed — the skill habitat is loaded from
`<MARCEL_ZOO_DIR>/skills/` automatically, the connector resolves per user
at capability-build time, and loading the skill activates the connector's
tools in the same step. Writing the connector itself — the manifest, the
server, the auth block — is covered in [Connectors](connectors.md).

## The marcel tool contract

The `marcel` tool provides internal utilities via action-based dispatch.

| Argument | Type | Required | Description |
|---|---|---|---|
| `action` | string | yes | One of: `search_conversations`, `compact`, `notify`, `list_models`, `get_model`, `set_model`, `render` |
| `query` | string | for `search_*` | Search query — matches filenames, frontmatter fields, and body content |
| `message` | string | for `notify` | Short plain-text progress update |
| `max_results` | int | no | Maximum results (default: 10 for memory, 5 for conversations) |

!!! warning "Retired actions"
    `read_skill` and `read_skill_resource` were removed with Skills v2.
    Calling either now returns a short message redirecting to
    `load_capability` (see
    [Deferred-capability disclosure](#deferred-capability-disclosure)).
    A loaded skill's resources are read via that capability's own scoped
    `read_skill_resource(resource)` tool — not via the `marcel` tool.

The `list_models` / `get_model` / `set_model` actions are documented in
[Model tiers](model-tiers.md); `render` in
[A2UI Components](a2ui-components.md).

### search_conversations

Searches past conversation segments by keyword. Returns matching messages with surrounding context.

### compact

Compresses the current conversation segment into a summary and opens a fresh segment.

### notify

Sends a short progress update to the user. On Telegram, this sends a real-time message. On other channels, it returns `"ok"` (the user sees streaming output).

The agent should call `notify` at the start of any multi-step task and after each major step.
