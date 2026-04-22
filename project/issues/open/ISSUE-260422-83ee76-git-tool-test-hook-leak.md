# ISSUE-83ee76: git-tool tests leak files into pre-commit-hook commits

**Status:** Open
**Created:** 2026-04-22
**Assignee:** Unassigned
**Priority:** High
**Labels:** bug, tests, dev-environment, tooling

## Capture

**Original request:** Discovered while creating ISSUE-081eeb. The `📝` commit unexpectedly included a stray `newfile.txt` with content `hello`. Root cause: the git tool tests in [tests/tools/test_core_tools.py](tests/tools/test_core_tools.py) write files at `tmp_path` and shell out to `git add` against them. During normal pytest runs those commands fail harmlessly (`tmp_path` is not inside any repo). But during a **pre-commit hook** run, git exports `GIT_DIR`, `GIT_WORK_TREE`, and `GIT_INDEX_FILE` into the hook's subprocess environment — and that leaks through `make check → pytest → asyncio.create_subprocess_shell(...)` into the `git add` call. The subprocess dutifully stages the file into the in-flight commit's index. Every `📝`/`🔧`/`✅` commit gets a junk file along for the ride.

**Resolved intent:** Fix the tests so they operate on an isolated tmp git repo, not the hook's in-flight index. Simple, narrow, local change. Do NOT try to sanitize `bash()`'s env globally — git tools inside a hook subprocess legitimately want the hook env; stripping it would break the agent's own `git_*` usage during self-modification.

## Description

### Reproducer

From a clean temp worktree on `main`:

```bash
git worktree add /tmp/repro main
cd /tmp/repro
touch project/issues/open/TEST.md   # any staged change
git add project/issues/open/TEST.md
git commit -m "test: does this get polluted?"
# Resulting commit will include both TEST.md AND a stray newfile.txt
```

### The mechanism

1. `git commit` launches the pre-commit hook with `GIT_INDEX_FILE=<in-flight index>`, `GIT_DIR=<worktree gitdir>`, `GIT_WORK_TREE=<worktree root>` in the env.
2. `.git/hooks/pre-commit` runs `make check`, which runs `pytest`, which runs `TestGit::test_git_add_returns_string` (and similar tests for `git_commit`, `git_push`).
3. The test creates `tmp_path/newfile.txt`, then calls `git_add(_ctx(str(tmp_path)), 'newfile.txt')`.
4. `git_add` calls `bash(ctx, 'git add newfile.txt')` → `asyncio.create_subprocess_shell('git add newfile.txt', cwd=<tmp_path>)`. **The subprocess inherits the hook's `GIT_*` env vars.**
5. `git add` reads `tmp_path/newfile.txt` (cwd-relative) and stages it into `GIT_INDEX_FILE` — the parent commit's index.
6. Test passes (it only asserts `isinstance(result, str)`). Parent commit succeeds. Surprise file in the tree.

### The fix

**In [tests/tools/test_core_tools.py](tests/tools/test_core_tools.py)**: every test that invokes a `git_*` tool needs a real, isolated git repo at its `tmp_path`. Add a fixture:

```python
@pytest.fixture
def git_repo(tmp_path, monkeypatch):
    """tmp_path initialized as a standalone git repo, with GIT_* env vars unset.

    Unsetting GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE prevents the test from
    accidentally targeting a parent git context (e.g. a pre-commit hook's
    in-flight commit — see ISSUE-83ee76).
    """
    for var in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE'):
        monkeypatch.delenv(var, raising=False)
    subprocess.run(['git', 'init', '-q'], cwd=tmp_path, check=True)
    subprocess.run(['git', 'config', 'user.email', 'test@example.com'], cwd=tmp_path, check=True)
    subprocess.run(['git', 'config', 'user.name', 'Test'], cwd=tmp_path, check=True)
    return tmp_path
```

Replace every `_ctx(str(tmp_path))` in the `TestGit*` classes (there are ~8 usages for `git_add`, `git_commit`, `git_push`, `git_status`, `git_diff`, `git_log`) with `_ctx(str(git_repo))`.

The fixture provides TWO layers of defense:

1. `monkeypatch.delenv` scrubs the leaking env — the subprocess no longer inherits the hook's index pointer.
2. `git init` gives the test its own `.git` dir so operations that DO find env vars via ancestor search (unlikely with #1, but belt-and-braces) still hit an isolated repo.

### Regression test

Two new tests in the same file:

```python
def test_git_add_does_not_pollute_outer_git_context(git_repo, monkeypatch):
    """Regression: tests must not stage files into a parent commit's index."""
    outer = tmp_path_factory_or_similar...  # a DIFFERENT git repo, simulating the hook
    monkeypatch.setenv('GIT_INDEX_FILE', str(outer / '.git' / 'index'))
    monkeypatch.setenv('GIT_DIR', str(outer / '.git'))
    monkeypatch.setenv('GIT_WORK_TREE', str(outer))
    (git_repo / 'scratch.txt').write_text('x')
    await git_add(_ctx(str(git_repo)), 'scratch.txt')
    # Confirm outer repo's index is untouched
    assert 'scratch.txt' not in subprocess.check_output(['git', '-C', str(outer), 'diff', '--cached', '--name-only']).decode()
```

The shape matters more than the exact fixture plumbing — point is: **the test must prove a pre-commit-hook-style env can't cause cross-repo staging**.

### Why not strip GIT_* in `bash()` itself

`bash()` is Marcel's admin-tier tool. During dev-mode self-modification, Marcel may legitimately call `git_*` from inside a pre-commit-hook subprocess (e.g. a meta-task that commits via the agent while another commit is in progress). Stripping GIT_* env globally would break that use case. Scope the fix to where it actually matters: the tests.

### Out of scope

- Refactoring `bash()` env handling. (Intentional — see above.)
- Migrating other tests to the fixture proactively. Only the tests in `TestGit*` hit the bug; others that use `tmp_path` don't shell out `git`.

## Tasks

- [ ] Add a `git_repo` fixture to [tests/tools/test_core_tools.py](tests/tools/test_core_tools.py) that `monkeypatch.delenv`s `GIT_DIR`/`GIT_WORK_TREE`/`GIT_INDEX_FILE` and `git init`s `tmp_path`.
- [ ] Replace every `_ctx(str(tmp_path))` inside `TestGit*` classes with `_ctx(str(git_repo))`.
- [ ] Add a regression test that sets fake `GIT_INDEX_FILE`/`GIT_DIR`/`GIT_WORK_TREE` env vars pointing at a second tmp repo, runs `git_add` in the primary `git_repo`, and asserts the second repo's index stays untouched.
- [ ] Reproduce the bug from a temp worktree (per the reproducer above) WITHOUT the fix, then confirm the fix makes it go away.
- [ ] `make check` passes with no stray file artifacts in the commit diff.

## Relationships

- Blocks: [[ISSUE-081eeb-issue-task-cli-reminder]] — its `📝` and `🔧` commits hit this bug and can't be made cleanly until this is fixed

## Comments

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
