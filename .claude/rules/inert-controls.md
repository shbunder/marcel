# Rule — a control that cannot fail is not a control

Every gate in Marcel exists because something can go wrong: the concurrency cap, the usage
guard, the approval check, the device-token check, the watchdog, the rollback.

## Always
- Ask the deciding question for every control: **if I delete this gate, which test goes red?**
  If the answer is "none", the control is untested and the story is not done.
- Test the degraded path as deliberately as the happy one: runner down, hub down, session dead,
  push failed, limit reached.
- Test through the path production takes: if production reaches a function through the API, the
  channel or the scheduler, the test does too.
- Name who sets a field in production. "Nothing yet" means the control is inert.

## Never
- Assert a setting instead of its effect (`assert cap == 4` proves nothing ran out of capacity)
- Add an item to a list or registry and call it wired: find what executes the list
