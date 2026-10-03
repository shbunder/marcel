# F07 — Cloud workers  (wave 2)

### S-07.1  Cloud task lifecycle                              runs-on: cloud · size: M
depends: S-03.6, S-04.5, SP3   touches: hub/src/marcel_hub/cloud.py hub/tests/
behaviours: B-04, B-06, B-10
do:
1. `tasks_spawn(location=cloud)` → `runner.cloud_spawn` with a prompt prefix that holds:
   - the worker protocol (report over HTTPS to `marcel-bot.com/report` with a per-task token);
   - the repo;
   - the expected outcome.
2. Store the session id and URL.
3. Track state with whatever SP3 proved works. The minimum is: the worker's own reports, plus a deadline watchdog.
4. Follow-ups use `runner.cloud_send`.
done when:
- [ ] Fake-runner test: spawn → report(progress) → report(done, PR artifact) → milestone with the PR card.
- [ ] Test: no report by `expected_by` → task `silent` → one "something broke" notification (B-23).

### S-07.2  Report endpoint                                   runs-on: cloud · size: S
depends: S-07.1   touches: hub/src/marcel_hub/report_api.py hub/tests/
do: `POST /report` takes a task-scoped token (minted at spawn, single task, expires after 48 h) and accepts the same
payloads as the channel `report`.
done when:
- [ ] Wrong task, expired token, or replay after done → rejected, each with a test.

# F08 — Notifications  (wave 2)

### S-08.1  Notifier interface, ntfy backend, routing         runs-on: cloud · size: M
depends: S-04.1   touches: hub/src/marcel_hub/notify/ hub/tests/
behaviours: B-16, B-18
do:
1. Define `Notifier.send(device, title, body, deep_link, level, collapse_key)` with two backends: ntfy (HTTP POST to a
   topic; the server URL is configurable) and APNs (S-08.2).
2. Routing policy: send on `needs_you`, `done`, `failed`, `silent`, `broke` and `digest`. Never send on progress.
3. Rate limits:
   - collapse repeats per task;
   - one "broke" per cause per hour;
   - a failure to send is logged, never raised into the caller.
done when:
- [ ] Routing table test.
- [ ] Test: the ntfy backend posts the right payload (`respx`).
- [ ] Test: a dead ntfy server does not break task processing.

### S-08.2  APNs backend                                      runs-on: cloud · size: S
depends: S-08.1   touches: hub/src/marcel_hub/notify/apns.py hub/tests/
do: Token-based APNs over HTTP/2 (`.p8` key from `deploy/.env.local`). The deep link carries the task id or message id.
The backend is disabled when no key is configured, and the app then registers an ntfy topic instead.
done when:
- [ ] Test: JWT signing and the payload shape, with a mocked APNs endpoint.
- [ ] Test: 410 Unregistered removes the device token.

# F09 — Scheduler, digests and watchdogs  (wave 3)

### S-09.1  Scheduler and schedules API                       runs-on: cloud · size: M
depends: S-04.4, S-05.3   touches: hub/src/marcel_hub/schedule/ hub/tests/
behaviours: B-22, B-23
do:
1. Use APScheduler with a SQLite job store. Each schedule has `location`:
   - `brain`: send a system event to the brain;
   - `nuc` or `cloud`: spawn a worker task with the prompt.
2. Pause, resume and delete. Missed fires are not made up; they are reported once.
3. A schedule with `deadline_minutes` raises "did not run" if no run started by then.
done when:
- [ ] Test with a frozen clock: fire, pause, a missed fire reported once, and the deadline alert.

### S-09.2  Digests                                           runs-on: cloud · size: M
depends: S-09.1, S-08.1, S-05.5   touches: hub/src/marcel_hub/digest.py brain/ hub/tests/
behaviours: B-17
do:
1. Four built-in schedules: morning 07:00, evening 21:00, weekly Sunday 18:00, and usage (part of evening).
2. The hub assembles a **facts bundle**: open approvals, threads in flight, done today, PRs ready, failures,
   usage by task, stale threads, and schedules today.
3. The bundle goes to the brain as a `digest_due` system event. The brain writes the digest with the
   `digest` skill → main chat + push.
done when:
- [ ] Test: the facts bundle is correct for a seeded DB.
- [ ] Live test (NUC): one digest written end to end.

# F10 — Usage guard  (wave 2)

### S-10.1  Guard and queue                                   runs-on: cloud · size: M
depends: S-03.7, S-04.4, S-08.1   touches: hub/src/marcel_hub/usage.py hub/tests/
behaviours: B-19
do:
1. Poll `runner.usage` every 5 min (every 1 min above 70 %).
2. At or above the threshold (default 90 % of the five-hour window, or 95 % weekly):
   - new spawns queue with reason `usage`;
   - running work continues;
   - one notification with the reset time.
3. After `resets_at`, drain the queue.
4. Also trigger on a detected limit hit (SP4), even when the reading was stale.
5. Attribute usage to tasks for the usage report, using the best available signal (token counts in transcripts).
done when:
- [ ] Test: above the threshold the spawn is queued, not started, and removing the guard makes this test fail.
- [ ] Test: the reset drains the queue in FIFO order.
- [ ] Test: a stale reading never blocks forever (fail-open after 30 min, with a log).

# F11 — Self-modification via PR  (wave 3)

### S-11.1  Self-change tasks and approval                    runs-on: cloud · size: M
depends: S-04.6, S-07.1   touches: hub/src/marcel_hub/selfmod.py plugins/marcel/skills/self-improve/ hub/tests/
behaviours: B-24
do:
1. A self-change is an ordinary task against `shbunder/marcel` that must end with a PR artifact flagged
   `self_change=true`.
2. The hub posts an approval card ("Marcel wants to change itself: <PR title>") with the diff stats.
3. Approve → merge through the GitHub API (squash, no force) → `request_redeploy(sha)`.
done when:
- [ ] Test: no merge without an approval, and removing the check makes this test fail.
- [ ] Test: deny closes the card and comments on the PR.

### S-11.2  Redeploy and rollback                             runs-on: NUC · size: M
depends: S-11.1, S-14.1   touches: deploy/redeploy.sh deploy/systemd/ hub/src/marcel_hub/watchdog/
do:
1. `request_redeploy` writes the `~/marcel/state/redeploy.flag` file (sha). A `marcel-redeploy.path` unit runs
   `redeploy.sh`:
   - pull;
   - rebuild the hub image;
   - restart the runner if `runner/` changed;
   - sync the brain template.
2. A health watchdog polls `/health` for 120 s. On failure it runs `git revert` on the merge, redeploys, and notifies.
done when:
- [ ] A live drill on the NUC: a deliberately broken commit is rolled back automatically, with a notification received.
