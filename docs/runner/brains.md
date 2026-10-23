# Runner — a brain per buddy (`POST /brains`)

The hub runs in Docker and cannot render `brain/` or trust a folder, so before it starts a second
buddy's brain it asks the runner, on the host (S-17.7, `contracts/runner-api.yaml` `prepareBrain`).
Code: `runner/src/marcel_runner/brains.py`.

`POST /brains` `{agent_slug, name, persona: {person_name, personality?, animal?}}` makes `~/marcel/brains/<slug>`
ready and answers `{agent_slug, cwd, memory_dir}`:

1. `brain/render.py` renders the template there: `AGENT_NAME` = `name`, `OWNER_NAME` = the person,
   `PERSONALITY` = the look's personality (default `professional`), `ANIMAL` = the look's animal (S-17.8;
   left out for a giraffe or none, so the template's "pink giraffe", the owner's look, stays; any animal
   outside the contract's list is a 400), `MARKETPLACE_DIR` = the deployed
   marketplace, `MEMORY_DIR` = `agents/<slug>/` in the memory repo. `MEMORY.md` and `memory/` link there;
   the render makes them empty when missing and never overwrites them.
2. `claude plugin install marcel@marcel-local --scope project` in the folder, unless Claude Code already
   lists it installed there.
3. The trust dialog, accepted the way the runner does it for a clone, unless the folder is trusted.

Safe to repeat. `agent_slug` is lowercase letters, digits and dashes, so it never leaves `~/marcel/brains`.

| Setting | Default | What |
|---|---|---|
| `MARCEL_BRAINS` | `~/marcel/brains` | Where the brain folders go. |
| `MARCEL_MEMORY_ROOT` | `~/marcel/data/memory` | The hub's memory folder on the host. |
| `MARCEL_MARKETPLACE_DIR` | `~/marcel/plugins` | The deployed `marcel-local` marketplace. |

| Error | Status | When |
|---|---|---|
| `bad_request` | 400 | The body is off the contract; nothing is made. |
| `render_failed` | 422 | The template refused a value, or a real file sits where a memory link goes (`detail`). |
| `clone_failed` | 422 | The plugin install failed (`detail` has Claude's words). |
| `not_trusted` | 422 | The trust dialog could not be accepted. |
| `claude_unavailable`, `claude_timeout` | 503 | `claude` is missing, or a step took too long. |

`ANIMAL` is not passed yet: `PrepareBrain` has no animal (contract request
`project/contract-requests/S-17.7.md`), so a second buddy's brain calls itself a pink giraffe while the app
draws its real look.

The owner's first agent keeps `~/marcel/brain` (`MARCEL_BRAIN_CWD`), rendered by `make -C deploy install`
with its memory linked to `agents/marcel/`.
