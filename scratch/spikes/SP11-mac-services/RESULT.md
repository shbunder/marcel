# SP11 — Marcel's sessions on a Mac laptop, without Docker: result

Run 2026-10-15 by marcel-src-a4 on the owner's MacBook Pro: **macOS 26.5.2 (25F84), arm64**, **Claude Code 2.1.290**
at the start; an automatic update moved the user's `~/.local/bin/claude` to **2.1.300** during the spike (12:28 UTC).
Run as the owner's own user, which is an **admin**; the fresh non-admin account SP11 asks for was skipped on the
lead's call, so **the non-admin case is untested**. Seven interactive Claude sessions shared this login throughout.

## Summary

| Step | Result | In one line |
|---|---|---|
| 1. Channel allowlist drop-in | **PARTLY** | The owner installed the drop-in with one password (2026-10-24). Channel registration from it is checked on the first real install (S-22.6 owns the drop-in) |
| 2. Supervisor next to a LaunchAgent runner | **PASS** (2026-10-24) | `claude --bg` from a LaunchAgent starts the on-demand supervisor in its own process group (ppid 1); `launchctl kickstart -k` of the runner leaves the supervisor and the session alive |
| 3. Login from a LaunchAgent | **PASS** (2026-10-24; reboot case not run) | `claude -p "/usage"` from a LaunchAgent read the plan usage from the Keychain login, exit 0, no prompt |
| 4. Private copy of the pinned binary | **FAIL** | It starts, but the first newer `claude` of the same user takes the supervisor over and moves Marcel's sessions onto its own version |
| 5. Sleep and wake | **PARTLY** | `caffeinate -i -w <pid>` works without admin (lid open, on power); lid, Wi-Fi and battery need the owner |
| 6. Trust in `~/.claude.json` | **PASS** | `projects["<path>"].hasTrustDialogAccepted: true` lets `claude --bg` start there |

**Verdict: not a pass yet.** Steps 1–3 are untested, and step 4 fails as ADR-013 designs it. F22 should not go ahead
on question 5 (the private pinned copy) as written: see "What changes" below.

## Step 4 — a private copy of the pinned binary: FAIL

