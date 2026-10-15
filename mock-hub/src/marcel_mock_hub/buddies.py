"""Buddies (B-49, ADR-013): one person, several buddies, and the buddy each phone shows.

Two buddies are there from the start: Marcel (the contract's `Agent` example, a rose giraffe) and
Pip (a tangerine fox), each with a main conversation and an Inbox. Any token the mock did not hand
out is the first phone's (the contract's `Device` example); a phone paired with a code from
`POST /pairing-codes` gets its own device and token.
"""

from __future__ import annotations

import copy
import hashlib
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import zoo

from . import contract
from .base import Refused, now

if TYPE_CHECKING:
    from .hub import Hub

PAIRING_MINUTES = 10
MARCEL_LOOK = {'animal': 'giraffe', 'color': 'rose', 'personality': 'professional'}
PIP_ID = 'agt_01JA3E2'  # the id the contract's `DeviceUpdate` and `NewPairingCode` examples use
PIP_CONVERSATION = 'cnv_main_pip'
PIP_HELLO = "Hi, I'm Pip. Ask me anything about the garden."
# The hub the contract's pairing links point at; a code's link names it as they do.
HUB_URL = 'https://marcel-bot.com/api'
USED = ''  # a pairing code that has paired a phone already


def avatar_etag(agent: dict[str, Any]) -> str | None:
    """Changes when the look changes; absent while the agent has no look."""
    look = agent.get('look')
    if look is None:
        return None
    return f'"lk-{look["animal"]}-{look["color"]}-{look["personality"]}"'


def look_for(name: str) -> dict[str, str]:
    """A look picked from the name: the same name always gets the same look (`POST /agents`)."""
    digest = hashlib.sha256(name.encode()).digest()
    animals, colors, personalities = list(zoo.SPECIES), list(zoo.COLORS), list(zoo.PERSONALITIES)
    return {
        'animal': animals[digest[0] % len(animals)],
        'color': colors[digest[1] % len(colors)],
        'personality': personalities[digest[2] % len(personalities)],
    }


def stamp(at: datetime) -> str:
    return at.strftime('%Y-%m-%dT%H:%M:%SZ')


