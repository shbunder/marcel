<div align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/design/logo-text-white.png" />
    <source media="(prefers-color-scheme: light)" srcset="docs/design/logo-text-black.png" />
    <img src="docs/design/logo-text-black.png" alt="Marcel" width="90%" />
  </picture>
</div>

# 🦒 Marcel

Meet Marcel, your friendly family Giraffe. Marcel is very smart 🤓 and can help with all kinds of tasks, from doing homework 🗒️ for the kids to alerting parents that rent is due 💰. Marcel can help with planning activities 📆 or warn you that you have dinner with the in-laws this Friday 😱. Don't ask Marcel to do the dishes though, he's lazy and well... he has no body. Marcel is there to help the family, because, let's be honest, would you really trust these tasks to a lobster 🦞?

Can Marcel really do all of the above? Depends... Marcel is mainly a hobby project to test the limits of vibe-coding and agentic-design.
The main goal of this project is to envision how a truly helpful AI assistent for a household would look like. Secondary this author wants to see how he can safely expose AI technology to his young children. And lastly we want to make the system spouse-proof, which is a challenge on its own. 

The ultimate goal is to see how flexible we can make Marcel to perform real day-to-day tasks; like setting reminders, remembering certain things, performing tasks, whatever you can imagine. We assume that to achieve this flexibility Marcel should be able to (partly) rewrite its own codebase.
As such the author basically wants to see how feasible such a setup is by experimenting on his family 🤐.

## Vision

Marcel is meant to run centrally on a home server, which means it requires a tech-savvy zoo keeper. This enables the family to have full control over their new pet. Just watch out with the police, I'm not sure keeping a pet Giraffe is allowed in every country. Ask Marcel for the legal implication. 

Family members can interact with Marcel through traditional chat channels like Telegram. The result of using this means of communication is that Marcel works with one continuous conversation per channel, instead of being a multi-session agent. 

Ultimately Marcel should feel like just another member of the family, a brother maybe, that’s always locked-up in his room and that you never see at the dinner table. 

## Who is it for?

Marcel is built around two very different kinds of people:

| Role | What they do | What they need |
|------|-------------|----------------|
| 🛠️ **Zoo keeper** (one per household) | Installs Marcel on a home server once, creates accounts for family members, hooks up services like iCloud or Telegram, keeps an eye on the logs. | Comfortable with a terminal, Docker, a `.env` file, and reading the occasional stack trace. |
| 👨‍👩‍👧 **Family members** | Chat with Marcel from Telegram on their phone, or from [marcel](src/marcel_cli/) in a terminal. Ask him things, get things done. | Nothing technical — Marcel is just another contact on Telegram. |

In other words: one person sets Marcel up, everyone else just talks to him. If nobody in your household wants to play zoo keeper, Marcel is not (yet) for you.

## Setting up Marcel

Setup happens in three phases: **stand up the server**, **onboard a family member**, **teach Marcel a new trick**. Everything uses `make` targets from the repo root so there is only one place to look.

### Phase 1 — Stand up the server (zoo keeper, once)

Marcel runs in Docker, managed by a user-level systemd unit so it survives reboots without needing root. The one-shot setup:

```bash
git clone https://github.com/shbunder/marcel.git
cd marcel
nano .env                   # fill in ANTHROPIC_API_KEY (and optionally the rest)
make setup-check            # verify prerequisites without touching anything
make setup                  # build the image, install systemd, start the container
```

