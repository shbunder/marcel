# Local LLM Setup

Marcel can run a self-hosted, OpenAI-compatible LLM as part of its model
fallback chain — either as a cheap backup for scheduled jobs or as the
failure-explanation voice when cloud providers are down. This page
documents the **runtime setup** (Ollama install, systemd unit, resource
limits). The **fallback chain logic** — when each tier fires and how to
opt jobs in or out — lives in [docs/model-tiers.md](./model-tiers.md),
which is the authoritative source for the behaviour matrix.

The intended runtime is [Ollama](https://ollama.com) on the same machine
Marcel runs on, but any server that implements the OpenAI
`/v1/chat/completions` endpoint with tool calling will work.

## Quick behaviour summary

The local LLM is invoked by the chain in two places:

1. **Explain-the-failure tier** (interactive turns) — when both the
   session tier's primary (e.g. `MARCEL_STANDARD_MODEL`) and its
   per-tier backup (e.g. `MARCEL_STANDARD_BACKUP_MODEL`) have failed,
   the local model runs with a synthesised "explain the failure" prompt
   and tells the user cloud models are temporarily unavailable.
2. **Complete-the-task tier** (scheduled jobs with
   `allow_local_fallback=True`) — when cloud retries exhaust, the local
   model runs the original task like the legacy ISSUE-070 path.

See [docs/model-tiers.md](./model-tiers.md) for the full tier semantics,
the behaviour matrix, and the `allow_fallback_chain` / `allow_local_fallback`
interaction.

## Environment variables

| Variable                    | Example                         | Purpose                                           |
|-----------------------------|---------------------------------|---------------------------------------------------|
| `MARCEL_LOCAL_LLM_URL`      | `http://127.0.0.1:11434/v1`     | Base URL of an OpenAI-compatible server           |
| `MARCEL_LOCAL_LLM_MODEL`    | `ministral-3:14b`               | Model tag served at that URL                      |
| `MARCEL_LOCAL_LLM_TIMEOUT`  | `300`                           | Per-turn wall-clock budget for LOCAL-tier turns (seconds, default 300) |

Put them in `.env.local`:

```bash
MARCEL_LOCAL_LLM_URL=http://127.0.0.1:11434/v1
MARCEL_LOCAL_LLM_MODEL=ministral-3:14b
# MARCEL_LOCAL_LLM_TIMEOUT=300   # optional — default is 300s
```

`MARCEL_LOCAL_LLM_URL` and `MARCEL_LOCAL_LLM_MODEL` must both be set for
the fallback to arm. The fallback also becomes visible as a selectable
model (`local:ministral-3:14b`) in the `list_models` UI, so you can set
it as a channel primary if you want to use the local model as the
default rather than just a fallback.

## Interactive turns: warm-up ack + wider timeout

When a Telegram turn resolves to the LOCAL tier (user typed `/local`, a
skill has `preferred_tier: local`, or the session/default tier is
LOCAL), two things change versus the cloud tiers:

- **Timeout.** The per-turn wall-clock budget is
  `MARCEL_LOCAL_LLM_TIMEOUT` (default 300s) instead of the 120s cloud
  budget. Ollama cold-start on a 14B is 30–60s to first token, plus
  ~3–5 tok/s generation on CPU — a 200-token reply can comfortably
  push past 120s on the first request. Tune down on faster hardware;
  keep the cloud budget tight so a genuinely hung cloud turn still
  fails fast.
- **Delayed-ack text.** The "Working on it..." message that appears
  after 10s becomes "Warming up the local model — this can take a
  minute..." so the user understands why nothing has streamed yet.

The WebSocket `/ws/chat` endpoint has no overall wall-clock timeout
(tokens stream as they arrive), so these knobs don't apply there.

## Opting a job in

```python
from marcel_core.jobs.models import JobDefinition, TriggerSpec, TriggerType

job = JobDefinition(
    name='Morning digest',
    users=['shaun'],
    trigger=TriggerSpec(type=TriggerType.CRON, cron='0 7 * * *', timezone='Europe/Brussels'),
    system_prompt='You compose a morning news digest...',
    task="Compose today's digest.",
    model='anthropic:claude-sonnet-4-6',
    allow_local_fallback=True,   # ← opt in to the local fallback
)
```

Jobs without this flag behave exactly as before — the local LLM is never
invoked for them, even if the environment variables are set.

## Runtime setup (Ollama on Linux)

This is the path validated on a Core Ultra 5 125H NUC. If you have a
dGPU, the GPU wheel will auto-detect and the steps are the same.

### Install

Download the latest Ollama Linux tarball (newest release lives at
<https://github.com/ollama/ollama/releases>):

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

The script installs the `ollama` binary to `/usr/local/bin` and creates
a `systemd` service named `ollama.service`. On a machine without sudo or
where you need an isolated install, you can also grab the `.tar.zst`
directly and extract it to `/opt/ollama/` — `start-ollama.sh` doesn't
exist for the upstream build, but `./bin/ollama serve` runs fine under
its own env.

### Pull the model

```bash
ollama --version          # requires ≥ 0.13.1 for Ministral 3
ollama pull ministral-3:14b
ollama pull ministral-3:8b
```

`ministral-3:14b` (Mistral AI, December 2025, Apache 2.0) is the
recommended primary. It ships with **native function-calling + JSON
output** — the critical axis for Marcel's chained-integration pattern —
and inherits the training lineage of Mistral Nemo 12B, which scored
**92.5%** on the jdhodges 2026 tool-calling eval (best for sequential
multi-turn workflows). Q4_K_M needs ~7 GB on disk and **~9 GB RAM** when
loaded. 256k context window, comfortable headroom for the ~8–12k prompts
Marcel actually sends. CPU throughput on a Core Ultra 5 125H sits
around 3–5 tok/s — adequate for async jobs, borderline-interactive for
the morning digest (which streams to Telegram, not a live chat).

`ministral-3:8b` is the faster understudy — same family, same training,
~5 GB RAM, ~6–9 tok/s CPU. Pull both so the harness can fall across to
the 8B on heavy-load turns without a cold reload.

**Legacy note:** earlier setups used `qwen3.5:4b` (97.5% on the same
eval at 4B). The 4B is no longer the recommended tag, but any tag your
Ollama server still serves will work if you point `MARCEL_LOCAL_LLM_MODEL`
at it — the harness is model-agnostic.

### Verify tool calling

Before wiring Marcel up, confirm the endpoint handles OpenAI-format tool
calls correctly — some models/runtimes silently mis-translate the chat
template:

```bash
curl -sS -X POST http://127.0.0.1:11434/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "ministral-3:14b",
    "messages": [
      {"role": "system", "content": "Always call the weather tool for weather questions."},
      {"role": "user", "content": "Weather in Brussels and Paris?"}
    ],
    "tools": [{
      "type": "function",
      "function": {
        "name": "get_weather",
        "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}
      }
    }],
    "stream": false
  }' | python3 -m json.tool
```

A healthy response has `"finish_reason": "tool_calls"` and a
`tool_calls` array with valid JSON arguments. If you see the model
generating plain-text pseudo-code for the tool call, the runtime's
template handling is broken — switch to a different model or upgrade the
server.

## Systemd unit with Plex coexistence

When the NUC also runs Plex, the ollama service needs hard memory and
CPU limits so a hot LLM load can never starve the transcoder. Drop-in
override at `/etc/systemd/system/ollama.service.d/override.conf`:

```ini
[Service]
Environment=OLLAMA_HOST=127.0.0.1:11434
Environment=OLLAMA_KEEP_ALIVE=10m            # longer unload window — cold-loading 14B is slower
Environment=OLLAMA_MAX_LOADED_MODELS=1
Environment=OLLAMA_NUM_PARALLEL=1
Environment=OLLAMA_NUM_THREAD=12             # Core Ultra 5 125H: 14 P+E cores, 12 leaves UI headroom
MemoryHigh=10G                               # back-pressure before hard cap
MemoryMax=12G                                # fits Q4_K_M 14B + KV cache; OOM kills ollama, never Plex
CPUQuota=800%                                # at most 8 full cores
Nice=10                                      # Plex always wins scheduler contention
IOSchedulingClass=best-effort
IOSchedulingPriority=5
```

If you also want the 8B resident simultaneously (avoids reload thrash
when the harness swaps between primary and understudy), raise `MemoryMax`
to `17G` and set `OLLAMA_MAX_LOADED_MODELS=2`.

Apply with:

```bash
sudo systemctl daemon-reload
sudo systemctl restart ollama
```

### Tuning CPU thread count

`OLLAMA_NUM_THREAD=12` is the starting point on a 125H. Dual-channel RAM
is a prerequisite for meaningful throughput gains — single-channel
starves memory bandwidth and adding threads won't help. Drop to 8 if
other services on the host (Plex transcode, background jobs) contend
for cores during an LLM run.

## Observability

Every fallback run is recorded in `~/.marcel/jobs/<slug>/runs/<user>.jsonl` (or `runs/_system.jsonl` for system-scope jobs):

```json
{"run_id": "...", "status": "completed", "fallback_used": "local", ...}
```

Grep for fallback usage:

```bash
grep -r '"fallback_used":"local"' ~/.marcel/jobs/*/runs/*.jsonl
```

Ollama's own logs include per-request timings (load, prompt eval,
generation). Watch them during the first few fallback events:

```bash
journalctl -u ollama -f
```

## Verification checklist

Before trusting the fallback in production:

- [ ] `curl http://127.0.0.1:11434/v1/models` lists the configured model
- [ ] The tool-calling curl above returns `finish_reason: tool_calls` with well-formed arguments
- [ ] `MARCEL_LOCAL_LLM_URL` and `MARCEL_LOCAL_LLM_MODEL` are readable from inside the Marcel process (`os.getenv(...)`)
- [ ] One test job with `allow_local_fallback=True` executes successfully when `ANTHROPIC_API_KEY` is unset or invalid
- [ ] The resulting `runs.jsonl` entry has `fallback_used: "local"`
- [ ] After 10 minutes of idle, `free -h` shows the model unloaded (~5 GB returned to the OS)
- [ ] A Plex 4K transcode during a fallback run stays within 10% of baseline FPS

## Rollback

Unsetting either environment variable disables the fallback immediately —
no restart needed for new job runs, though already-running jobs keep
their original resolution. For a full rollback:

```bash
# .env.local — comment out
# MARCEL_LOCAL_LLM_URL=...
# MARCEL_LOCAL_LLM_MODEL=...

sudo systemctl stop ollama
sudo systemctl disable ollama
```

The Marcel code change is a no-op when the env vars are unset, so you
can keep the code path live without any runtime footprint.
