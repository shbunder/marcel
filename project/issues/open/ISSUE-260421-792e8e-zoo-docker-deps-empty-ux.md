# ISSUE-792e8e: Close kernel-side zoo extraction — container deps, empty-zoo UX, first-party slot cleanup

**Status:** Open
**Created:** 2026-04-21
**Assignee:** Unassigned
**Priority:** Medium
**Labels:** infrastructure, docs, ux

## Capture
**Original request:** Session C.2 of ISSUE-63a946: close out the kernel-side marcel-zoo extraction work. Three related threads: (1) in-container zoo-deps install — the Docker image is currently baked with only kernel deps (caldav/vobject were removed from marcel-core's pyproject in Session B, closed 2e219dd), so iCloud integration fails ImportError silently at habitat discovery inside prod containers; ship a `make zoo-docker-deps` target that runs the zoo-setup dep install inside the running container via `docker exec`, and wire it into the `make setup` flow after `docker compose up`. (2) empty-zoo UX polish — today `channels.discover()` and `jobs.discover_templates()` silent no-op when MARCEL_ZOO_DIR is unset or the zoo is empty, so a fresh install with no `make zoo-setup` boots with zero habitats and the operator gets no feedback; add a startup INFO log line showing resolved MARCEL_ZOO_DIR and habitat counts (channels / skills / jobs / agents), plus a loud WARNING if the zoo is missing or empty. (3) docs/skills.md:100 and src/marcel_core/skills/integrations/__init__.py still describe a "first-party integrations slot" at src/marcel_core/skills/integrations/<name>.py that is empty post-Session-A (defaults/ removal on d32c1a3) — verifier surfaced this as out-of-scope cleanup during C.1's pre-close review; remove the framing or mark the slot's deliberate vestigiality explicit. Explicitly deferred: zoo GitHub Actions CI (belongs in the zoo repo as its own issue there), replacement of marcel-zoo/conftest.py sys.path shim (dev use case still depends on it, deferred again from Session C of the parent issue), and a git-push step in /finish-issue (meta-workflow, separate issue). Closes the kernel-side work on ISSUE-63a946; only zoo-repo-side follow-ups remain after C.2.

**Follow-up Q&A:** None — scope was surveyed and proposed by the assistant, user approved with "ok to proceed".

**Resolved intent:** This is the final kernel-side session for the marcel-zoo extraction (ISSUE-63a946). After Sessions A, B, and C.1 carved out the zoo repo, extracted its pyproject, and shipped host-side `make zoo-setup` + docs, three loose ends remain inside marcel-core: the prod Docker image still ships with only kernel deps (so habitats importing caldav/vobject fail silently at discovery); a fresh install with no zoo is invisible (no log, no warning); and two code sites still advertise a "first-party integrations slot" that was removed in Session A. This session closes all three and lets the parent issue ISSUE-63a946 hand off cleanly to zoo-repo follow-ups.

## Description

Three narrow, related threads. All three close kernel-side work started by ISSUE-63a946 — nothing new is introduced.

### Thread 1 — in-container zoo deps install

Session B removed zoo-specific dependencies (caldav, vobject) from `pyproject.toml`. The image built by `make docker-up` therefore no longer contains them. On the host, `make zoo-setup` (shipped in C.1) installs them into the kernel venv, but there is no equivalent path for the container.

- Add a `make zoo-docker-deps` target that runs `scripts/zoo-setup.sh` logic inside the running container via `docker exec` (reads `/host/.marcel/zoo/pyproject.toml` on the bind-mounted volume, then `uv pip install` into the container venv).
- Wire this into `make setup` so a first-boot operator who runs `make setup` ends up with a container that has the zoo deps.
- Mirror the `--sync` ergonomics: a `make zoo-docker-sync` (or equivalent) for re-install after zoo updates.

### Thread 2 — empty-zoo UX polish

`channels.discover()` (plugin/channels.py:190-215) and `jobs.discover_templates()` currently return silently when `MARCEL_ZOO_DIR` is unset or empty. A fresh install without `make zoo-setup` boots with zero habitats — no feedback to the operator.

- At kernel startup, log one INFO line showing resolved `MARCEL_ZOO_DIR` + habitat counts (channels / skills / jobs / agents).
- Emit a WARNING when the zoo is missing or empty (zero habitats total), pointing at `make zoo-setup`.

### Thread 3 — first-party integrations slot cleanup

Two files still describe a "first-party integrations slot at `src/marcel_core/skills/integrations/<name>.py`" that was emptied by Session A (defaults/ removal on d32c1a3):

- `docs/skills.md:100`
- `src/marcel_core/skills/integrations/__init__.py` (module docstring)

Remove the framing — the slot is either vestigial and deliberately so (and should be marked as such) or it should be removed entirely. Decide based on whether anything still imports from the package.

### Out of scope (explicitly deferred)

- **Zoo GitHub Actions CI** — belongs in the zoo repo as its own issue there.
- **Replace marcel-zoo/conftest.py sys.path shim** — dev workflow still depends on it; deferred again from the parent issue.
- **`/finish-issue` git-push step** — meta-workflow, separate issue.

## Tasks

- [ ] Add `make zoo-docker-deps` target that runs zoo dep install inside the running container via `docker exec`
- [ ] Wire `zoo-docker-deps` into `make setup` (so first-boot gives a container with zoo deps)
- [ ] Add `make zoo-docker-sync` (or equivalent `--sync` ergonomics) for post-zoo-update re-install
- [ ] Add startup INFO log: resolved `MARCEL_ZOO_DIR` + habitat counts (channels / skills / jobs / agents)
- [ ] Add startup WARNING when zoo is missing or empty, pointing at `make zoo-setup` / `make zoo-docker-deps`
- [ ] Decide on `src/marcel_core/skills/integrations/` — remove the package or mark vestigial with a clear comment
- [ ] Update `docs/skills.md:100` — remove or rewrite the "first-party integrations slot" framing
- [ ] Update `src/marcel_core/skills/integrations/__init__.py` docstring to match the decision above
- [ ] Run straggler grep for `first-party`, `integrations slot`, `src/marcel_core/skills/integrations/<name>`, `defaults/` across docs + zoo + .claude
- [ ] `make check` green
- [ ] `/finish-issue` → merged close commit on main

## Relationships
- Follows: [[ISSUE-12e920-zoo-first-boot-setup-docs]] (Session C.1, closed 5a3536f)
- Follows: [[ISSUE-2e219dd-zoo-pyproject-extract]] (Session B, closed 2e219dd)
- Follows: [[ISSUE-d32c1a3-defaults-removal]] (Session A, closed d32c1a3)
- Parent: [[ISSUE-63a946-marcel-zoo-extraction]] — this session closes the kernel-side work; only zoo-repo follow-ups remain.

## Implementation Log
<!-- Append entries here when performing development work on this issue -->

## Lessons Learned
<!-- Filled in at close time. Three subsections below — delete any that have nothing useful to say. -->

### What worked well
-

### What to do differently
-

### Patterns to reuse
-
