# contracts/ — the interfaces every lane builds against

`app-api.yaml` (hub ↔ app), `events.schema.json` (app WebSocket), `runner-api.yaml` (hub ↔ runner),
`transcript.schema.json`, `channel-protocol.md` (plugin ↔ hub) and `task-states.md`. Written by the
lead in F02. Implementers never change a contract; they file `project/contract-requests/<story>.md`.
