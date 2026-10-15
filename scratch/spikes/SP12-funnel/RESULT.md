# SP12 — A public address with Tailscale Funnel, no admin

Mac sitting done, 2026-10-15. The NUC sitting follows (`NUC.md`); the reboot test moved there.

| Step | Mac (user-space `tailscaled`, no sudo) |
|---|---|
| 1. Run without admin, sign in, Funnel on | **PASS** (one sign-in link, one tailnet policy link; the public name took ~13 min to resolve) |
| 2. `/api` only; `/channel` and `..` tricks blocked | **PASS** (all 404 at Tailscale, none reached the app) |
| 2. WebSocket 10 min | **PASS** from the Mac (661 s); phone on mobile data **PASS with a caveat** (reached twice, held 19 s and 109 s; a 10-minute phone hold moves to S-22.26) |
| 3. A cloud session's report | **PASS** |
| 4. Lid closed 10 min, Wi-Fi changed | **FAIL without help** (dead 15+ min), **PASS with a restart** of `tailscaled` (72 s); S-22.8 adds a watchdog |
| 4. Same address after a reboot | moved to the NUC sitting |

**Pass for S-22.8, with the watchdog.** Notes for it: the first `funnel` needs the tailnet admin's policy click; a new
public name takes minutes to resolve; Funnel marks relayed requests with `Tailscale-Funnel-Request: ?1`.

## Setup (Mac)

| | |
|---|---|
| Mac | macOS 26 (Darwin 25.5), Apple silicon; the user is in the `admin` group, but **nothing used sudo or asked for a password** |
| Tailscale | 1.104.1 (commit 9a522a978, go1.27.1), the open-source `tailscale` + `tailscaled` from Homebrew's `homebrew/core` bottle (`brew fetch --force-bottle tailscale`), unpacked into a scratch folder; **not installed** (`brew list` does not show it) |
| State | `~/.marcel-sp12/` (statedir and socket; the socket path must stay short on macOS) |
| App | `app.py` here on `127.0.0.1:7420`: `/api/health`, `/api/ws`, `/api/session/report`, `/api/phone-test`, and a private `/channel` that logs `CHANNEL-REACHED` if anything ever gets to it |

## Step 1 — user-space `tailscaled`, sign-in, Funnel

```bash
tailscaled --tun=userspace-networking --statedir=$HOME/.marcel-sp12 --socket=$HOME/.marcel-sp12/ts.sock --port=0
tailscale --socket=$HOME/.marcel-sp12/ts.sock up --hostname=marcel-sp12
tailscale --socket=$HOME/.marcel-sp12/ts.sock funnel --bg --set-path /api http://127.0.0.1:7420/api
```

- `tailscaled` started as the user, no prompt: **PASS** (14:27 UTC).
- `up` printed a login link (`https://login.tailscale.com/a/…`) and waited; the owner signed in from their own
  device; the node came up as `marcel-sp12`, `100.120.199.14`, about 25 minutes later (the wait was the owner, not
  Tailscale): **PASS**. The link stayed valid the whole time.
- The first `funnel` printed **"Funnel is not enabled on your tailnet. To enable, visit:
  https://login.tailscale.com/f/funnel?node=…"** and waited (the tailnet policy link: one click for the tailnet's
  admin, once per tailnet; it also turns on HTTPS certificates). After the owner's click it printed "Available on the
  internet: https://marcel-sp12.tail2af3bc.ts.net/api |-- proxy http://127.0.0.1:7420/api" (15:26 UTC): **PASS**.
- The Let's Encrypt certificate came within 30 s (`tailscaled` log: SetDNS for the ACME challenge, "got cert").
- **The public name took about 13 minutes to resolve** in public DNS (1.1.1.1, 8.8.8.8 and the Mac's resolver all
  empty until 15:39). A fresh install should say "your address is getting ready, a few minutes" rather than fail.
- Address shape: `https://<hostname>.<tailnet>.ts.net`, the tailnet part (`tail2af3bc`) chosen by Tailscale.

## Step 2 — reachability, `/api` only

From the Mac over the public internet (15:39 UTC):

| Request | Answer | Reached the app? |
|---|---|---|
| `GET /api/health` | 200 `{"ok":true}` in 0.7–1.4 s | yes |
| `POST /api/session/report` | 200 `{"ok":true,"received":34}` | yes |
| `GET /channel` | 404 | **no** |
| `GET /` | 404 | **no** |
| `GET /api/../channel` (`curl --path-as-is`) | 404 | **no** |
| `GET /api/%2e%2e/channel` | 404 | **no** |
| `GET /api%2f..%2fchannel` | 404 | **no** |
| `GET /apichannel` | 404 | **no** |

Tailscale itself answers the 404s; the app's log shows no `/channel` request from the internet. **PASS: only `/api`
is published.** Each relayed request carries `Tailscale-Funnel-Request: ?1`, so a hub can tell internet traffic from
local traffic.

