# Rule — debugging triage

When something is broken — a failing test, a 500 from a route, a skill returning garbage — work through the same five steps in order. Do not skip ahead.

## The loop

1. **Reproduce.** Get the bug to happen reliably, on demand. If you cannot reproduce it, you cannot fix it; you can only guess. Capture the exact command, input, and expected-vs-actual output.
2. **Localize.** Narrow the failure to one function, one call site, or one commit. Use `git bisect`, `pytest -k`, log statements, or a debugger — whichever is fastest for *this* bug. The goal is a single place to stare at, not a neighbourhood.
3. **Reduce.** Shrink the repro to the smallest input that still fails. A three-line test beats a full integration run every time; a minimal repro often reveals the cause on its own.
4. **Fix.** Change the code. Per [CODING_STANDARDS.md](../../project/CODING_STANDARDS.md) (Tests), the minimal repro from step 3 becomes a failing test *before* you touch production code.
5. **Guard.** Leave the regression test in place. If the bug revealed a class of problems wider than the single failure — e.g. unchecked `None`, missing timeout, silent swallow — add an assertion, type narrowing, or log so the next instance fails loud instead of quiet.

## Never

- **Never guess-and-check.** Editing code hoping it'll fix the failure, running the test, and editing again is debugging by accident. Reproduce first, hypothesise, *then* edit.
- **Never delete a failing test to make CI green.** The failing test is evidence. If the test is wrong, prove it's wrong — then fix the test, not hide it.
- **Never fix a symptom when you can see the cause.** Wrapping the crash site in `try/except` and logging a warning is not a fix; it is a cover-up that will rot into a silent failure mode. See also [CLAUDE.md](../../CLAUDE.md) on root causes.
- **Never `print`-debug into committed code.** Debug prints go away before the commit. If you need persistent visibility, use the logger at the appropriate level.

## Always

- **Always reproduce first.** Even if the bug "looks obvious". Obvious bugs that aren't reproduced are obvious bugs in the *wrong* place 20% of the time.
- **Always write the regression test in the same commit as the fix.** Not in a follow-up. Per [docs-in-impl](./docs-in-impl.md) and [closing-commit-purity](./closing-commit-purity.md), the fix and its evidence ship together.
- **Always note any wider guard you added in the Implementation Log.** "Fixed X, added assertion in Y because the same class of bug could recur there" is exactly the context the next reader needs.

## Why

Marcel runs on a home server for a family. A bug that gets "fixed" by guessing is a bug that comes back — usually at dinner time on a Sunday, when the zoo keeper is not at a laptop. The five-step loop is slower than guessing for easy bugs and dramatically faster for hard ones; applying it uniformly means nobody has to decide which kind this is at the start, when that decision is unreliable.

## Common rationalizations

| Excuse | Reality |
|--------|---------|
| "I can see the fix from the stack trace, I don't need to reproduce" | The stack trace tells you *where* it crashed, not *why* the offending state existed. Reproduce, confirm, fix. |
| "Writing a test for this is hard" | Then write a test at the nearest layer where it is easy — harness, storage, channel. An integration test is better than no test. |
| "The bug only happens in prod, I can't reproduce it locally" | Then the first step is making it reproducible — add logging, capture the real input, run it against dev. "Non-reproducible" is the bug's disguise, not its nature. |
| "The fix is one line, a regression test is overkill" | One-line fixes are the ones that come back. The test takes two minutes and costs one line of suite runtime forever. |

## Enforcement

- [.claude/agents/code-reviewer.md](../agents/code-reviewer.md) flags fixes that land without a regression test covering the reported failure.
- [.claude/agents/pre-close-verifier.md](../agents/pre-close-verifier.md) flags `🔧 impl:` commits that claim to fix a bug but do not add or modify a test.
