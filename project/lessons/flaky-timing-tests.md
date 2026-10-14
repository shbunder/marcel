# Flaky timing tests

Three runner and hub tests failed now and then on a busy Mac and passed when rerun. Each was a race
between the test and the code under test, not a slow assertion. What fixed them:

- **Take a child's pid from the parent, not from the child.** A throwaway `claude` script that
  writes its own pid can be killed by a short timeout before Python has even run that line, so the
  pid file is missing for a perfectly good run. Record the pid where the process is started instead
  (`spawned_pids` in `runner/tests/test_cloud.py`: a `Popen` subclass patched onto the module under
  test, restored after the test).
- **A pid only the child knows: start the timeout when the child is ready.** A grandchild's pid can
  only come from the child. Give the code under test a clock that stands still until the child has
  written it down (`clock_from` in the same file), so the timeout counts from a known state rather
  than from a spawn the machine may spend starting Python.
- **Wait on the state you assert, not on a signal that comes before it.** The hub counts a failed
  rollover inside the spawn, does a database read, and only then closes the reading window. Waiting
  for the count and then checking the window caught the gap in between. Wait for both together
  (`test_a_failed_rollover_spawn_ends_the_reading_window`).
- **Reproduce by slowing one step.** Load alone made these fail about one run in ten. A scratch test
  that slowed exactly one step (the script's start, the alert lookup) made each fail every time, and
  showed the fix held. Delete the scratch test afterwards.
- **Never fix it with a bigger timeout.** Check the test still goes red with the code it guards
  removed (the kill, the reap, the line that closes the window).
