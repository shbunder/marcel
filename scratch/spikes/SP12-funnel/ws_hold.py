import asyncio, json, sys, time
import websockets

async def main(url: str, seconds: int) -> None:
    start = time.time()
    try:
        async with websockets.connect(url, ping_interval=None) as ws:
            print(f"open at {time.strftime('%H:%M:%S', time.gmtime())}Z", flush=True)
            last = 0
            while time.time() - start < seconds:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=60))
                if "tick" in msg and msg["tick"] - last >= 60:
                    last = msg["tick"]
                    print(f"still open: {msg['tick']} s", flush=True)
                if int(time.time() - start) % 20 == 0:
                    await ws.send("ping")
            print(f"PASS: held {round(time.time() - start)} s", flush=True)
    except Exception as e:
        print(f"CLOSED after {round(time.time() - start)} s: {e!r}", flush=True)

asyncio.run(main(sys.argv[1], int(sys.argv[2])))
