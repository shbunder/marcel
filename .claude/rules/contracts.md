# Rule — contracts are law

`contracts/` (the app API, the event schema, the runner and channel protocol, the dashboard and avatar-sheet
schemas) is where the lanes meet. The hub, the runner, the plugin and the iOS app are built in parallel against it.

- Only the **lead** changes `contracts/`, in a contract story (F02) or a dedicated PR. The guard hook asks for an
  unlock on every edit there.
- An implementer who finds a contract wrong **stops that story**: set it `blocked` on the board with the reason,
  add a `log` row with `kind: "contract-request"` that says what should change and why, and pick another story.
- Code may never quietly diverge from a contract "for now". Generated clients are regenerated, not hand-edited.

## Why

Six agents build against the same contract at once. One silent change breaks the others days later, at join time,
where it is most expensive to find.
