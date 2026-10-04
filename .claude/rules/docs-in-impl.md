# Rule — docs ship with the change that needs them

- A story that adds or changes something an operator runs, configures or reads (a setting, a
  command, a credential, a notification, an endpoint) updates `docs/` **in the same commit**.
- A new module's page says: what it does, what it needs, what happens when its dependency is down,
  and what the user sees then.
- Write what Marcel does **today**. Plans live in `plan/` and on the board, not in `docs/`.
- Before finishing, grep for every name you renamed or removed: `grep -rn "<old name>" docs/ plan/ .claude/`.
