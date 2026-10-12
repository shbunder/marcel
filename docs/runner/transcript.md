# Runner — transcript tailing (`GET /sessions/{id}/tail`)

The hub shows what a session is doing by reading its transcript through the runner. The runner reads
the JSONL file Claude Code writes and streams each line as normalised events, so the hub never needs
`~/.claude` (SP6).

Code: `runner/src/marcel_runner/transcript.py`. Contract: `contracts/runner-api.yaml` (`tailSession`)
and `contracts/transcript.schema.json`.

## Where the transcript is

| Transcript | File |
|---|---|
| A session | `~/.claude/projects/<cwd, every non-alphanumeric character → ->/<session-id>.jsonl` |
| One of its subagents | `<same folder>/<session-id>/subagents/agent-<agent_id>.jsonl` |

The runner looks the session up in `claude agents --json --all` (full or short id) to get its cwd.
Only the `/` → `-` part of the folder name is recorded (SP2). If that folder has no such file, the
runner looks for `<session-id>.jsonl` in every project folder, so a cwd with `_` or `.`, or a path
Claude Code shortens, is still found. While a tail waits for a file that does not exist yet, the
expected folder is looked at on every poll (4 times a second), but the search through every folder
runs at most once every 5 seconds (`Locator`, `SEARCH_EVERY`).

A tail that found its file by that search stays on it, even if a file with the same name later
appears in the expected folder. A session id names one transcript, so in practice the two cannot
both be written; see `project/lessons/runner-transcripts.md` for why the runner does not switch.

## The request

`GET /sessions/{id}/tail?offset=&follow=&subagent=`

| Parameter | Meaning |
|---|---|
| `offset` | Byte offset to start from (default 0). `Last-Event-ID` wins when both are sent. |
| `follow` | `true` (default): keep the stream open and send new lines as they are written. `false`: send what is there and close. |
| `subagent` | A subagent's `agent_id` (hex) from a `subagent_start` event: stream its own file. Every event then carries `subagent_id`, and offsets are into that file. |

Each SSE message is:

```
id: <byte offset just after the line>
event: transcript
data: <one TranscriptEvent>
```

One line can give zero, one or several events; several share the offset and count up in `index`.
A quiet stream gets a `: still here` comment every 15 seconds, so a client that left is noticed.

## Resuming

- Reconnect with the last offset you processed (`offset=` or `Last-Event-ID`): the stream continues
  with the next line. Nothing is sent twice and nothing is skipped.
- An offset in the middle of a line starts at the beginning of that line, never mid-line. (The hub
  resumes from the start of the last line it saw and skips the events it already stored.)
- An offset past the end of the file means the file was rewritten: the stream starts again at 0.
  Offsets then go backwards; the client resets its position (the hub does, and skips the events
  it already stored).

## One tail per file

A second tail for the same file (same session, same subagent or none) **replaces** the first: the
older stream ends. The hub reconnects as soon as its old connection drops, often before the runner
has noticed; refusing the new tail would leave the hub without a transcript.

## How lines become events

`Normaliser` is a port of `contracts/tests/reference_normalizer.py` (SP2's mapping table); a test
checks it gives the same events for every recorded transcript. What the runner adds:

| Case | What happens |
|---|---|
| A line type nobody knows (e.g. `ai-title`) | A `raw` event with `entry_type` and the line (cut to `{type, truncated}` above 8 KB). |
| A known type in a shape never seen (missing ids, odd fields) | A `raw` event, never a crash. |
| A line that is not a JSON object | `raw` with `entry_type: unreadable`. |
| A line longer than 8 MB (e.g. a huge pasted image) | Skipped without holding it in memory; one `raw` event, `entry_type: line-too-long`, with its size. |
| A line without a time | It takes the time of the line before it (the reference uses 1970). |
| A tool's input | Every string in it is cut to 8 KB. Tool results to 8 KB with `truncated: true`. |
| A resume after an `Agent` call but before its launch result | The runner first reads the earlier lines that mention `Agent`, so `subagent_start` still names the call's `description` and `subagent_type`. At most 256 open calls are remembered. |

## The file changes under the stream

| What happens to the file | What the stream does |
|---|---|
| Does not exist yet | Waits for it (`follow=false`: closes with no events). |
| Grows | Sends each new complete line. A half-written last line waits for its newline. |
| Truncated in place | Starts again at offset 0. |
| Replaced or deleted | Sends what the old file still had, then reads the new one from 0 when it appears. |

When the file starts over, the hub's stored offset is larger than the new offsets; the hub decides
what to keep.

## Errors

JSON `Error` bodies, in plain words: 404 `not_found` (no such session), 400 `bad_request` (an
offset that is not a whole number, a subagent id that is not hex), 405 for anything but GET, and the
roster's own 503 when `claude agents` cannot be read.
