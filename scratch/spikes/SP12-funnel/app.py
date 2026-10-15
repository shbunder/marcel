"""SP12 throwaway app: what the hub would publish through Tailscale Funnel, and what it must not.

Run: uv run --with fastapi --with uvicorn uvicorn app:app --host 127.0.0.1 --port 7420
Every request is logged to requests.log with the path the app saw, so we can tell whether /channel was reached.
"""

import asyncio
import json
import time
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

app = FastAPI()
LOG = Path(__file__).with_name('requests.log')
STARTED = time.time()


def log(**entry: object) -> None:
    with LOG.open('a') as f:
        f.write(json.dumps({'t': round(time.time(), 1), **entry}) + '\n')


@app.middleware('http')
async def record(request: Request, call_next):
    log(kind='http', method=request.method, path=request.url.path,
        via=request.headers.get('tailscale-funnel-request') or request.headers.get('x-forwarded-for'))
    return await call_next(request)


@app.get('/api/health')
async def health() -> dict[str, object]:
    return {'ok': True, 'up_seconds': round(time.time() - STARTED)}


@app.post('/api/session/report')
async def report(request: Request) -> dict[str, object]:
    body = await request.body()
    log(kind='report', bytes=len(body), body=body[:200].decode(errors='replace'))
    return {'ok': True, 'received': len(body)}


@app.websocket('/api/ws')
async def ws(socket: WebSocket) -> None:
    await socket.accept()
    opened = time.time()
    log(kind='ws-open')
    try:
        while True:
            await socket.send_json({'tick': round(time.time() - opened)})
            try:
                msg = await asyncio.wait_for(socket.receive_text(), timeout=15)
                await socket.send_json({'echo': msg})
            except asyncio.TimeoutError:
                pass
    except WebSocketDisconnect:
        log(kind='ws-close', open_seconds=round(time.time() - opened))


@app.api_route('/channel', methods=['GET', 'POST'])
@app.api_route('/channel/{rest:path}', methods=['GET', 'POST'])
async def channel(request: Request) -> dict[str, object]:
    log(kind='CHANNEL-REACHED', path=request.url.path)
    return {'private': 'this must never be reachable from the internet'}


PAGE = """<!doctype html><meta name=viewport content="width=device-width,initial-scale=1">
<title>SP12 phone test</title>
<body style="font:18px -apple-system,sans-serif;margin:24px">
<h2>SP12 phone test</h2>
<p id=h>Checking /api/health…</p>
<p id=w>Opening the live connection…</p>
<p id=c>Checking that /channel is closed…</p>
<p style="color:#666">Keep this page open on mobile data for 10 minutes, screen on.</p>
<script>
const s = t => t + ' at ' + new Date().toLocaleTimeString();
fetch('/api/health').then(r => r.json()).then(j => h.textContent = s('Health OK')).catch(e => h.textContent = 'Health FAILED: ' + e);
fetch('/channel').then(r => c.textContent = s('/channel answered ' + r.status + (r.ok ? ' (BAD: reachable)' : ' (good: closed)')))
  .catch(e => c.textContent = s('/channel unreachable (good)'));
const t0 = Date.now(); let drops = [], openedAt = 0, longest = 0;
function show() {
  const now = Math.round((Date.now() - t0) / 1000), cur = openedAt ? Math.round((Date.now() - openedAt) / 1000) : 0;
  w.innerHTML = 'Page open ' + now + ' s. Live connection ' + (openedAt ? 'open for ' + cur + ' s' : 'reconnecting…')
    + '<br>Longest: ' + Math.max(longest, cur) + ' s. Drops: ' + drops.length
    + (drops.length ? '<br>' + drops.slice(-5).join('<br>') : '');
}
function connect() {
  const ws = new WebSocket('wss://' + location.host + '/api/ws');
  ws.onopen = () => { openedAt = Date.now(); show(); };
  ws.onmessage = () => show();
  ws.onclose = e => {
    const held = openedAt ? Math.round((Date.now() - openedAt) / 1000) : 0;
    longest = Math.max(longest, held); openedAt = 0;
    drops.push(new Date().toLocaleTimeString() + ': closed after ' + held + ' s, code ' + e.code
      + (document.hidden ? ' (page was hidden)' : ''));
    show(); setTimeout(connect, 2000);
  };
  setInterval(() => ws.readyState === 1 && ws.send('ping'), 20000);
}
document.addEventListener('visibilitychange', () => drops.push(new Date().toLocaleTimeString() + ': page '
  + (document.hidden ? 'hidden' : 'visible again')));
connect(); setInterval(show, 1000);
</script>"""


@app.get('/api/phone-test', response_class=HTMLResponse)
async def phone_test() -> str:
    return PAGE
