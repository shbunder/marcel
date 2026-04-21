# ISSUE-0baea6: Author marcel-zoo/pyproject.toml and split habitat-only deps out of marcel-core

**Status:** Open
**Created:** 2026-04-21
**Assignee:** Unassigned
**Priority:** Medium
**Labels:** refactor, packaging

## Capture

**Original request:** Session B of ISSUE-63a946: Author marcel-zoo/pyproject.toml and split habitat-only deps (caldav, vobject) out of marcel-core. Session A (closed as d32c1a3) deleted defaults/ and moved MARCEL.md + routing.yaml into the zoo. Session C will handle first-boot clone, SETUP.md updates, zoo CI, integration-test migration. Narrower than the parent issue anticipated: survey shows habitat-only deps are just caldav+vobject (icloud integration). The EnableBanking/pyicloud/feedparser/docker-SDK candidates listed in the parent issue aren't actually imported anywhere — banking uses raw httpx+jwt, icloud uses imaplib+caldav, news uses httpx+ET, docker uses subprocess. playwright+trafilatura are kernel (tools/web/, tools/browser/) and stay in marcel-core's browser optional group.

**Follow-up Q&A:** None — scope resolved from the parent issue's Session B checklist and a survey of both repos' import surface.

**Resolved intent:** Give `marcel-zoo` its own `pyproject.toml` so it is a genuine standalone Python project, and remove from marcel-core every Python dependency that exists only to satisfy zoo habitats. After this change, marcel-core's dep tree reflects only what the kernel needs; the zoo's reflects only what its habitats need. Session A removed the *file-based* coupling (`defaults/` seeding); Session B removes the *packaging* coupling. Session C will close the loop by making fresh installs clone the zoo automatically.

## Description

### Survey results (what is actually habitat-only?)

The parent issue listed `EnableBanking`, `pyicloud`, `feedparser`, `docker SDK` as candidates. A grep of `marcel-zoo/` for `^(import|from) <name>` shows none of them are imported. The actual import surface resolves as:

| Dep | Used by | Where it stays |
|---|---|---|
| `caldav` | `integrations/icloud/client.py` (zoo) | → zoo |
| `vobject` | caldav result objects (`ev.vobject_instance.vevent`) — zoo | → zoo |
| `httpx` | kernel + zoo | marcel-core core deps (stays) |
| `jwt` (PyJWT) | kernel + zoo (banking) | marcel-core core deps (stays) |
| `yaml` (PyYAML) | kernel + zoo (news) — pulled in transitively; marcel-core already depends on it via `pydantic-settings` / mkdocs | marcel-core (stays, pulled transitively) |
| `playwright`, `trafilatura` | kernel (`src/marcel_core/tools/browser/`, `src/marcel_core/tools/web/`) | marcel-core `[browser]` optional (stays) |

So Session B's real diff is: **delete the `zoo` optional-dependency group from marcel-core's pyproject (currently `caldav` + `vobject`), and create marcel-zoo's pyproject listing those two deps plus a minimal dev group for its own tests.**

### What the zoo's pyproject needs

- `[project]` table with `name = "marcel-zoo"`, `version = "0.1.0"`, `requires-python = ">=3.11,<3.13"` (matching marcel-core's range), and `dependencies = ["caldav>=3.1.0", "vobject>=0.9.9"]`.
- `[build-system]` using hatchling (same as marcel-core) — but we do NOT package the zoo as a wheel. The kernel loads habitats from `MARCEL_ZOO_DIR` via file discovery, not `import marcel_zoo`. So no `[tool.hatch.build.targets.wheel]` packages declaration is needed. The pyproject exists primarily to declare deps for a future `uv sync`/`pip install -e .` in the zoo repo (Session C wires this into fresh-boot).
- `[tool.pytest.ini_options]` replacing today's ad-hoc `pytest.ini` + `conftest.py` sys.path hack. The zoo's conftest.py currently mutates `sys.path` to find `marcel_core` — keep that for now (Session C may change the install story); just move the pytest config block into pyproject and delete `pytest.ini`.

### What marcel-core's pyproject loses

```toml
zoo = [
    "caldav>=3.1.0",
    "vobject>=0.9.9",
]
```

That entire `zoo` entry under `[project.optional-dependencies]` goes away. No other kernel dep changes.

### What doesn't change

- marcel-core's `[browser]` optional group (playwright + trafilatura) — those are kernel-owned.
- The `make serve` / `docker-compose.dev.yml` / Dockerfile path that installs `--all-extras` — still works, just installs fewer things.
- The zoo's own test entrypoint (`python -m pytest integrations/ skills/ channels/` from the zoo root) — still works.
- `MARCEL_ZOO_DIR` wiring, habitat discovery, plugin registration — unchanged.

## Tasks

- [ ] Survey marcel-zoo's actual import surface to confirm caldav + vobject are the only habitat-only deps (done pre-issue; result documented above).
- [ ] Author `/home/shbunder/projects/marcel-zoo/pyproject.toml` with: `[project]` metadata, `dependencies = [caldav, vobject]`, `[build-system]` hatchling, `[tool.pytest.ini_options]` mirroring the current `pytest.ini`. Commit in the zoo repo with `add pyproject.toml`.
- [ ] Delete `/home/shbunder/projects/marcel-zoo/pytest.ini` in the same zoo commit (config moves to pyproject).
- [ ] Update marcel-zoo's `conftest.py` docstring to stop referencing the "no pyproject.toml" state now that it exists. Commit in the zoo repo.
- [ ] Remove the `[project.optional-dependencies].zoo = [...]` block from marcel-core's `pyproject.toml`.
- [ ] Verify `uv sync --all-extras` in marcel-core still succeeds and `make check` passes (no hidden import of caldav/vobject from kernel code).
- [ ] Update [docs/plugins.md](../../docs/plugins.md) if it mentions the `zoo` extra; otherwise confirm no doc references.
- [ ] Straggler grep: `grep -rn "caldav\|vobject" docs/ ~/.marcel/ .claude/ src/marcel_core/ Makefile Dockerfile docker-compose*.yml README.md SETUP.md` — confirm every match is either in zoo-checkout paths (not kernel source) or is a habitat that owns that dep.
- [ ] Run `make check` (format + lint + typecheck + tests, 90% coverage).
- [ ] Close via `/finish-issue` and merge to main.

## Relationships

- Follows: [[ISSUE-63a946-extract-marcel-zoo-repo]] (Session A — closed as `d32c1a3`)
- Precedes: Session C (separate issue — first-boot clone, SETUP.md, zoo CI, integration-test migration, empty-zoo verify)

## Comments

### 2026-04-21 - Planner
Session-B scope came out narrower than the parent issue anticipated. The original task list named `EnableBanking`, `pyicloud`, `feedparser`, and a docker SDK as candidate habitat-only deps, but a `^(import|from) <name>` grep across `marcel-zoo/` shows none of them are imported — each habitat uses raw stdlib or the already-in-core `httpx`/`jwt`. This issue is therefore a two-file diff (zoo's new `pyproject.toml` + core's `pyproject.toml` minus 4 lines) rather than the multi-dep migration the parent hinted at. Good outcome — fewer moving parts.

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
