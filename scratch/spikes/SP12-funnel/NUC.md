# SP12 on the NUC — Tailscale Funnel as the owner, no sudo

For the NUC session. Every command runs as the owner's own user: **never `sudo`**. If any step asks for root, stop
and write down the exact message: that is the result.

Everything lives in `~/sp12/` and is deleted at the end. Nothing is installed system-wide, no service is added.

## 1. Download (tailscale.com only)

```bash
mkdir -p ~/sp12/state && cd ~/sp12
V=1.104.1                         # the version the Mac sitting used
curl -fsSLO https://pkgs.tailscale.com/stable/tailscale_${V}_amd64.tgz
curl -fsSL https://pkgs.tailscale.com/stable/tailscale_${V}_amd64.tgz.sha256   # compare with:
sha256sum tailscale_${V}_amd64.tgz
tar -xzf tailscale_${V}_amd64.tgz && mv tailscale_${V}_amd64/tailscale tailscale_${V}_amd64/tailscaled . && ./tailscale version
```

## 2. The throwaway app

Copy `scratch/spikes/SP12-funnel/app.py` from the repo to `~/sp12/`, then:

```bash
cd ~/sp12 && nohup uv run --with fastapi --with uvicorn uvicorn app:app --host 127.0.0.1 --port 7421 > uvicorn.log 2>&1 &
curl -s http://127.0.0.1:7421/api/health
```

(Port 7421, not 7420: the NUC's real hub may use 7420.)

## 3. tailscaled as the user, user-space networking

```bash
cd ~/sp12 && nohup ./tailscaled --tun=userspace-networking --statedir=$HOME/sp12/state \
  --socket=$HOME/sp12/state/ts.sock --port=0 > tailscaled.log 2>&1 &
./tailscale --socket=$HOME/sp12/state/ts.sock up --hostname=marcel-sp12-nuc
```

`up` prints a login link: send it to the lead (the owner approves it in the same tailnet as the Mac test). Record
whether anything asked for root.

## 4. Funnel `/api` only

```bash
./tailscale --socket=$HOME/sp12/state/ts.sock funnel --bg --set-path /api http://127.0.0.1:7421/api
./tailscale --socket=$HOME/sp12/state/ts.sock funnel status
```

If it prints a link to enable Funnel or HTTPS for the tailnet, send that to the lead too (already done on the Mac
sitting, so probably not asked again).

## 5. Checks (from the NUC itself and the Mac)

```bash
H=https://marcel-sp12-nuc.<tailnet>.ts.net          # from `funnel status`
curl -s $H/api/health                                 # 200 {"ok":true}
curl -s -o /dev/null -w '%{http_code}\n' $H/channel   # must not be the app's 200
curl -s --path-as-is -o /dev/null -w '%{http_code}\n' $H/api/../channel
curl -s -X POST -d '{"spike":"SP12"}' $H/api/session/report
grep CHANNEL-REACHED ~/sp12/requests.log || echo "channel never reached: good"
```

The phone part (mobile data, `$H/api/phone-test` open for 10 minutes) and a reboot test (does `$H` stay the same?)
need the owner; the lead routes them.

## 6. Undo

```bash
./tailscale --socket=$HOME/sp12/state/ts.sock funnel reset
./tailscale --socket=$HOME/sp12/state/ts.sock logout      # removes the node from the tailnet
pkill -f "$HOME/sp12/tailscaled"; pkill -f "uvicorn app:app --host 127.0.0.1 --port 7421"
rm -rf ~/sp12
pgrep -fl tailscale || echo "no tailscale left"
```

Report back: each step PASS/FAIL, exact errors, times, and the address shape.
