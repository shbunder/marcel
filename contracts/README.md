# contracts/ — the interfaces every lane builds against

| File | Between | Story |
|---|---|---|
| [app-api.yaml](app-api.yaml) | app ↔ hub (REST, OpenAPI 3.1) | S-02.1 |
| [events.schema.json](events.schema.json) | hub → app (WebSocket `/ws`, JSON Schema 2020-12) | S-02.1 |
| [task-states.md](task-states.md) | the task state machine every lane follows | S-02.1 |
| [dashboard.schema.json](dashboard.schema.json) | worker → app (the content of a `dashboard` artifact) | S-02.1 |
| [runner-api.yaml](runner-api.yaml) | hub → runner (HTTP over a unix socket) | S-02.2 |
| [transcript.schema.json](transcript.schema.json) | runner → hub (one normalised event per transcript line) | S-02.2 |
| [channel-protocol.md](channel-protocol.md) + [channel.schema.json](channel.schema.json) | session's channel server ↔ hub (WebSocket, outside `/api`) | S-02.2 |
| [session-api.yaml](session-api.yaml) | sessions → hub over the tunnel (`/api/session/adopt`, `/api/session/report`) | S-02.2 |

Only the lead changes these files (the guard hook enforces it). Implementers who find a contract
wrong or missing something write `project/contract-requests/<story>.md` instead.

`make -C contracts check` validates the OpenAPI spec and checks that every schema has an example,
that every example matches its schema, that each WebSocket example matches exactly one event kind,
and that the state table matches the API's `TaskState` enum.

The WebSocket events live in a JSON Schema, which OpenAPI code generators do not read, so the iOS
app writes those Swift types by hand from `events.schema.json`.

After changing a contract, the lead also runs `make -C contracts mutants`. It deletes each
`if`/`then` rule in every contract in turn and fails if any deletion leaves the tests green, which
would mean that rule has no test that can fail.

`tests/reference_normalizer.py` turns the real transcripts recorded in SP2
(`tests/fixtures/transcripts/`) into `transcript.schema.json` events; the tests check that every one is
valid. The runner (S-03.4) may port it.
