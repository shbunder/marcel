# Rule — debugging: reproduce, localize, reduce, fix, guard

1. **Reproduce** reliably, ideally as a failing test.
2. **Localize** to one function, one call site or one commit.
3. **Reduce** to the smallest input that still fails.
4. **Fix** the cause, not the symptom. The failing test is written first.
5. **Guard**: the test stays. If the bug is one of a class, add the assertion or log that makes the
   next one fail loudly.

## Never
- Delete, skip or `xfail` a failing test to get green
- Lower `fail_under` or a vitest threshold to get green: they are ratchets and only go up
- Mark a test `live` to get it out of the gate when it really needs a fixture
- Change an assertion to match what you observed, unless you can say in one sentence why it was wrong
