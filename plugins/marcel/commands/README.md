# commands/

## `/marcel-adopt [title]`

Hands the current Claude Code session to Marcel (B-14): it shows in the Marcel app as a thread, with
an "adopted" milestone in the main chat, and Marcel follows it from then on. Run it inside the
session you want to hand over.

- **Setup, once per computer.** Make a token on the NUC with
  `docker compose exec hub marcel-hub adopt-token`, then save it here, readable by you only:
  `mkdir -p ~/.marcel && (umask 077 && cat > ~/.marcel/adopt-token)`, paste, Enter, Ctrl-D. In a
  cloud session, set it as the `MARCEL_ADOPT_TOKEN` secret of the environment instead.
- **What happens.** Claude picks a title (yours, or a short line about the session) and runs
  `scripts/adopt.py`, which posts the session's id, folder and host name (or, in the cloud, its
  `session_…` id and link) to `https://marcel-bot.com/api/session/adopt`
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
| `~/.marcel/adopt-token` | — | The token (required, owner-only) |
| `~/.marcel/hub-url` | `https://marcel-bot.com` | Another hub, e.g. a test hub; must be https (owner-only) |
| `MARCEL_ADOPT_TOKEN` (environment) | — | The token itself, only when there is no token file: for a cloud session. It can only go to the hub above |

The command's permission rule lets Claude run exactly
`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/adopt.py --session-id …` without asking, nothing else.

Its tests live in the tools lane (`plugins/marcel/tools/tests/test_adopt_script.py`), whose gate
also lints, type-checks and measures coverage of `scripts/`.