class Buddies:
    def __init__(self, hub: Hub) -> None:
        self.hub = hub
        me = contract.example('Me')
        self.user_id: str = me['user_id']
        self.user_name: str = me['name']
        marcel = hub.agent_record
        marcel.update(look=dict(MARCEL_LOOK), animal=MARCEL_LOOK['animal'])
        self.records: dict[str, dict[str, Any]] = {marcel['id']: marcel}  # first one first
        first = contract.example('Device')
        first.pop('current', None)
        first.update(agent_id=marcel['id'], push='none')
        self.first_device: str = first['id']
        self.devices: dict[str, dict[str, Any]] = {first['id']: first}
        self.push_targets: dict[str, dict[str, Any]] = {}  # device id -> what it registered
        self.tokens: dict[str, str] = {}  # token -> device id, for phones the mock paired
        self.revoked: set[str] = set()  # tokens of signed-out phones: always a 401
        self.codes: dict[str, str] = {}  # pairing code -> the buddy it starts on, or USED
        pip = self._new_agent(PIP_ID, 'Pip', contract.example('NewAgent')['look'], PIP_CONVERSATION)
        self.records[pip['id']] = pip
        self.hub.add_main_conversation(pip, '2026-10-15T09:00:00Z')
        self.hub.messages.append(
            {
                'id': 'msg_pip_hello',
                'conversation_id': PIP_CONVERSATION,
                'role': 'agent',
                'agent_id': PIP_ID,
                'kind': 'text',
                'text': PIP_HELLO,
                'created_at': '2026-10-15T09:00:00Z',
            }
        )
        self.hub.create_project({'agent_id': PIP_ID, 'name': 'Inbox', 'slug': 'inbox'}, quiet=True)

    def _new_agent(
        self, agent_id: str, name: str, look: dict[str, str], conversation_id: str
    ) -> dict[str, Any]:
        marcel = self.hub.agent_record
        return {
            'id': agent_id,
            'name': name,
            'look': dict(look),
            'animal': look['animal'],
            'main_conversation_id': conversation_id,
            'state': {'kind': 'idle'},
            'model_brain': marcel['model_brain'],
            'model_worker': marcel['model_worker'],
        }

    # ---- buddies ---------------------------------------------------------------------------

    def agent(self, agent_id: str) -> dict[str, Any]:
        """One buddy as the app sees it, an archived one too. Unknown ids are a 404."""
        if agent_id == self.hub.agent_record['id']:
            return self.hub.agent()
        if agent_id not in self.records:
            raise Refused(404, 'not_found', 'There is no buddy with that id.')
        return copy.deepcopy(self.records[agent_id])

    def live(self) -> list[dict[str, Any]]:
        """Every live buddy, the first one first (`Me.agents`)."""
        return [self.agent(i) for i, r in self.records.items() if 'archived_at' not in r]

    def live_id(self, agent_id: str) -> str:
        """The id when it names a live buddy; else 404 `agent_not_found`."""
        record = self.records.get(agent_id)
        if record is None or 'archived_at' in record:
            raise Refused(404, 'agent_not_found', 'There is no live buddy with that id.')
        return agent_id

    def _announce(self, agent_id: str) -> dict[str, Any]:
        agent = self.agent(agent_id)
        data: dict[str, Any] = {'agent': agent}
        etag = avatar_etag(agent)
        if etag is not None:
            data['avatar_etag'] = etag
        self.hub.emit('agent.updated', data)
        return agent

    def create(self, body: dict[str, Any]) -> dict[str, Any]:
        """`POST /agents`: a new buddy with a main conversation and an Inbox. No phone switches."""
        agent_id = self.hub.next_id('agt')
        look = body.get('look') or look_for(body['name'])
        agent = self._new_agent(agent_id, body['name'], look, self.hub.next_id('cnv'))
        self.records[agent_id] = agent
        self.hub.add_main_conversation(agent, now())
        self.hub.create_project({'agent_id': agent_id, 'name': 'Inbox', 'slug': 'inbox'})
        return self._announce(agent_id)

    def update(self, agent_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        """`PATCH /agents/{id}`: a new name, a new look (`animal` follows it), onboarded once."""
        self.agent(agent_id)  # 404 for an unknown id
        record = self.records[agent_id]
        if 'name' in changes:
            record['name'] = changes['name']
        if 'look' in changes:
            record.update(look=changes['look'], animal=changes['look']['animal'])
        if 'palette' in changes and 'look' not in record:
            record['palette'] = changes['palette']
        if changes.get('onboarded') and 'onboarded_at' not in record:
            record['onboarded_at'] = now()
        return self._announce(agent_id)

    def archive(self, agent_id: str) -> dict[str, Any]:
        """`POST /agents/{id}/archive`. Its phones move to the first live buddy. Archiving an
        archived buddy changes nothing; the last live one is refused."""
        record = self.records.get(agent_id)
        if record is None:
            raise Refused(404, 'not_found', 'There is no buddy with that id.')
        if 'archived_at' in record:
            return self.agent(agent_id)
        if len(self.live()) == 1:
            raise Refused(409, 'last_buddy', 'This is your only buddy. Add another one first.')
        record['archived_at'] = now()
        agent = self._announce(agent_id)
        first = self.live()[0]['id']
        for device in self.devices.values():
            if device['agent_id'] == agent_id:
                device['agent_id'] = first
                self.hub.emit('device.updated', device)
        return agent

    # ---- phones ----------------------------------------------------------------------------

    def known(self, token: str) -> bool:
        """Any token works except one whose phone was signed out."""
        return token not in self.revoked

    def device_of(self, token: str | None) -> str:
        return self.tokens.get(token or '', self.first_device)

    def _shown(self, device: dict[str, Any], token: str | None) -> dict[str, Any]:
        shown = copy.deepcopy(device)
        shown['current'] = device['id'] == self.device_of(token)
        return shown

    def me(self, token: str | None) -> dict[str, Any]:
        me = contract.example('Me')
        me.update(user_id=self.user_id, device_id=self.device_of(token), agents=self.live())
        return me

    def list_devices(self, token: str | None) -> list[dict[str, Any]]:
        newest = sorted(self.devices.values(), key=lambda d: d['created_at'], reverse=True)
        return [self._shown(d, token) for d in newest]

    def _device(self, device_id: str) -> dict[str, Any]:
        device = self.devices.get(device_id)
        if device is None:
            raise Refused(404, 'device_not_found', 'There is no phone with that id.')
        return device

    def show(self, token: str | None, device_id: str, agent_id: str) -> dict[str, Any]:
        """`PATCH /devices/{id}`: the buddy that phone shows from now on; `device.updated`."""
        device = self._device(device_id)
        device['agent_id'] = self.live_id(agent_id)
        self.hub.emit('device.updated', device)
        return self._shown(device, token)

    def set_push(self, token: str | None, device_id: str, target: dict[str, Any]) -> dict[str, Any]:
        """`PUT /devices/{id}/push`: stored, and its kind shows in `Device.push`."""
        device = self._device(device_id)
        self.push_targets[device_id] = copy.deepcopy(target)
        device['push'] = target['kind']
        return self._shown(device, token)

    def revoke(self, device_id: str) -> None:
        """`DELETE /devices/{id}`: a phone the mock paired stops working at once. The first phone
        stays: any token the mock did not hand out is still its."""
        mine = [t for t, d in self.tokens.items() if d == device_id]
        for token in mine:
            del self.tokens[token]
            self.revoked.add(token)
        if mine:
            self.devices.pop(device_id, None)
            self.push_targets.pop(device_id, None)

    # ---- pairing ---------------------------------------------------------------------------

    def issue_code(self, agent_id: str | None) -> dict[str, Any]:
        """`POST /pairing-codes`: a code that pairs one more phone, once, for 10 minutes."""
        if agent_id is not None:
            self.live_id(agent_id)
        code = f'MOCK{len(self.codes) + 1:02d}'
        link = f'marcel://pair?hub={quote(HUB_URL, safe="")}&code={code}'
        if agent_id is not None:
            link += f'&buddy={agent_id}'
        self.codes[code] = agent_id or self.live()[0]['id']
        expires = datetime.now(UTC) + timedelta(minutes=PAIRING_MINUTES)
        return {'code': code, 'link': link, 'expires_at': stamp(expires)}

    def pair(self, code: str, device_name: str) -> dict[str, Any]:
        """`POST /pair`. A code from `issue_code` pairs a new phone, once, on its buddy (the first
        live one if that buddy was archived since). Any other code answers the contract's example,
        the first phone."""
        pairing = contract.example('PairResponse')
        if code not in self.codes:
            return pairing
        agent_id = self.codes[code]
        if agent_id == USED:
            raise Refused(400, 'bad_request', 'That code was used already. Make a fresh one.')
        self.codes[code] = USED
        if 'archived_at' in self.records[agent_id]:
            agent_id = self.live()[0]['id']
        device_id = self.hub.next_id('dev')
        self.devices[device_id] = {
            'id': device_id,
            'name': device_name,
            'created_at': now(),
            'agent_id': agent_id,
            'push': 'none',
        }
        token = f'mrc_mock_{self.hub.next_id("tok")}'
        self.tokens[token] = device_id
        pairing.update(device_id=device_id, user_id=self.user_id, token=token)
        return pairing
