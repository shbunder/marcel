# ISSUE-0baea6: Author marcel-zoo/pyproject.toml and split habitat-only deps out of marcel-core

**Status:** Closed
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

- [✓] Survey marcel-zoo's actual import surface to confirm caldav + vobject are the only habitat-only deps (done pre-issue; result documented above).
- [✓] Author `/home/shbunder/projects/marcel-zoo/pyproject.toml` with: `[project]` metadata, `dependencies = [caldav, vobject]`, `[build-system]` hatchling, `[tool.pytest.ini_options]` mirroring the current `pytest.ini`. Commit in the zoo repo with `add pyproject.toml`.
- [✓] Delete `/home/shbunder/projects/marcel-zoo/pytest.ini` in the same zoo commit (config moves to pyproject).
- [✓] Update marcel-zoo's `conftest.py` docstring to stop referencing the "no pyproject.toml" state now that it exists. Commit in the zoo repo.
- [✓] Remove the `[project.optional-dependencies].zoo = [...]` block from marcel-core's `pyproject.toml`.
- [✓] Verify `uv sync --all-extras` in marcel-core still succeeds and `make check` passes (no hidden import of caldav/vobject from kernel code).
- [✓] Update [docs/plugins.md](../../docs/plugins.md) if it mentions the `zoo` extra; otherwise confirm no doc references.
- [✓] Straggler grep: `grep -rn "caldav\|vobject" docs/ ~/.marcel/ .claude/ src/marcel_core/ Makefile Dockerfile docker-compose*.yml README.md SETUP.md` — confirm every match is either in zoo-checkout paths (not kernel source) or is a habitat that owns that dep.
- [✓] Run `make check` (format + lint + typecheck + tests, 90% coverage).
- [✓] Close via `/finish-issue` and merge to main.

## Relationships

- Follows: [[ISSUE-63a946-extract-marcel-zoo-repo]] (Session A — closed as `d32c1a3`)
- Precedes: Session C (separate issue — first-boot clone, SETUP.md, zoo CI, integration-test migration, empty-zoo verify)

## Comments

### 2026-04-21 - Planner
Session-B scope came out narrower than the parent issue anticipated. The original task list named `EnableBanking`, `pyicloud`, `feedparser`, and a docker SDK as candidate habitat-only deps, but a `^(import|from) <name>` grep across `marcel-zoo/` shows none of them are imported — each habitat uses raw stdlib or the already-in-core `httpx`/`jwt`. This issue is therefore a two-file diff (zoo's new `pyproject.toml` + core's `pyproject.toml` minus 4 lines) rather than the multi-dep migration the parent hinted at. Good outcome — fewer moving parts.

## Implementation Log
<!-- Append entries here when performing development work on this issue -->

### 2026-04-21 - LLM Implementation
**Action**: Author marcel-zoo/pyproject.toml and drop the habitat-only `[project.optional-dependencies].zoo` block from marcel-core.
**Files Modified**:
- `pyproject.toml` (marcel-core) — removed the entire `zoo = [caldav, vobject]` optional-deps block plus its migration-history comment.
- `uv.lock` (marcel-core) — refreshed by `uv sync --all-extras --all-packages --group dev --group lint --group docs`; removed 12 package rows (`caldav`, `vobject`, and the 10 transitives that no other dep still pulls in: `dnspython`, `icalendar`, `icalendar-searcher`, `jh2`, `niquests`, `qh3`, `recurring-ical-events`, `urllib3-future`, `wassima`, `x-wr-timezone`). `tzdata` was uninstalled from the venv but stays in `uv.lock` because another kernel dep still requires it.
- `project/issues/open/ → wip/` move for this issue file.

**Zoo-side changes** (separate repo `/home/shbunder/projects/marcel-zoo/`, commit `cc9da47`):
- `pyproject.toml` — new file. `[project]` with `name = "marcel-zoo"`, `version = "0.1.0"`, `requires-python = ">=3.11,<3.13"`, `dependencies = ["caldav>=3.1.0", "vobject>=0.9.9"]`. `[build-system]` hatchling with an explicit comment explaining why there is no `[tool.hatch.build.targets.wheel]` (the zoo is not packaged — habitats are discovered from `MARCEL_ZOO_DIR` at runtime, not via `import marcel_zoo`). `[tool.pytest.ini_options]` folded in from the old `pytest.ini`.
- `pytest.ini` — deleted; config moved into `pyproject.toml`.
- `conftest.py` — docstring updated: dropped the "zoo currently has no pyproject.toml" framing and rewrote the final paragraph to reference Session C of ISSUE-63a946 as the point where the `sys.path` shim goes away.

