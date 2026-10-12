# Rule — every control has a test that fails without it

A **control** is code whose only job is to stop something: the concurrency cap, the usage guard, the approval gate,
the device-token check, the pairing expiry, the redeploy rollback, the guard hook.

For each one there is a test that goes **red when the control is deleted**. A test that still passes with the
control removed proves nothing.

## How to check

Comment out the control (or make it return "allow"), run the lane gate, and confirm a test fails with a message
that names the control. Restore it. Mention the test by name in the PR description.

## Why

Controls are the code that is never exercised on a good day. Without a test that depends on them, a refactor removes
one silently and nobody notices until the day it was needed.
