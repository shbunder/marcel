# Installing skills and connectors

Marcel's habitats move through a **three-state lifecycle**
(ADR-260718-7addc8). Each state answers a different question, owned by a
different party:

| State | Question | Owned by | Where it lives |
|---|---|---|---|
| **Available** | Does it exist somewhere we trust? | The source curator | `<zoo>/sources.yaml` |
| **Installed** | Is it in this household's Marcel? | The admin | A git commit in the zoo repo |
| **Enabled** | Can *this* family member see it? | Household policy | `<data_root>/enablement.yaml` |

Installed ≠ visible: one banking connector installed once can be enabled for
the two adults and invisible to the kids. On top of enablement sits per-user
*configuration* — an enabled-but-unlinked connector serves its SETUP flow
instead of tools.

!!! note
    This page covers **available → installed** (the marketplace,
    FEAT-260718-210a5f). Enablement *enforcement* — actually filtering
    catalogs by the manifest — lands with FEAT-260707-acb2b6; until then the
    manifest is seeded but advisory.

## Trusted sources — `sources.yaml`

The registry lives in the zoo repo and is itself admin surface — editing it
is a zoo commit. Three source types:

```yaml
sources:
  - name: anthropic-skills
    type: agentskills-git          # a git repo of agentskills-format skills
    url: https://github.com/anthropics/skills
    ref: main                       # optional pin (branch / tag / sha)
    subdir: skills                  # optional path inside the repo
    description: Anthropic's curated public skills
  - name: mcp-registry
    type: mcp-registry              # read-only HTTP catalog of MCP servers
    url: https://registry.modelcontextprotocol.io
    description: The official MCP server registry
```

`plugin-marketplace` sources (Claude-plugin-style repos with a
`.claude-plugin/marketplace.json`) are also supported; their plugins'
`skills/` folders are the installable units. Git sources take `https://`
URLs or absolute local paths (household mirrors, fixtures); plain `http://`
is refused everywhere.

## The flows

Everything is admin-only — conversationally via the `marketplace` tool, or
from the terminal via make targets:

```bash
make marketplace-sources
make marketplace-browse SOURCE=anthropic-skills
make marketplace-install SOURCE=anthropic-skills NAME=slack-gif-creator
make marketplace-update KIND=skill NAME=slack-gif-creator
make marketplace-remove KIND=skill NAME=slack-gif-creator
```

**Browsing is read-only**: candidates are listed by parsing text
(frontmatter, JSON catalogs) — nothing from a source is imported or executed,
and nothing touches the zoo or data roots.

**Every install is reviewed and git-committed.** The flow is two-step: a
*review* renders a human-readable summary (name, description, source and
pinned ref, file inventory, a ⚠️ when the candidate ships scripts) and mints
a token bound to that exact candidate state; the *install* requires the token
back. A source that moved since review invalidates the token — you re-review
what will actually land. The candidate is then validated in a staging
directory (skills-v2 frontmatter validator / connector schema — an invalid
habitat aborts with the violated rule named, leaving nothing behind), placed
(global by default, `users/<slug>/` on request), and committed to the zoo
repo with named files. **The habitat is never discoverable before the commit
lands** — and `git revert` of that commit is a clean uninstall.

Installing a skill is installing software. Read what the review shows you —
especially when it warns about scripts.

**Updates respect household edits.** Installs record provenance
(`.marcel-provenance.yaml`: source, pinned ref, per-file hashes). The update
review compares hashes first — any local modification aborts loudly rather
than overwriting your household's changes — then shows what changed upstream
before asking for confirmation.

**Removal cleans up**: folder deleted, removal commit, enablement-manifest
entry dropped.

**MCP-registry installs** write a `connector.yaml` pointed at the server's
declared remote endpoint (http transport). Registry entries that only ship
runnable packages are refused — running arbitrary packages is beyond what a
review gate can make safe. Auth material is never fetched: after install,
the connector goes through the normal [SETUP flow](connectors.md).

Every review and every mutation is appended to the audit log
(`<data_root>/approvals/audit.jsonl`) with source, ref and commit SHA.

## Enablement seeding

Installs seed `<data_root>/enablement.yaml` from the habitat's declaration —
skills `metadata: {marcel-default-enabled: all|admin|none}`, connectors
`default_enabled:` in `connector.yaml`; absent means `all`:

- `all` — nothing written (an absent entry already means everyone).
- `admin` — the *current* admin slugs are written as a named list.
- `none` — an empty list: installed, visible to nobody until enabled.

## A worked household example

Shaun (admin) hears about a public transit skill:

1. *"Marcel, what's in the anthropic-skills source about transit?"* — browse
   lists `nmbs-departures`, no scripts, `marcel-default-enabled: all`.
2. *"Show me the review."* — Marcel renders the summary: source, ref
   `a1b2c3d4e5f6`, three files. Shaun says install.
3. The skill validates, lands at `<zoo>/skills/nmbs-departures/`, and the
   zoo gets commit `marketplace: install skill nmbs-departures from
   anthropic-skills@a1b2c3d4e5f6`. Default `all` — the whole family sees it.
4. Next week Shaun installs `devops-dashboard` (`marcel-default-enabled:
   admin`): same flow, but the manifest gains `devops-dashboard: [shaun]` —
   the kids' catalogs never carry it.
5. The transit skill misbehaves after a fridge-magnet incident involving a
   six-year-old and the zoo laptop: `git log` shows exactly what changed,
   and `git revert` of the install commit uninstalls it cleanly.

## See also

- [Skills](skills.md) — the skills-v2 format installs are validated against.
- [Connectors](connectors.md) — the connector schema + SETUP flow.
- [Habitats](habitats.md) — the five-kind taxonomy.