**Commands Run**:
- `uv sync --all-extras --all-packages --group dev --group lint --group docs` — refreshed uv.lock, uninstalled 14 caldav/vobject-adjacent packages from the kernel venv.
- `uv pip install --reinstall-package urllib3 urllib3` — healed a uv quirk where `urllib3-future`'s uninstall left the real `urllib3-2.6.3.dist-info` metadata in place but with the package directory deleted, breaking `import urllib3` for the OTel exporter during test collection.
- `make check` — format + lint + typecheck + tests with coverage. 1332 passed, coverage 91.35% (above the 90% gate).

**Result**: Success. marcel-core's dep tree now reflects only kernel deps; marcel-zoo's reflects only habitat deps. Kernel venv shrank by 13 packages. The `make serve` / `docker-compose.dev.yml` / Dockerfile paths (all of which run `uv sync --all-extras`) still succeed — `--all-extras` now just installs the `browser` extra, which is exactly what they need.

**Next**: Session C of ISSUE-63a946 — first-boot zoo clone in Dockerfile/entrypoint, SETUP.md + docs/claude-code-setup.md updates for the two-repo model, zoo CI, cross-habitat integration-test migration from marcel-core `tests/` to the zoo, empty-zoo error verification, and replacing the zoo's `conftest.py` `sys.path` shim with a proper editable install against marcel-core.

**Reflection** (via pre-close-verifier):
- Verdict: APPROVE
- Coverage: 10/10 tasks addressed
- Shortcuts found: none
- Scope drift: none — diff is exactly the 3 expected files (`pyproject.toml`, `uv.lock`, issue-file rename)
- Stragglers: none — `caldav`/`vobject`/`[zoo]` extra grep across `src/`, `docs/`, `.claude/`, `~/.marcel/`, Makefile, Dockerfile, `docker-compose*.yml`, README, SETUP returned zero hits outside the issue file itself and historical closed-issue records (which correctly describe the old state). The only `.zoo` hits in kernel code are `settings.zoo_dir` (the zoo **directory** setting, unrelated to the extras group).
- Auditability correction (flagged by verifier, addressed): Implementation Log originally said "13 packages removed including tzdata" — actual count is 12 (tzdata stays in uv.lock via another dependent). Corrected in the log.

## Lessons Learned

### What worked well
- **Survey-before-scope.** Running `grep '^(import|from) <name>'` across the zoo *before* writing the issue converted a speculated multi-dep migration (EnableBanking, pyicloud, feedparser, docker SDK — none of which are actually imported) into a confirmed two-dep migration (caldav + vobject). That 10-minute survey saved a lot of issue churn and let the Comments section document *why* the scope is smaller than the parent anticipated.
- **One atomic `🔧 impl:` commit per repo.** marcel-zoo commit `cc9da47` and marcel-core commit `f7a1f1d` each stand alone and make sense read in isolation. The zoo commit doesn't mention marcel-core file paths; the core commit references the zoo commit hash so the pair is cross-linked but independently readable.
- **Keeping the zoo's `conftest.py` `sys.path` shim for now.** Session B is about declaring deps, not about changing how the zoo finds `marcel_core`. The shim is ugly but works; Session C replaces it with an editable install. Resisting the urge to fix the shim "while we're here" kept the diff tight.

### What to do differently
- **Don't guess package counts for commit messages.** The initial Implementation Log said "13 packages removed" based on the `uv sync` output, which conflates venv-uninstall (14 lines) with lock-removal (12 lines). For this kind of mechanical claim, `git diff main...HEAD -- uv.lock | grep -c '^-name = "'` is the authoritative source — use it instead of eyeballing `uv sync` output. The verifier caught this; next time I'll pre-check.
- **Be suspicious of `urllib3-future` and similar drop-in replacements.** When `uv sync --all-extras` uninstalled `urllib3-future` (a caldav transitive), it left the real `urllib3-2.6.3.dist-info` metadata in place but deleted the package directory, silently breaking `import urllib3` for OTel's test-time import chain. `uv pip install --reinstall-package urllib3 urllib3` healed it. Worth knowing any time a dep removal uninstalls a `*-future` / `*-legacy` / shim package — verify the underlying module still imports before running tests.

### Patterns to reuse
- **"Survey results" table in the issue Description** (this issue, lines 22–29). When the parent issue's scope guess needs correction, a 3-column table (`Dep | Used by | Destination`) is faster to write and audit than prose, and the verifier can mechanically compare it to the diff. Reuse whenever a dep split / API surface split / file move is driven by a speculative parent issue.
- **Cross-repo commit cross-linking.** Mention the zoo commit hash in the marcel-core commit message body (and vice versa, ideally) when the change lands in both repos. A future reader doing `git log --oneline` on one repo can find the paired change in the other without having to grep by date.
- **`[build-system] hatchling` without `[tool.hatch.build.targets.wheel]`** (zoo pyproject, line 26–31 + comment). The pyproject exists to declare deps for `uv sync` / `pip install -e .` but the project is not packaged — the kernel discovers it via filesystem walk. Document the intent in a comment so the next reader doesn't "helpfully" add a wheel target and break the zoo's loader model.
