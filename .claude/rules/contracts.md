# Rule — contracts are law

`contracts/` (OpenAPI, JSON Schemas, the channel protocol and the task state machine) is what lets
six agents build lanes in parallel without talking to each other. The guard hook blocks edits there.

## Implementers
- Build against the contract exactly. A field the contract lacks does not exist.
- If a contract is wrong or missing something, stop that part of the story and write
  `project/contract-requests/<story>.md` (what, why, proposed change). Carry on with other work.

## The lead
- Changes a contract only in its own commit, `[S-02.x] contract: …`, after checking every lane that
  reads it. Breaking changes bump the contract's version and list the affected stories.