What was run (the user's own `claude` was 2.1.300; 2.1.290 was the "pinned" version):

```bash
mkdir -p ~/marcel/claude/2.1.290
cp ~/.local/share/claude/versions/2.1.290 ~/marcel/claude/2.1.290/claude       # --version: 2.1.290
cd ~/marcel/sp11-work                                                            # trusted, step 6
DISABLE_AUTOUPDATER=1 ~/marcel/claude/2.1.290/claude --bg --name sp11-pin --model haiku "Reply with the single word ready. Then stop."
```

- **Works on its own.** With no supervisor running, the copy started one of its own version (`claude daemon status`:
  `version: 2.1.290`, `origin: transient — started on-demand by claude --bg`). The session answered "ready" on
  2.1.290. `claude agents --json` from the copy and from the user's 2.1.300 both listed it. The supervisor stayed on
  2.1.290 for over a minute although `~/.local/bin/claude` pointed at 2.1.300: it does not upgrade on a mismatch at
  start, only when the link *changes*.
- **A newer client takes it over.** The user's 2.1.300 then started a second session (`claude --bg --name sp11-new`).
  The daemon log:
  ```
  [supervisor] shutting down (cause=signal, uptime=123s, leases=0, live_workers=1)
  [supervisor] ─── daemon start ─── version=2.1.300 pid=10393 origin=transient
  [bg] bg adopt: adopted=1 respawned=0 dead=0
  [bg] bg: post-takeover prewarm burst — respawned 1/1 stale workers in 0s        (15 s later)
  ```
  After that, `sp11-pin` ran `~/.local/share/claude/versions/2.1.300 --resume …`: **the pinned session was moved
  onto the user's version**, 15 seconds after the takeover. Nothing was lost (it was adopted, then resumed), but the
  pin was gone.
- **The same happens on the user's own update.** Earlier the same day, the automatic update of the user's install
  restarted the supervisor while SP13's two background sessions were live:
  ```
  [supervisor] binary at ~/.local/bin/claude changed (…/versions/2.1.290 → …/versions/2.1.300) — self-restarting for upgrade
  [supervisor] shutting down (cause=upgrade, uptime=60s, leases=0, live_workers=2)
  [bg] bg adopt: adopted=2 respawned=0 dead=0
  [bg] bg: post-takeover prewarm burst — respawned 2/2 stale workers in 0s
  ```
- **`respawn`** works by session id (`claude respawn c9c69e67` → `respawned c9c69e67`), not by name (`No job matching
  'sp11-pin'`). The respawned session runs on one of the supervisor's spare processes, so on the supervisor's
  version, never on the version of the `claude` that asked.

**Why it fails:** there is one supervisor per user login (`~/.claude/daemon.lock`, one control socket in
`/tmp/cc-daemon-<uid>/`). A session always runs on the supervisor's version. The supervisor is replaced by the newest
client that talks to it, and by the user's own update, and then it moves every session onto that version. A private
copy with `DISABLE_AUTOUPDATER=1` only pins Marcel while the user never runs a newer `claude --bg` and never updates,
which is exactly what ADR-013 promised the user they could do.

**The fallback SP11 names** ("point Marcel at Claude's own versions folder and re-pin after each user update") does
not hold either, for the same reason: re-pinning cannot stop the takeover. What is left:

- **(a) Follow the user's version (recommended).** Marcel runs the user's `claude` and stops pinning. It must
  then tolerate an update under it: the runner reads the supervisor's version (`claude daemon status`) in its health
  check and the hub tells the owner when it changed ("Claude Code updated to 2.1.300; Marcel's sessions carried on").
  The NUC keeps its system-wide pin (it has no other user).
- **(b) A separate `CLAUDE_CONFIG_DIR` for Marcel.** Its own supervisor, lock and socket, so the user's sessions never
  touch Marcel's. Untested here on the lead's call; open: whether the Keychain login is shared or needs a second
  sign-in, and whether the user then sees Marcel's sessions in their own `claude agents`.
- (c) Turn updates off for the whole login, as the NUC does today. Rejected by ADR-013 for colleagues' Macs.

## Step 6 — trust: PASS

```bash
python3 -c '…'   # projects["/Users/<u>/marcel/sp11-work"].hasTrustDialogAccepted = true, written atomically
```

Before the entry: `Workspace not trusted. Run claude in … once and accept the trust prompt, then retry.` After it,
`claude --bg` started in that folder at once. The file is shared with every running interactive session of the user,
so the write must be atomic (write to a temporary file in `~`, then rename), as above.

## Step 5 — sleep and wake: PARTLY

```bash
caffeinate -i -w <pid of the background session> &
pmset -g assertions | grep caffeinate
# pid 13069(caffeinate): PreventUserIdleSystemSleep named: "caffeinate command-line tool"
#   Details: caffeinate asserting on behalf of Process ID 10457
```

Works without admin, and the assertion goes when the process it watches ends. Measured with the lid open, on AC power
(`pmset -g batt`: AC, 71 %). Not run: on battery, with the lid closed (Apple documents that a closed lid sleeps
regardless of `caffeinate -i`, unless the Mac is on power with an external display), the 10-minute lid close, the
Wi-Fi switch and the channel reconnect time. These need the owner at the Mac.

## Steps 1, 2 and 3: NOT RUN

- **Step 1** needs `sudo` to write `/Library/Application Support/ClaudeCode/managed-settings.d/50-marcel.json` (and,
  for "which list wins", a temporary `managed-settings.json` next to it). There is no `managed-settings.json` on this
  Mac today. The lead is asking the owner; the commands to paste are below.
- **Steps 2 and 3:** writing `~/Library/LaunchAgents/com.marcel.sp11.usage.plist` and `launchctl bootstrap
  gui/<uid>` were refused by this session's permission settings (the owner's), so no LaunchAgent was made. Two things
  learned without one change step 2:
  - **`claude daemon` cannot install a service in this version.** `claude daemon --help` (2.1.290): "Service install
    is disabled in this version — the daemon runs on demand and exits when the last client disconnects." Seen: after
    the last background session was removed, the supervisor quit by itself. Whether `claude daemon run` in the
    foreground stays up under a `KeepAlive` LaunchAgent with no sessions is still open.
  - **A `KeepAlive` LaunchAgent and the takeover may fight.** If Marcel's LaunchAgent runs `claude daemon run` and the
    user's newer `claude` signals it to stop (as above), `launchd` restarts Marcel's job, which then meets the user's
    supervisor holding the lock. Untested; it must be checked before S-22.4 relies on a `claude-daemon` LaunchAgent.
  - **Step 3, partly:** background sessions started from a shell use the Keychain login with no prompt; the
    supervisor logs `auth: scheduling proactive refresh in 9263s`. From a LaunchAgent, after a reboot and before
    anyone logs in: not run.

### For the owner, if step 1 goes ahead (paste in Terminal; asks for the password)

```bash
sudo mkdir -p "/Library/Application Support/ClaudeCode/managed-settings.d"
sudo tee "/Library/Application Support/ClaudeCode/managed-settings.d/50-marcel.json" >/dev/null <<'EOF'
{"channelsEnabled": true, "allowedChannelPlugins": [
  {"marketplace": "marcel-local", "plugin": "marcel"},
  {"marketplace": "claude-plugins-official", "plugin": "telegram"},
  {"marketplace": "claude-plugins-official", "plugin": "imessage"},
  {"marketplace": "claude-plugins-official", "plugin": "discord"}]}
EOF
# afterwards, to remove it:
sudo rm "/Library/Application Support/ClaudeCode/managed-settings.d/50-marcel.json"
sudo rmdir "/Library/Application Support/ClaudeCode/managed-settings.d" "/Library/Application Support/ClaudeCode"
```

