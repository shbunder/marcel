# Local LLM Fallback

Marcel can fall back to a self-hosted, OpenAI-compatible LLM when cloud
providers (Anthropic, Bedrock, OpenAI) fail. The feature is **opt-in per
job** and **off by default** — nothing changes until the two environment
variables below are set and at least one job declares `allow_local_fallback`.

The intended runtime is [Ollama](https://ollama.com) on the same machine
Marcel runs on, but any server that implements the OpenAI
`/v1/chat/completions` endpoint with tool calling will work.

## When it fires

After `execute_job_with_retries()` runs its normal retry loop, the fallback
fires if **all** of these are true:

1. The final run is still `FAILED` or `TIMED_OUT`
2. `job.allow_local_fallback` is `True`
3. `MARCEL_LOCAL_LLM_URL` and `MARCEL_LOCAL_LLM_MODEL` are both set
4. The error category is in the fallback-eligible set:
   `rate_limit`, `timeout`, `network`, `server_error`, `auth_or_quota`

The fallback runs `execute_job()` exactly once against `local:<model>`.
It does **not** re-enter the retry loop. Successful runs are marked with
`fallback_used = 'local'` in `runs.jsonl`.

Permanent non-auth errors (bad input, unknown skill, validation errors)
**never** trigger the fallback — a local model wouldn't fix the request
shape.

## Environment variables

| Variable                  | Example                         | Purpose                                 |
|---------------------------|---------------------------------|-----------------------------------------|
| `MARCEL_LOCAL_LLM_URL`    | `http://127.0.0.1:11434/v1`     | Base URL of an OpenAI-compatible server |
| `MARCEL_LOCAL_LLM_MODEL`  | `qwen3.5:4b`                    | Model tag served at that URL            |

Put them in `.env.local`:

```bash
MARCEL_LOCAL_LLM_URL=http://127.0.0.1:11434/v1
MARCEL_LOCAL_LLM_MODEL=qwen3.5:4b
```

Both must be set for the fallback to arm. The fallback also becomes
visible as a selectable model (`local:qwen3.5:4b`) in the `list_models`
UI, so you can set it as a channel primary if you want to use the local
model as the default rather than just a fallback.

## Opting a job in

```python
from marcel_core.jobs.models import JobDefinition, TriggerSpec, TriggerType

job = JobDefinition(
    name='Morning digest',
    user_slug='shaun',
    trigger=TriggerSpec(type=TriggerType.CRON, cron='0 7 * * *', timezone='Europe/Brussels'),
    system_prompt='You compose a morning news digest...',
    task='Compose today\'s digest.',
    model='anthropic:claude-sonnet-4-6',
    allow_local_fallback=True,   # ← new flag
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
ollama pull qwen3.5:4b
```

`qwen3.5:4b` scored **97.5%** on the jdhodges 2026 tool-calling eval
(best in its size class, including multi-tool parallel calls), needs
~3.4 GB on disk and ~5 GB RAM when loaded. It is the recommended starting
model for this path.

### Verify tool calling

Before wiring Marcel up, confirm the endpoint handles OpenAI-format tool
calls correctly — some models/runtimes silently mis-translate the chat
template:

```bash
curl -sS -X POST http://127.0.0.1:11434/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3.5:4b",
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
Environment=OLLAMA_KEEP_ALIVE=5m            # unload after 5 min idle — frees RAM for Plex
Environment=OLLAMA_MAX_LOADED_MODELS=1
Environment=OLLAMA_NUM_PARALLEL=1
MemoryHigh=5G                                # back-pressure before hard cap
MemoryMax=6G                                 # hard cap — OOM kills ollama, never Plex
CPUQuota=400%                                # at most 4 full cores
Nice=10                                      # Plex always wins scheduler contention
IOSchedulingClass=best-effort
IOSchedulingPriority=5
```

Apply with:

```bash
sudo systemctl daemon-reload
sudo systemctl restart ollama
```

### Tuning CPU thread count (post-upgrade)

Default ollama only uses a handful of threads on the Core Ultra 5 125H
(4 observed in the ISSUE-070 smoke test). Once you've verified the
fallback is working end-to-end, tune with:

```ini
Environment=OLLAMA_NUM_THREAD=8    # try 6, 8, 12 on 14 P+E cores
```

Dual-channel RAM is a prerequisite for meaningful throughput gains —
single-channel starves memory bandwidth and adding threads won't help.

## Observability

Every fallback run is recorded in `~/.marcel/users/<user>/jobs/<job_id>/runs.jsonl`:

```json
{"run_id": "...", "status": "completed", "fallback_used": "local", ...}
```

Grep for fallback usage:

```bash
grep -r '"fallback_used":"local"' ~/.marcel/users/*/jobs/*/runs.jsonl
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
