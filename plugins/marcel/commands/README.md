# commands/

## `/marcel-adopt [title]`

Hands the current Claude Code session to Marcel (B-14): it shows in the Marcel app as a thread, with
an "adopted" milestone in the main chat, and Marcel follows it from then on. Run it inside the
session you want to hand over.

- **Setup, once per computer.** Make a token on the NUC with
  `docker compose exec hub marcel-hub adopt-token`. It prints an adopt code, `mra_…@https://<your
  Marcel>`: the token bound to the address of the hub that issued it. Save it here, readable by you
  only: `mkdir -p ~/.marcel && (umask 077 && cat > ~/.marcel/adopt-token)`, paste, Enter, Ctrl-D.
  In a cloud session, set the code as the `MARCEL_ADOPT_TOKEN` secret of the environment instead.
- **What happens.** Claude picks a title (yours, or a short line about the session) and runs
  `scripts/adopt.py`, which posts the session's id, folder and host name (or, in the cloud, its
  `session_…` id and link) to `<your Marcel's address>/api/session/adopt`, the address saved in
  the adopt code (or, for a bare token from before, `~/.marcel/hub-url`)
  (`contracts/session-api.yaml`). The session id comes from `${CLAUDE_SESSION_ID}` in the command,
  else from `CLAUDE_CODE_SESSION_ID`; a cloud session is recognised by `CLAUDE_CODE_REMOTE` and its
  id read from `CLAUDE_CODE_REMOTE_SESSION_ID`.
- **Live or links only.** A session on the NUC is followed live: transcript and state. Anywhere
  else Marcel keeps the task and its link only.
- **Running it twice** changes nothing: the hub answers with the same task.

## `scripts/adopt.py`

Standard library only: it runs in any session, on any computer. It never prints the token, talks
only HTTPS, follows no redirects and gives up after 15 seconds. Every outcome is one line starting
with what happened ("Marcel has taken over …", "Not adopted. …").

**Where the token goes is not up to the environment.** A cloned repository's
`.claude/settings.json` can set environment variables for every command Claude runs. So the script
reads its settings only from files in `~/.marcel/`, with `~` taken from the account's password
entry, not `$HOME`, and uses each file only when it belongs to you and nobody else can read it
(fix: `chmod 600 <file>`).

| Setting | Default | What for |
|---|---|---|
| `~/.marcel/adopt-token` | — | The adopt code `mra_…@https://<hub>` (required, owner-only). The token goes only to the hub in the code |
| `~/.marcel/hub-url` | — | Only for a bare `mra_…` token from before S-22.2: your Marcel's address (owner-only, https). The install and `marcel connect` write it; with neither, the command says "Run `marcel connect` first" |
| `MARCEL_ADOPT_TOKEN` (environment) | — | The adopt code itself, only when there is no token file: for a cloud session. The address comes only from the code, so a repository that sets this variable replaces the token too and can never redirect yours |

The command's permission rule lets Claude run exactly
`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/adopt.py --session-id …` without asking, nothing else.

Its tests live in the tools lane (`plugins/marcel/tools/tests/test_adopt_script.py`), whose gate
also lints, type-checks and measures coverage of `scripts/`.
