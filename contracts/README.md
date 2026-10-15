# contracts/ — the interfaces every lane builds against

| File | Between | Story |
|---|---|---|
| [app-api.yaml](app-api.yaml) | app ↔ hub (REST, OpenAPI 3.1) | S-02.1; projects and artifact versions S-02.5; people, looks, quoted replies and mentions S-02.7 (0.7.0); one person, buddies on phones, relay push S-02.13 (0.8.0); memory per buddy (`agent_id` on the memory routes) 0.8.1 |
| [events.schema.json](events.schema.json) | hub → app (WebSocket `/ws`, JSON Schema 2020-12) | S-02.1, S-02.5; `device.updated` S-02.13 |
| [push-relay.yaml](push-relay.yaml) | app and hub → the owner's push relay (`relay/`, a Cloudflare Worker) | S-02.13 |
| [task-states.md](task-states.md) | the task state machine every lane follows | S-02.1 |
| [dashboard.schema.json](dashboard.schema.json) | worker or hub → app (the content of a `dashboard` or project `board` artifact) | S-02.1, S-02.5 |
| [avatar-sheet.schema.json](avatar-sheet.schema.json) | `shared/avatar/zoo` (each agent's look, served by the hub, and the bundled default giraffe, `make -C ios/AvatarKit sheet`) → AvatarKit (the sprite sheet's loops) | S-02.5; zoo S-19.1 |
| [runner-api.yaml](runner-api.yaml) | hub → runner (HTTP over a unix socket) | S-02.2; a brain folder per agent (`POST /brains`) S-02.7; the pinned claude version (`claude_version`) S-02.9; workflow tails, the brain's animal, Claude update events S-02.11; whether Claude Code is logged in (`Health.login`) and why a usage reading is stale (`UsageReading.reason`) S-02.12; no pin on a Mac (`claude_change.expected` and `Health.claude_version_expected` may be absent) S-02.13 (0.1.14) |
| [transcript.schema.json](transcript.schema.json) | runner → hub (one normalised event per transcript line) | S-02.2; workflow events S-02.11 |
| [channel-protocol.md](channel-protocol.md) + [channel.schema.json](channel.schema.json) | session's channel server ↔ hub (WebSocket, outside `/api`) | S-02.2 |
| [session-api.yaml](session-api.yaml) | sessions → hub over the tunnel (`/api/session/adopt`, `/api/session/report`); the plugin's session-end hooks on the NUC (`/channel/session-end`, outside `/api`) | S-02.2; session-end S-02.9 |
| [brain-api.yaml](brain-api.yaml) | the brain's tools server (`plugins/marcel/tools`) and the plugin's hooks → hub (`/brain/*`, NUC only) | S-05.3; `/brain/summary` S-02.9; `NewTask.kickoff` S-02.13 (0.1.5) |

Only the lead changes these files (the guard hook enforces it). Implementers who find a contract
wrong or missing something write `project/contract-requests/<story>.md` instead.

`make -C contracts check` validates the OpenAPI spec and checks that every schema has an example,
that every example matches its schema, that each WebSocket example matches exactly one event kind,
and that the state table matches the API's `TaskState` enum.

**app-api 0.8.0 (S-02.13, ADR-013): one person, many buddies.** Breaking, on purpose:
- **Removed:** the people routes and their schemas (`User`, `NewUser`, `UserRole`), `Me.role`, the
  owner-only mark on operations, the `OwnerOnly` response and the codes `owner_only`, `user_not_found`,
  `cannot_remove_owner`. Every route is open to every paired phone of the one person.
- **Buddies** (the schema is still `Agent`): `POST /agents` (`NewAgent`), `POST /agents/{agent_id}/archive`
  (`Agent.archived_at`; 409 `last_buddy`).
- **A phone's buddy:** `Device.agent_id` (required), `PATCH /devices/{device_id}` (`DeviceUpdate`), the
  `device.updated` event, and `POST /pairing-codes` (`NewPairingCode`), whose link may end `&buddy=<agent id>`.
- **Relay push:** `PushTarget` kind `relay`, `Device.push` `relay`, `Me.push.relay`, and `SealedPush` /
  `SealedPushContent`, with a known-answer vector in `tests/fixtures/sealed-push/vector.json`. The relay's
  own API is `push-relay.yaml`.
- `Health.os` (`macos | linux`).
- **Trackers:** `Project.tracker_url` is a claude.ai artifact url only, set only by the project's kickoff
  quest (brain-api 0.1.5 `NewTask.kickoff`; channel `Artifact.role`).

**app-api 0.8.1 (additive): memory per buddy.** `GET /memory` and `GET`/`PUT /memory/files/{path}` take an
optional `agent_id` query parameter (`MemoryAgentId`); omitted, it is the calling phone's buddy. An id that is no
buddy is 404 `agent_not_found`; an archived buddy's memory stays readable and refuses a save. Query parameters the
hub does not read yet are listed in `hub/tests/test_contract_routes.py` `PENDING_QUERY_PARAMETERS`.

While the hub and the mock catch up, operations the hub still serves but 0.8.0 removed are listed in
`hub/tests/test_contract_routes.py` `RETIRED_OPERATIONS` (S-22.11 removes them).

**Order of rollout.** The app's generated client (`ios/MarcelKit/Sources/MarcelAPI`) refuses a field or an
enum value it does not know (`additionalProperties: false` makes its decoders strict). So a field the
hub starts sending, optional or not (`Device.agent_id`, `Agent.archived_at`, `Me.push.relay`,
`Health.os`, `Device.push` `relay`), breaks an app built from an older client. The app regenerates
from 0.8.0 (S-22.12) before the owner's hub runs a story that sends them (S-22.11, S-22.15, S-22.27).

**Additive bumps.** app-api 0.7.0 adds people, agent looks, quoted replies, mentions and project
trackers without breaking 0.6 clients: every field it adds to an existing schema is optional, and
`Agent.palette` and `Agent.seed` stay readable, marked `deprecated`. In 0.7.1 a `Look` the hub sends
holds open strings (a client treats an unknown value as no look and shows the default avatar), while
`NewLook`, what the hub accepts, keeps the closed lists. New operations the hub does not
serve yet (0.8.0's too) are listed in `hub/tests/test_contract_routes.py` `PENDING_OPERATIONS`, new fields in
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