`make setup` checks prerequisites (Docker, Docker Compose, docker group, systemd linger), renders the systemd unit templates, builds the image, starts the service, and waits for `/health` on [http://localhost:7420](http://localhost:7420) to come back green. If anything is missing it tells you exactly what to fix. For security hardening (API token, credential encryption key, Telegram webhook secret) see [SETUP.md](SETUP.md) — that's the extended walkthrough; this section is the happy path.

Day-to-day operations after that:

```bash
make docker-logs            # tail the container logs
make docker-restart         # rebuild and redeploy (with rollback on failure)
make teardown               # stop Marcel and remove systemd units
```

### Phase 2 — Onboard a family member

Every person Marcel talks to is a **user** — a directory under `~/.marcel/users/<slug>/` with a `profile.md`, memories, and conversation history. The slug is lowercase (letters, digits, `-`, `_`); it's what Marcel writes down next to a fact he learns about that person.

```bash
make add-user USER=alice                # regular user
make add-user USER=shaun ROLE=admin     # admin (unlocks bash, git, self-modification tools)
```

From there, the family member needs a way to actually talk to Marcel. Pick one (or both):

**Option A — Telegram** *(recommended for non-technical users)*. Marcel identifies a chat by its Telegram chat ID, which is stored in the user's `profile.md` frontmatter. The link is one make target away:

1. Zoo keeper sets `TELEGRAM_BOT_TOKEN` and `TELEGRAM_WEBHOOK_SECRET` in `.env.local` (see [SETUP.md](SETUP.md#step-6-set-up-telegram-optional) for BotFather + webhook registration).
2. Family member opens Telegram, messages your bot, sends `/start`. The bot replies with their chat ID.
3. Zoo keeper runs:
   ```bash
   make link-telegram USER=alice CHAT=123456789
   ```
4. Alice sends her next message and Marcel knows who she is — from that point on every message on that Telegram chat is routed to her user directory.

The link is bidirectional and exclusive: one Telegram chat maps to one Marcel user, and vice versa. If a chat ID arrives that isn't linked, Marcel replies with a "you're not linked yet" message instead of leaking into a random account.

**Option B — Native CLI**. The [marcel](src/marcel_cli/) binary is a single ~3.6 MB Rust TUI (ratatui + crossterm) that streams responses over WebSocket. On the family member's machine:

```bash
./scripts/install.sh --host 192.168.1.50 --port 7420 --user alice
marcel
```

That builds and installs the binary to `~/.cargo/bin` and writes `~/.marcel/config.toml` with the server address and their user slug. The config also carries the `MARCEL_API_TOKEN` that must match the server's token.

### Phase 3 — Teach Marcel new tricks

Everything Marcel can *do* — check iCloud calendar, read bank balances, fetch RSS, browse the web — is a **skill**. Skills live in `~/.marcel/skills/<name>/` and consist of two markdown files:

- `SKILL.md` — what the skill is, what it exposes, how to call it. Injected into Marcel's system prompt.
- `SETUP.md` — shown *instead of* `SKILL.md` when the skill's declared requirements (credentials, env vars, files) aren't met. This is how the agent learns to onboard its own integrations: when Alice asks about her calendar and iCloud isn't configured, Marcel reads `SETUP.md` and walks her through it.

Adding a service is almost always conversational: Alice says *"I want you to read my iCloud calendar"*, Marcel loads the `icloud` skill's `SETUP.md`, asks for the credentials, stores them encrypted under her user directory, and from then on has the skill available. No config file editing, no restart.

Adding a *new* skill — one that doesn't exist yet — is a developer task: drop a directory in [src/marcel_core/defaults/skills/](src/marcel_core/defaults/skills/) with a `SKILL.md` and either a JSON entry in `skills.json` (for simple HTTP/shell calls) or a Python module with `@register` decorators. See [docs/skills.md](docs/skills.md) for the full integration contract. Because Marcel can modify his own codebase, *he* can help you do this.

## Architectural decisions

A few choices shape everything else:

- **One central server, thin clients.** Marcel runs as a single FastAPI process per household. Telegram and the Rust CLI are both thin shells that stream over WebSocket; no agent state lives on the client. One brain, many mouths.
- **One continuous conversation per (user, channel).** There are no sessions. Each `(alice, telegram)` pair has an append-only JSONL log that never ends. Segments rotate at 500 messages or 500 KB; a rolling Haiku-generated summary is regenerated after 60 minutes of idle. This matches how Telegram actually works — your chat history with Marcel *is* the chat history, not an ephemeral session.
- **Flat files over databases.** Users, profiles, memories, conversations, artifacts — all live as markdown and JSONL under `~/.marcel/`. Easy to back up, easy to diff, easy for Marcel to rewrite. No migration pain.
- **Markdown as the source of truth.** System prompts are assembled from five H1 blocks pulled from `MARCEL.md`, `profile.md`, skill docs, memory index, and channel guidance. The agent edits the same files it reads, which is what makes self-modification work.
- **Self-modification is a feature, not a hack.** Marcel can edit his own code and restart himself. A systemd path watcher observes a flag file; when Marcel writes to it, systemd redeploys the container with automatic rollback on health-check failure. See [docs/self-modification.md](docs/self-modification.md).
- **Role-gated tools.** Admins get the full power set (`bash`, `read_file`, `write_file`, `edit_file`, `git_*`, `claude_code`). Regular users only get `integration` + the `marcel` utility tool. The model never sees tools it isn't allowed to use.

### Frameworks Marcel is built on

- **[pydantic-ai](https://ai.pydantic.dev/)** — the agent harness. Model-agnostic, typed tool registration, streaming events, structured dependencies. Marcel's whole inner loop is `agent.run_stream(...)`.
- **[FastAPI](https://fastapi.tiangolo.com/) + uvicorn** — the HTTP/WebSocket layer (`/ws/chat`, `/health`, `/api/history`, `/telegram/webhook`, `/api/artifact/...`).
- **[pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)** — every environment variable is declared once, typed, in [src/marcel_core/config.py](src/marcel_core/config.py). No `os.environ.get` scattered around.
- **[ratatui](https://ratatui.rs/) + crossterm** — the Rust CLI's TUI stack, the same one codex-cli uses.
- **[Playwright](https://playwright.dev/)** — the browsing tool's backend (navigate, snapshot, evaluate).
- **[OpenTelemetry](https://opentelemetry.io/) + [Arize Phoenix](https://phoenix.arize.com/)** — optional LLM tracing via `openinference-instrumentation-pydantic-ai`. Enabled with `MARCEL_TRACING_ENABLED=true`.
- **Docker + user-level systemd** — deployment. No root required after the initial `usermod -aG docker` and `loginctl enable-linger`.

### Supported models

Model identifiers are pydantic-ai-native `provider:model` strings, passed
verbatim to the agent. Pydantic-ai handles provider dispatch and reads the
matching credential from the environment (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
AWS credentials, etc.). Per-channel model preference is stored per user, so
Alice can run Sonnet on Telegram while Shaun runs Opus in the CLI.

| Model ID | Display name |
|----------|--------------|
| `anthropic:claude-sonnet-4-6` *(default)* | Claude Sonnet 4.6 — fast, recommended |
| `anthropic:claude-opus-4-6` | Claude Opus 4.6 — most capable |
| `anthropic:claude-haiku-4-5-20251001` | Claude Haiku 4.5 — used for memory/summary background tasks |
| `openai:gpt-4o`, `openai:gpt-4o-mini` | GPT-4o family |
| `openai:o1`, `openai:o3-mini` | Reasoning models |

The table above is a curated suggestion list; **any** pydantic-ai-supported
`provider:model` string works without a code change — `anthropic:`, `openai:`,
`bedrock:`, `groq:`, `mistral:`, `google-gla:`, `ollama:`, and more. The
display registry lives in [src/marcel_core/harness/agent.py](src/marcel_core/harness/agent.py).
The full architectural overview (module layout, agent loop, WebSocket protocol)
lives in [docs/architecture.md](docs/architecture.md).

## Development

```bash
make serve          # dev backend (uvicorn --reload on :7421, separate from the :7420 Docker prod)
make cli-dev        # build + run CLI (debug)
make cli            # build + run CLI (release)
make check          # format, lint, typecheck, test
```

## License

[MIT](LICENSE)

# Footnote

If you have read all the way to here, I want to note I have nothing against lobsters 🦞! They are jummy and have provided a good source of inspiration. 