From Anthropic's servers (Claude's WebFetch tool, 15:46 UTC): `GET /api/health` → `{"ok":true}`. **Step 3, a real
Claude Code cloud session** (started by the lead; on this Mac every folder stops `claude --cloud` at the "trust this
folder" prompt, which a spike must not accept for the owner): `POST /api/session/report` → `{"ok":true,"received":38}
HTTP 200 in 0.87 s`, and `GET /api/health` → 200. The app logged the report body `{"spike":"SP12","from":"claude-cloud"}`
at 15:47 UTC. **PASS: cloud reports reach the hub through Funnel.**

WebSocket (`wss://…/api/ws` from the Mac, the app sending a tick each 15 s and the client a ping every 20 s): opened
15:39:43 UTC, **held 661 s, closed only by the client** (the app logged `ws-close` after 660 s): **PASS**. Phone on mobile data (`/api/phone-test`), as the app's log saw it (UTC):

| Try | Page loaded | WebSocket open | Closed after | Note |
|---|---|---|---|---|
| 1 | 17:24:21 | 17:24:23 | 19 s | the owner probably left Safari to report back (iOS suspends it at once) |
| 2 | 17:25:25 | 17:25:26 | 109 s | cause not visible on the server; the Mac's 5-s probe of the address never failed meanwhile |

Every request carried the Funnel header, so the phone reached the app through Funnel over mobile data: **PASS for
reachability**. **Lead's call: PASS with a caveat** (the Mac held 11 minutes through
Funnel; the phone connected twice with no drop seen on the Funnel side; the real app's reconnect-and-replay covers
drops; S-22.26 measures a 10-minute hold on a phone with the real app). The 10-minute hold from the phone is **not
confirmed**: the owner's page showed "583 s", but the server
logged no third connection, and the page from try 2 shows a stale count when Safari does not deliver the close event.
The test page now reconnects by itself and lists each drop with its close code and whether the page was hidden, for a
clean retry (Auto-Lock off, Safari in front, 10 minutes).

## Step 4 — sleep and a network change

The probe (`probe.sh`, on the Mac itself) hits the public address every 5 s and logs each up/down change. It cannot
measure while the Mac sleeps.

| Time (UTC) | What happened | Source |
|---|---|---|
| ~17:37 | Lid closed; the Mac slept (one DarkWake 17:37:11–17:37:56) | `pmset -g log` |
| 17:50:51 | Lid opened: "Wake from Deep Idle … lid" | `pmset -g log` |
| 17:50:51 | The Mac came back on the owner's phone hotspot (172.20.10.x) instead of the home Wi-Fi | `tailscaled` log |
| 17:50:58 | Public address down: the connection reaches Tailscale's Funnel ingress, then the TLS handshake is cut (`SSL_ERROR_SYSCALL`); from Anthropic's servers too ("socket is closed") | probe, curl, WebFetch |
| 17:50:59–17:51:38 | `tailscaled`: DERP London "no route to host" over IPv6, control connection reset, then new endpoints found (UDP ok, DERP #14); `funnel status` still says on | `tailscaled` log |

**Possible factor: Cloudflare WARP.** This Mac also runs Cloudflare WARP (a VPN): its addresses (`2a09:bac5:…`,
`104.28.…`, `2606:4700:…`) are among `tailscaled`'s endpoints and are the source of its control connection. A Marcel
user's Mac may have a VPN too, so this matters either way.

| 17:55:11 | Back on the home Wi-Fi (192.168.0.x); `tailscaled` saw the link change at 17:55:15 | `tailscaled` log |
| 18:05:52 | Still down: 15 min after wake, over 10 min on the home Wi-Fi, no command run | probe |
| 18:06:13 | Restarted the test `tailscaled` only (lead's OK), same flags and state folder | — |
| 18:07:25 | **Up again, 72 s after the restart**; Anthropic's servers get `{"ok":true}`; same address, Funnel still configured | probe, WebFetch |

**Result: FAIL without help, PASS with a restart.** After sleep plus a network change, the user-space `tailscaled`
kept saying "Funnel on" but the address stayed dead for 15 minutes, even after the Mac was back on its usual Wi-Fi, so
the hotspot alone is not the cause. Restarting `tailscaled` brought it back in about a minute, with the same address
and Funnel config (it lives in the state folder). Not separated: whether WARP is part of the cause (this Mac kept WARP
on throughout); the NUC sitting (no WARP, no sleep) will tell more.

**What Marcel needs (S-22.8):** a watchdog in Marcel's service that checks its own public address after a wake or a
network change (and every few minutes) and restarts `tailscaled` when it stops answering for, say, 2 minutes; the app
should show "Your Mac is waking up, Marcel is reconnecting" meanwhile.


## Step 5 — notes

- **Sign-in:** a browser link only (`tailscale up` printed it and waited ~25 min for the owner without expiring). An
  auth key (`--auth-key`) would skip the browser, but making one needs the same account's admin page, so for a
  person installing Marcel the link is simpler. Not tried.
- **Policy link:** the first `funnel` printed `https://login.tailscale.com/f/funnel?node=…` and waited; one click by the
  tailnet's admin, which also turns on HTTPS certificates. Asked once per tailnet.
- **Limits seen:** none. Health answers in 0.7–1.4 s through Funnel; the WebSocket held 11 min with a message every
  15 s. Tailscale publishes no bandwidth figure.

## Clean-up (18:20 UTC)

`funnel reset` ("No serve config" after), `logout` (the node `marcel-sp12` leaves the tailnet), every test process
stopped (`tailscaled`, the app, the probe); the public address no longer answers. Nothing was installed: no Homebrew
package, no app, no login item. Left on disk because this session may not delete folders: `~/.marcel-sp12/` (356 KB,
the logged-out state) and the unpacked binaries in the session's scratch folder; `rm -rf ~/.marcel-sp12` removes the
first, and `brew cleanup tailscale` the downloaded bottle in Homebrew's cache.
