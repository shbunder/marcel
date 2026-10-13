# contracts/ — the interfaces every lane builds against

| File | Between | Story |
|---|---|---|
| [app-api.yaml](app-api.yaml) | app ↔ hub (REST, OpenAPI 3.1) | S-02.1; projects and artifact versions S-02.5; people, looks, quoted replies and mentions S-02.7 (0.7.0) |
| [events.schema.json](events.schema.json) | hub → app (WebSocket `/ws`, JSON Schema 2020-12) | S-02.1, S-02.5 |
| [task-states.md](task-states.md) | the task state machine every lane follows | S-02.1 |
| [dashboard.schema.json](dashboard.schema.json) | worker or hub → app (the content of a `dashboard` or project `board` artifact) | S-02.1, S-02.5 |
| [avatar-sheet.schema.json](avatar-sheet.schema.json) | `shared/avatar/zoo` (each agent's look, served by the hub) or `shared/avatar/pixel/build.py` (the bundled giraffe) → AvatarKit (the sprite sheet's loops) | S-02.5; zoo S-19.1 |
| [runner-api.yaml](runner-api.yaml) | hub → runner (HTTP over a unix socket) | S-02.2; a brain folder per agent (`POST /brains`) S-02.7 |
| [transcript.schema.json](transcript.schema.json) | runner → hub (one normalised event per transcript line) | S-02.2 |
| [channel-protocol.md](channel-protocol.md) + [channel.schema.json](channel.schema.json) | session's channel server ↔ hub (WebSocket, outside `/api`) | S-02.2 |
| [session-api.yaml](session-api.yaml) | sessions → hub over the tunnel (`/api/session/adopt`, `/api/session/report`) | S-02.2 |
| [brain-api.yaml](brain-api.yaml) | the brain's tools server (`plugins/marcel/tools`) → hub (`/brain/*`, NUC only) | S-05.3 |

Only the lead changes these files (the guard hook enforces it). Implementers who find a contract
wrong or missing something write `project/contract-requests/<story>.md` instead.

`make -C contracts check` validates the OpenAPI spec and checks that every schema has an example,
that every example matches its schema, that each WebSocket example matches exactly one event kind,
and that the state table matches the API's `TaskState` enum.

**Owner-only operations.** An app-api operation marked `x-owner-only: true` is the owner's alone; the
hub answers a member 403 `owner_only` (`components/responses/OwnerOnly`). The hub's tests read this
mark from the contract, so a new owner route is guarded as soon as it is marked.

**Additive bumps.** app-api 0.7.0 adds people, agent looks, quoted replies, mentions and project
trackers without breaking 0.6 clients: every field it adds to an existing schema is optional, and
`Agent.palette` and `Agent.seed` stay readable, marked `deprecated`. New operations the hub does not
serve yet are listed in `hub/tests/test_contract_routes.py` `PENDING_OPERATIONS`, new fields in
`hub/tests/test_contract.py` `PENDING` and `hub/tests/test_channel_frames.py` `PENDING_FIELDS`; each
list only shrinks.

**Avatar sheets over HTTP.** `GET /agents/{agent_id}/avatar` answers an `avatar-sheet.schema.json`
document (or, with `Accept: image/png`, its image). The response schema in app-api.yaml is a plain
object so code generators need not read the sheet schema; `tests/test_contract_0_7.py` checks the
example against it.

The WebSocket events live in a JSON Schema, which OpenAPI code generators do not read, so the iOS
app writes those Swift types by hand from `events.schema.json`.

After changing a contract, the lead also runs `make -C contracts mutants`. It deletes each
`if`/`then` rule in every contract in turn and fails if any deletion leaves the tests green, which
would mean that rule has no test that can fail.

`tests/reference_normalizer.py` turns the real transcripts recorded in SP2
(`tests/fixtures/transcripts/`) into `transcript.schema.json` events; the tests check that every one is
valid. The runner (S-03.4) may port it.
