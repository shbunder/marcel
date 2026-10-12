// node shoot.js <url> <out.png> [width] [waitMs] [js-to-run-before]
// Drives headless Chrome over the DevTools protocol (no packages): full-page screenshot.
const { spawn } = require("child_process"), fs = require("fs");
const [url, out, width = "1200", wait = "4000", pre = "", selector = ""] = process.argv.slice(2);
const CH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const port = 9300 + Math.floor(Math.random() * 500);
const chrome = spawn(CH, ["--headless=new", `--remote-debugging-port=${port}`, "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
  "--hide-scrollbars", "--no-first-run", `--user-data-dir=/tmp/claude-501/chrome-${port}`, "about:blank"], { stdio: "ignore" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  let target;
  for (let i = 0; i < 50 && !target; i++) { try { target = (await (await fetch(`http://127.0.0.1:${port}/json`)).json()).find((t) => t.type === "page"); } catch { await sleep(200); } }
  const ws = new WebSocket(target.webSocketDebuggerUrl); let id = 0; const pending = {};
  ws.onmessage = (m) => { const d = JSON.parse(m.data); if (d.id && pending[d.id]) { pending[d.id](d); delete pending[d.id]; } };
  await new Promise((r) => (ws.onopen = r));
  const send = (method, params = {}) => new Promise((r) => { const i = ++id; pending[i] = r; ws.send(JSON.stringify({ id: i, method, params })); });
  await send("Page.enable"); await send("Runtime.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: +width, height: 1000, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url }); await sleep(+wait);
  if (pre) { await send("Runtime.evaluate", { expression: pre }); await sleep(1500); }
  if (selector) {     // capture one element only
    const q = await send("Runtime.evaluate", { expression: `(()=>{const e=document.querySelector(${JSON.stringify(selector)}); e.scrollIntoView(); const b=e.getBoundingClientRect(); return JSON.stringify({x:b.x,y:b.y+scrollY,w:b.width,h:b.height})})()`, returnByValue: true });
    const b = JSON.parse(q.result.result.value); await sleep(1500);
    const shot = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true, clip: { x: b.x, y: b.y, width: b.w, height: b.h, scale: 1 } });
    fs.writeFileSync(out, Buffer.from(shot.result.data, "base64")); console.log("element", selector, Math.round(b.w), "x", Math.round(b.h));
    ws.close(); chrome.kill(); return;
  }
  const r = await send("Runtime.evaluate", { expression: "JSON.stringify({h: document.documentElement.scrollHeight, errors: window.__errors || []})", returnByValue: true });
  const info = JSON.parse(r.result.result.value);
  await send("Emulation.setDeviceMetricsOverride", { width: +width, height: info.h, deviceScaleFactor: 1, mobile: false });
  await sleep(1500);
  const shot = await send("Page.captureScreenshot", { format: "png" });
  fs.writeFileSync(out, Buffer.from(shot.result.data, "base64"));
  console.log("height", info.h, "errors", info.errors.length ? info.errors : "none");
  ws.close(); chrome.kill();
})().catch((e) => { console.error(e); chrome.kill(); process.exit(1); });
