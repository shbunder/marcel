# SP9 — Workers publish claude.ai artifacts — RESULT

**Verdict: PASS** (steps 1–2 on the NUC, 2026-10-14, Claude Code 2.1.296). Step 3 (opening the link on the iPhone
and a laptop) is the owner's: the link is below.

## What was run

A background session on the NUC, started the way the runner starts a worker: `claude --bg --setting-sources
project,local --settings role_settings/worker.json --model sonnet --permission-mode auto`, in
`~/marcel/workspaces/scratch`, on the owner's Max login, with no browser and nobody watching. Its prompt asked it to
publish a small page with the shared-database capability, write two `stories` rows, update one with `if_version`
from a read, and list the collection.

## Answers

- **Tools present:** `Artifact` and `ArtifactData` (found with `ToolSearch`), and the `artifact-capabilities` and
  `artifact-design` skills. Nothing was missing.
- **No approval:** in `auto` mode nothing asked; the session ran to `done` unattended.
- **Publish:** `Published … at https://claude.ai/artifact/H33nUtv4MC2qfjTiZ3e87M (Version 1)`, capabilities `db`.
- **Rows:** `set` S-1 and S-2 (version 1 each); `get` S-1 (version 1); `update` S-1 to Done with `if_version: 1`
  (version 2); `list` returned both with the new status. The same calls this repo's build board uses.
- **Where the page file lives:** the worker wrote the HTML in its own job folder (`~/.claude/jobs/<id>/tmp/`), which
  a background session may write without asking.

## What changes in the plan

- S-20.2 can stand as written: the Kickoff quest (on the NUC) publishes the project's tracker and updates its rows.
- The probe page `SP9 probe board` stays in the owner's claude.ai artifacts until the owner deletes it.
