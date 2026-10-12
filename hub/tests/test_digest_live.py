"""One digest written end to end on the NUC (S-09.2 done-when). Marked `live`: never in a gate.

Needs the running hub and a real brain: `MARCEL_LIVE_URL` (e.g. http://localhost:7420),
`MARCEL_LIVE_TOKEN` (a paired device's token) and `MARCEL_LIVE_AGENT`. It moves the morning digest
to the next minute, waits for the hub's card of the published digest in the main conversation
(up to 35 minutes: the brain's 30, and a look), then puts the schedule's time back.

    make -C hub test-live
"""

import os
import time
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest

pytestmark = pytest.mark.live

WAIT_S = 35 * 60


def test_one_digest_is_written_end_to_end() -> None:
    url, token, agent = (os.environ.get(k) for k in
                         ('MARCEL_LIVE_URL', 'MARCEL_LIVE_TOKEN', 'MARCEL_LIVE_AGENT'))  # fmt: skip
    if not (url and token and agent):
        pytest.skip('Set MARCEL_LIVE_URL, MARCEL_LIVE_TOKEN and MARCEL_LIVE_AGENT on the NUC.')
    hub = httpx.Client(base_url=f'{url}/api', headers={'Authorization': f'Bearer {token}'})
    main = hub.get(f'/agents/{agent}').raise_for_status().json()['main_conversation_id']
    listed = hub.get('/schedules', params={'agent_id': agent}).raise_for_status().json()
    morning = next(s for s in listed if s['id'] == f'sch_digest_morning_{agent}')
    started = datetime.now(UTC)
    soon = (started + timedelta(minutes=2)).astimezone(ZoneInfo(morning['tz']))
    hub.patch(f'/schedules/{morning["id"]}', json={'cron': f'{soon.minute} {soon.hour} * * *'})
    try:
        deadline = time.monotonic() + WAIT_S
        while time.monotonic() < deadline:
            page = hub.get(f'/conversations/{main}/messages', params={'limit': 20}).json()
            cards = [
                m for m in page['items']
                if m['kind'] == 'artifact' and m['created_at'] >= started.isoformat()[:19]
            ]  # fmt: skip
            texts = [m.get('text', '') for m in page['items'] if m['role'] == 'system']
            assert not any('morning digest was not written' in t for t in texts), texts
            if cards:
                art = hub.get(f'/artifacts/{cards[0]["artifact_id"]}').raise_for_status().json()
                assert art['kind'] in ('doc', 'dashboard')
                return
            time.sleep(30)
        pytest.fail('No digest card arrived within 35 minutes.')
    finally:
        hub.patch(f'/schedules/{morning["id"]}', json={'cron': morning['cron']})
