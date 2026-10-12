# brain/ — the brain workspace template

Rendered to `~/marcel/brain` on the NUC, where the hub has the runner start the brain session
(`role: brain`, `cwd` = that folder, Opus, `auto`, `--channels plugin:marcel@marcel-local`; see
`contracts/runner-api.yaml`). Side threads are forks of it and share the same workspace.

| File | What it is |
|---|---|
| `CLAUDE.md` | Marcel's persona and hard rules. Short on purpose: how to handle a message lives in the plugin's `orchestrate` skill, which it points at. |
| `.claude/settings.json` | Opus, `auto` permission mode, the `marcel-local` marketplace and the `marcel@marcel-local` plugin, cross-session messages refused (ADR-003), and restarts denied. |
| `render.py` | Fills the deploy variables and makes the memory links. Standard library only. |
| `tests/` | The gate: the template renders, the settings validate, the persona names tools exactly. |

## Deploy variables

Defined once, in `VARIABLES` in `render.py`; this table says the same.

| Variable | Default | What it is |
|---|---|---|
| `AGENT_NAME` | `Marcel` | The agent's name, as the family calls it. |
| `OWNER_NAME` | — | The owner's first name; Marcel addresses them by it. |
| `MARKETPLACE_DIR` | — | Absolute path of the deployed `marcel-local` marketplace: a folder with `.claude-plugin/marketplace.json` listing a released copy of `plugins/marcel/` (never a working tree: a directory plugin runs in place, SP1). |
| `MEMORY_DIR` | — | Absolute path, on the host, of the hub's memory folder (`<data_dir>/memory`). The hub's `/data` must be a bind mount of a host folder, not a named volume, for the brain to reach it. |

```bash
python3 brain/render.py --out ~/marcel/brain \
  --var OWNER_NAME=Shaun \
  --var MARKETPLACE_DIR=$HOME/marcel/plugins \
  --var MEMORY_DIR=$HOME/marcel/data/memory
```

It refuses, in one sentence, an unknown or missing variable, a relative path, a placeholder left in the
output, or a real file where a memory link should go. Rendering again is safe: it rewrites the two
template files and replaces its own links.

## Memory

The hub keeps memory in a git working copy at `<data_dir>/memory` (`hub/src/marcel_hub/memory.py`); its
git database sits next to it, out of the brain's reach. The workspace links into it:

```
~/marcel/brain/MEMORY.md  →  $MEMORY_DIR/MEMORY.md     read at the start of every session
~/marcel/brain/memory/    →  $MEMORY_DIR/memory/       dated notes, memory/projects/<slug>.md
```

The brain reads them directly. It saves through `memory_write`, so each save is one commit with an
etag check; an edit made straight in the file is picked up by the next etag.

## After rendering (S-14.1)

Deploy still installs the plugin into Claude Code's plugin cache, project-scoped, which keeps the same
two settings entries: `claude plugin install marcel@marcel-local --scope project` in `~/marcel/brain`, and
trusts the folder once.

## Development

```bash
make -C brain check   # ruff, pyright, pytest with coverage ≥ 90 %
```

The persona test reads the brain tools from `plugins/marcel/tools/src` and the runner's allowed tools
from `contracts/runner-api.yaml`, so renaming a tool turns this gate red.