## What changes in the stories

- **S-22.6 (Marcel's own Claude Code version):** do **not** build the private copy as written; its "done when" (after
  the user's `claude update`, Marcel's sessions unchanged) cannot pass with one supervisor per login. Take option (a):
  run the user's `claude`, record the supervisor's version in the health check, tell the owner when it changes. The
  drop-in part stands, but waits for step 1. Or run a small spike on option (b) first.
- **S-22.4 (the `marcel` command and its services):** the `claude-daemon` service is in doubt: Claude Code runs its
  supervisor on demand and has turned service install off. Before keeping it in the service table, check that a
  `KeepAlive` job running `claude daemon run` does not fight a takeover by the user's newer `claude`. If it does, drop
  the `claude-daemon` service on macOS and let Marcel's first `claude --bg` start the supervisor, as everything else
  does.
- **S-22.3 (the runner on macOS):** "the supervisor's pid is the pid of the `com.marcel.claude-daemon` LaunchAgent"
  only works if that job keeps the supervisor (see S-22.4). With an on-demand supervisor, the runner should read
  `claude daemon status` (pid, version, `origin`) instead; `origin: transient — started on-demand by claude --bg (pid …)
  in <cwd>` says who started it. The runner must never stop the supervisor while sessions it did not start are on it.
- **S-22.18 (keep the Mac awake):** `caffeinate -i -w <pid>` works without admin and ends with the process, so the
  runner can hold one per live worker, or one for itself while any worker lives. The docs must say a closed lid on
  battery still sleeps. Lid, battery and Wi-Fi measurements are still owed (step 5).

## What was changed on the Mac, and undone

| Change | Undone |
|---|---|
| `~/marcel/claude/2.1.290/claude` (copy of 2.1.290), `~/marcel/sp11-work/` | Deleted; `~/marcel` no longer exists |
| `projects["~/marcel/sp11-work"].hasTrustDialogAccepted` in `~/.claude.json` | Removed (atomic write); a backup of the file was kept outside the repo |
| Background sessions `sp11-pin` (c9c69e67) and `sp11-new` (cf8b2159) | Stopped and removed (`claude stop`, `claude rm`) |
| An on-demand supervisor (2.1.290, then 2.1.300) | Quit by itself once no session was left; `claude daemon stop` was not needed |
| LaunchAgents, `/Library` files | None made |

Usage: three short Haiku turns on the owner's subscription.


## Second sitting, 2026-10-24 (macOS 27.0.1, Claude Code 2.1.300)

The owner ran the LaunchAgent and drop-in commands by hand (this session's permission settings refuse `launchctl`
and writes to `~/Library/LaunchAgents`). Files: `la/` (runner.sh, usage.sh, the two plists and their logs).

- **Step 3:** `com.marcel.sp11.usage` (one-shot) printed the real `/usage` screen, `exit=0`. The Keychain login works
  from a LaunchAgent in the `gui/501` domain. **Finding:** launchd's environment has no `node` on `PATH`; a SessionEnd
  hook from another plugin failed with `node: command not found`. S-22.4's plist must put Marcel's node 22 first on
  `PATH` (it already says so) — this is now evidence, not a guess.
- **Step 2:** first run refused with "Workspace not trusted": a sub-folder of a trusted repo is **not** trusted by
  inheritance. After writing `projects[<folder>].hasTrustDialogAccepted` (step 6's method) and `kickstart -k`, the
  runner started `sp11-probe`. Process table:
  - runner.sh: pid 11103, pgid 11103
  - supervisor `claude daemon run --origin transient`: pid 11199, **ppid 1, pgid 11199** (own group)
  - session: pid 11297, under `bg-pty-host`, pgid 11297
  Between the two kickstarts the supervisor restarted with `cause=upgrade` (10:37:28Z) and adopted the session
  (`adopted=1`) — the takeover SP11 described, triggered by another client. The second `launchctl kickstart -k`
  (10:38:54Z) restarted the runner (new pid 14195); the supervisor (12591) and the session (13447) **survived**, and
  `claude agents --json` still listed `sp11-probe`. So S-22.3's check is: the supervisor's pgid differs from the
  runner job's, and a runner restart never takes sessions down. `AbandonProcessGroup` is not needed.
- **Not run:** a reboot with automatic login, and "before anyone logs in" (expected: nothing runs, as LaunchAgents
  need a GUI login). Step 1's channel registration is checked on the first real Mac install.
- **Cleaned up:** the session stopped, the trust entry removed. The owner removes the two plists and the drop-in.
