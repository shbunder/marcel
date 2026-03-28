# ISSUE-008: CLI Installer, Pride Versioning, and Header Polish

**Status:** Closed
**Created:** 2026-03-28
**Closed:** 2026-03-28
**Assignee:** Claude
**Priority:** Medium
**Labels:** feature, cli, ux, versioning, docs

## Capture

**Original requests (in order):**
1. "I want to create an installer for the cli (so I can install it on my laptop and link to the service running on my NUC)"
2. "I should be able to set the host and port"
3. "use a more random standard port number for the backend than 8000"
4. "I want to see the version of the cli and backend (to see if they are in sync)"
5. "highlight the user name in the header so it's more easily visible"
6. "add some rules for versioning to the project, use the pride approach: PROUD.DEFAULT.SHAME"
7. "ensure that changes have impact on the version"
8. "commit the changes properly"

**Resolved intent:** Two related improvements. First, a standalone CLI installer (`install.sh` + `make install-cli`) using `uv tool install`, with host/port/user flags for configuring the connection to a remote Marcel server. The default port is changed from the generic 8000 to 7420. The CLI header is updated to show both CLI and backend versions (fetched live from `/health`), with the user name highlighted in the brand color. Second, a Pride Versioning convention (`PROUD.DEFAULT.SHAME`) is documented and integrated into the Ship step of the dev procedure.

## Tasks

- [✓] Create `install.sh` — `uv tool install` with `--host`, `--port`, `--user` flags that pre-configure `~/.marcel/config.toml`
- [✓] Add `make install-cli` Makefile target
- [✓] Change default port from `8000` to `7420` in `config.py`, `.env`, and default config template
- [✓] Add `__version__` to `marcel_cli/__init__.py`, decouple CLI from `marcel_core` import
- [✓] Fetch backend version from `/health` on startup (2s timeout, shows `offline` in red if unreachable)
- [✓] Update header: `CLI v{x}` / `Server v{x}` / model / user (user now in brand blush-rose `#cc5e76`)
- [✓] Update `/status` command to show both `cli:` and `backend:` version lines
- [✓] Create `project/VERSIONING.md` — Pride Versioning rules, segment table, version file locations
- [✓] Update `project/CLAUDE.md` Ship step to require a version bump per change
- [✓] Sync `pyproject.toml` to `0.1.0`; bump CLI to `0.2.0` (DEFAULT) for this release

## Implementation Log

### 2026-03-28 - LLM Implementation
**Action**: Implemented CLI installer, version display, port change, header polish, and Pride Versioning docs.
**Files Modified**:
- `install.sh` — new file; installs CLI via `uv tool install`, writes `~/.marcel/config.toml`
- `Makefile` — added `install-cli` target
- `.env` — port `8000` → `7420`
- `src/marcel_cli/__init__.py` — added `__version__ = '0.2.0'`
- `src/marcel_cli/app.py` — replaced `marcel_core` import with CLI-local version; added `_fetch_server_version()` async helper; updated `_print_header` to show both versions and highlight user; updated `/status` handler
- `src/marcel_cli/config.py` — default port `8000` → `7420`; default config template updated
- `pyproject.toml` — version `0.0.1` → `0.1.0`
- `project/VERSIONING.md` — new file; Pride Versioning rules
- `project/CLAUDE.md` — Ship step references VERSIONING.md
**Result**: CLI installs cleanly with `./install.sh`, header shows live CLI + server versions, user name highlighted in brand color. Pride Versioning documented and enforced in dev procedure.
