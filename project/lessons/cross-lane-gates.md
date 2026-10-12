# Cross-lane gates — a hub change can break the plugin

`plugins/marcel/tools` tests run the tools against the **real hub app** (in-process), not a stub. So a
hub change that alters behaviour the tools see — a push category, a "not built yet" error turning into a
real feature — turns the tools gate red on main even though `make -C hub check` is green.

It happened once: hub PR #43 (notify → category `note`, board notes, artifact versions) merged with the
hub gate green and left three tools tests expecting the old behaviour. PR #44 fixed them.

## Do

- Before merging anything under `hub/`, also run `make -C plugins/marcel/tools check`.
- When a hub change makes a "not built yet" answer real, grep the tools tests for that answer
  (`grep -rn "not built\|cannot store" plugins/marcel/tools/tests`) and update them in the same PR.
