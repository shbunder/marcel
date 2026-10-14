"""People (B-37): the owner, Emma from the contract's `User` example, and whose a token is.

Any token the mock did not hand out is the owner's, as before. A phone paired with a code from
`POST /users/{user_id}/pairing-codes` gets a token that belongs to that person.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from . import contract
from .base import Refused, now

if TYPE_CHECKING:
    from .hub import Hub

PAIRING_MINUTES = 10


def avatar_etag(agent: dict[str, Any]) -> str | None:
    """Changes when the look changes; absent while the agent has no look."""
    look = agent.get('look')
    if look is None:
        return None
    return f'"lk-{look["animal"]}-{look["color"]}-{look["personality"]}"'


class People:
    def __init__(self, hub: Hub, owner_agent: dict[str, Any], owner_since: str) -> None:
        self.hub = hub
        me = contract.example('Me')
        self.owner_id: str = me['user_id']
        self.owner_name: str = me['name']
        self.users: dict[str, dict[str, Any]] = {
            self.owner_id: {
                'id': self.owner_id,
                'name': self.owner_name,
                'role': 'owner',
                'created_at': owner_since,
                'agent': {'id': owner_agent['id'], 'name': owner_agent['name']},
                'device_count': 1,
                'quests_this_week': 0,
            }
        }
        self.agents: dict[str, dict[str, Any]] = {}  # members' agents; the owner's is the hub's
        self.tokens: dict[str, tuple[str, str]] = {}  # token -> (user id, device id)
        self.revoked: set[str] = set()  # tokens of people who were removed: always a 401
        self.codes: dict[str, str | None] = {}  # pairing code -> user id; None once used
        emma = contract.example('User')
        self.users[emma['id']] = emma
        # Emma's helper as the `agent.updated` example shows it, but before "Meet your helper":
        # Emma was just added and has not opened the app, so her first sign-in shows onboarding.
        example = contract.load_events()['$defs']['AgentUpdated']['examples'][0]
        pip = copy.deepcopy(example['data']['agent'])
        pip.pop('onboarded_at', None)
        self._add_agent(pip, emma['created_at'])

    def _add_agent(self, agent: dict[str, Any], since: str) -> None:
        self.agents[agent['id']] = agent
        self.hub.conversations[agent['main_conversation_id']] = {
            'id': agent['main_conversation_id'],
            'agent_id': agent['id'],
            'kind': 'main',
            'title': agent['name'],
            'created_at': since,
        }

    # ---- who is calling --------------------------------------------------------------------

    def caller(self, token: str) -> dict[str, Any] | None:
        """The person a token belongs to: the owner for any token the mock did not hand out,
        None for the token of someone who was removed."""
        if token in self.revoked:
            return None
        if token not in self.tokens:
            return self.users[self.owner_id]
        return self.users[self.tokens[token][0]]

    def owner_only(
        self, message: str = 'Only {owner} can manage people. Ask {owner} to do it.'
    ) -> Refused:
        return Refused(403, 'owner_only', message.format(owner=self.owner_name))

    # ---- agents ----------------------------------------------------------------------------

    def _record(self, agent_id: str) -> dict[str, Any]:
        if agent_id == self.hub.agent_record['id']:
            return self.hub.agent_record
        return self.agents[agent_id]

    def agent(self, agent_id: str) -> dict[str, Any]:
        if agent_id == self.hub.agent_record['id']:
            return self.hub.agent()
        return copy.deepcopy(self.agents[agent_id])

    def update_agent(self, agent_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        """`PATCH /agents/{id}`: a new name, a new look (`animal` follows it), onboarded once.
        Sends `agent.updated`, with an `avatar_etag` when the agent has a look."""
        record = self._record(agent_id)
        if 'name' in changes:
            record['name'] = changes['name']
        if 'look' in changes:
            record.update(look=changes['look'], animal=changes['look']['animal'])
        if 'palette' in changes and 'look' not in record:
            record['palette'] = changes['palette']
        if changes.get('onboarded') and 'onboarded_at' not in record:
            record['onboarded_at'] = now()
        for user in self.users.values():
            if user['agent']['id'] == agent_id:
                user['agent'] = {'id': agent_id, 'name': record['name']}
                if 'look' in record:
                    user['agent']['look'] = record['look']
        agent = self.agent(agent_id)
        data: dict[str, Any] = {'agent': agent}
        etag = avatar_etag(agent)
        if etag is not None:
            data['avatar_etag'] = etag
        self.hub.emit('agent.updated', data)
        return agent

    def me(self, user: dict[str, Any], token: str) -> dict[str, Any]:
        me = contract.example('Me')
        me.update(
            user_id=user['id'],
            name=user['name'],
            role=user['role'],
            agents=[self.agent(user['agent']['id'])],
        )
        if token in self.tokens:
            me['device_id'] = self.tokens[token][1]
        return me

    # ---- the owner's People screen ---------------------------------------------------------

    def list_users(self) -> list[dict[str, Any]]:
        """The owner first, then members by name."""
        owner = copy.deepcopy(self.users[self.owner_id])
        week_ago = (datetime.now(UTC) - timedelta(days=7)).strftime('%Y-%m-%dT%H:%M:%SZ')
        owner['quests_this_week'] = sum(
            t['agent_id'] == owner['agent']['id'] and t['created_at'] >= week_ago
            for t in self.hub.tasks.values()
        )
        members = sorted(
            (u for u in self.users.values() if u['role'] == 'member'), key=lambda u: u['name']
        )
        return [owner, *copy.deepcopy(members)]

    def add_user(self, body: dict[str, Any]) -> dict[str, Any]:
        """A member with their own helper and main conversation. Without a look, the helper
        gets the `User` example's."""
        stamp = now()
        look = body['agent'].get('look') or contract.example('User')['agent']['look']
        owner_agent = self.hub.agent_record
        agent = {
            'id': self.hub.next_id('agt'),
            'name': body['agent']['name'],
            'animal': look['animal'],
            'look': look,
            'main_conversation_id': self.hub.next_id('cnv'),
            'state': {'kind': 'idle'},
            'model_brain': owner_agent['model_brain'],
            'model_worker': owner_agent['model_worker'],
        }
        self._add_agent(agent, stamp)
        user = {
            'id': self.hub.next_id('usr'),
            'name': body['name'],
            'role': 'member',
            'created_at': stamp,
            'agent': {'id': agent['id'], 'name': agent['name'], 'look': look},
            'device_count': 0,
            'quests_this_week': 0,
        }
        self.users[user['id']] = user
        return copy.deepcopy(user)

    def revoke_device(self, caller_token: str | None, device_id: str) -> bool:
        """`DELETE /devices/{id}` of one of the caller's own phones ("Sign out of this phone"): its
        token stops working at once. False when the caller has no phone with that id."""
        person = self.caller(caller_token) if caller_token else None
        if person is None:
            return False
        mine = [
            t
            for t, (owner, device) in self.tokens.items()
            if owner == person['id'] and device == device_id
        ]
        for token in mine:
            del self.tokens[token]
            self.revoked.add(token)
        return bool(mine)

    def remove_user(self, user_id: str) -> None:
        """Their phones are signed out at once and their helper goes."""
        user = self._user(user_id)
        if user['role'] == 'owner':
            raise Refused(409, 'cannot_remove_owner', 'The owner cannot be removed.')
        del self.users[user_id]
        agent = self.agents.pop(user['agent']['id'], None)
        if agent is not None:
            self.hub.conversations.pop(agent['main_conversation_id'], None)
        for token in [t for t, (owner, _) in self.tokens.items() if owner == user_id]:
            del self.tokens[token]
            self.revoked.add(token)

    def issue_code(self, user_id: str) -> dict[str, Any]:
        """A one-time code for this person's phone, in the shape of `IssuedPairingCode`."""
        self._user(user_id)
        issued = contract.example('IssuedPairingCode')
        code = f'MOCK{len(self.codes) + 1:02d}'
        issued['link'] = issued['link'].replace(issued['code'], code)
        issued['code'] = code
        expires = datetime.now(UTC) + timedelta(minutes=PAIRING_MINUTES)
        issued['expires_at'] = expires.strftime('%Y-%m-%dT%H:%M:%SZ')
        self.codes[code] = user_id
        return issued

    def pair(self, code: str) -> dict[str, Any]:
        """`POST /pair`. A code from `issue_code` pairs the phone to its person, once. Any other
        code pairs it to the owner, as before."""
        pairing = contract.example('PairResponse')
        if code not in self.codes:
            return pairing
        user_id = self.codes[code]
        if user_id is None:
            raise Refused(
                400,
                'bad_request',
                f'That code was used already. Ask {self.owner_name} for a fresh one.',
            )
        if user_id not in self.users:
            raise Refused(
                400, 'bad_request', f'That code was for someone {self.owner_name} has removed.'
            )
        self.codes[code] = None
        token = f'mrc_mock_member_{self.hub.next_id("tok")}'
        device = self.hub.next_id('dev')
        self.tokens[token] = (user_id, device)
        self.users[user_id]['device_count'] += 1
        pairing.update(device_id=device, user_id=user_id, token=token)
        return pairing

    def _user(self, user_id: str) -> dict[str, Any]:
        user = self.users.get(user_id)
        if user is None:
            raise Refused(404, 'user_not_found', 'There is no person with that id.')
        return user
