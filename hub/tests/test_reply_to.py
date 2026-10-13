"""Replies remember what they answer (S-18.1, B-41): the brain's `in_reply_to`, and the message that
started a task on everything posted for it. Every message the phone gets is checked against the
contract's `Message`."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from test_channel_fakes import MESSAGE, ChannelHub, channel_hub, permission

from marcel_hub.models import TaskState


@pytest.fixture
def hub(tmp_path: Path) -> Iterator[ChannelHub]:
    with channel_hub(tmp_path) as hub:
        yield hub


def ask(hub: ChannelHub, text: str, conversation_id: str | None = None) -> str:
    """The owner's message from the phone; returns its id."""
    res = hub.client.post(
        f'/api/conversations/{conversation_id or hub.main_id}/messages', json={'text': text}
    )
    assert res.status_code == 201, res.text
    return res.json()['id']


def on_the_phone(hub: ChannelHub, conversation_id: str | None = None) -> list[dict[str, Any]]:
    """The conversation as the phone reads it, oldest first."""
    res = hub.client.get(f'/api/conversations/{conversation_id or hub.main_id}/messages')
    assert res.status_code == 200, res.text
    for message in res.json()['items']:
        MESSAGE.validate(message)
    return list(reversed(res.json()['items']))  # the phone gets the newest first


def brain_reply(hub: ChannelHub, **fields: Any) -> dict[str, Any]:
    sid = hub.brain()
    with hub.connect() as ch:
        ch.hello(sid)
        ack = ch.call('reply', text='Checking now.', **fields)
    [reply] = [m for m in on_the_phone(hub) if m['role'] == 'agent']
    return {'ack': ack, 'reply': reply}


# The brain's reply ----------------------------------------------------------------------------


def test_a_reply_that_names_the_owners_message_quotes_it(hub: ChannelHub) -> None:
    asked = ask(hub, 'How full is the disk?')
    got = brain_reply(hub, in_reply_to=asked)
    assert got['reply']['reply_to_message_id'] == asked
    [event] = [e for e in hub.events('message.created') if e['data']['role'] == 'agent']
    assert event['data']['reply_to_message_id'] == asked


def test_a_reply_naming_a_message_of_another_conversation_is_stored_without_it(
    hub: ChannelHub, caplog: pytest.LogCaptureFixture
) -> None:
    side_id, _ = hub.side()
    elsewhere = ask(hub, 'A side question', side_id)
    got = brain_reply(hub, in_reply_to=elsewhere)
    assert 'error' not in got['ack']
    assert got['reply']['text'] == 'Checking now.'
    assert 'reply_to_message_id' not in got['reply']
    assert f'in_reply_to {elsewhere}' in caplog.text


def test_a_reply_naming_an_agent_message_is_stored_without_it(hub: ChannelHub) -> None:
    sid = hub.brain()
    with hub.connect() as ch:
        ch.hello(sid)
        ch.call('reply', text='First.')
        [first] = on_the_phone(hub)
        ch.call('reply', text='Second.', in_reply_to=first['id'])
    second = on_the_phone(hub)[-1]
    assert second['text'] == 'Second.' and 'reply_to_message_id' not in second


def test_a_reply_naming_an_unknown_message_is_stored_without_it(
    hub: ChannelHub, caplog: pytest.LogCaptureFixture
) -> None:
    got = brain_reply(hub, in_reply_to='msg_nope')
    assert 'error' not in got['ack']
    assert 'reply_to_message_id' not in got['reply']
    assert 'msg_nope' in caplog.text


def test_a_reply_without_in_reply_to_quotes_nothing(hub: ChannelHub) -> None:
    ask(hub, 'How full is the disk?')
    got = brain_reply(hub)
    assert 'reply_to_message_id' not in got['reply']


# What a task posts --------------------------------------------------------------------------


def test_a_tasks_milestones_card_and_items_quote_the_message_that_started_it(
    hub: ChannelHub,
) -> None:
    asked = ask(hub, 'Check the disk usage of the Docker volumes')
    task_id, sid = hub.task(TaskState.starting, parent_message_id=asked)
    later = ask(hub, 'And remind me about the dentist.')
    (hub.workers / 'volumes.txt').write_text('plex_media_cache 38.2 GB\n')
    with hub.connect() as ch:
        ch.hello(sid)
        ch.call('report', kind='started', text='Looking at the volumes.')
        ch.send(**permission())
        ch.until_pong()
        [card] = hub.approvals(state='open')
        assert hub.answer(card['id'], 'approve').status_code == 200
        ch.call(
            'report',
            kind='done',
            summary='plex_media_cache is 38.2 GB.',
            artifacts=[
                {
                    'kind': 'file',
                    'title': 'Docker volumes',
                    'path': str(hub.workers / 'volumes.txt'),
                }
            ],
        )
    posted = [m for m in on_the_phone(hub) if m.get('task_id') == task_id]
    kinds = [m['milestone']['event'] if m['kind'] == 'milestone' else m['kind'] for m in posted]
    assert kinds == ['started', 'approval', 'artifact', 'done']
    assert {m['reply_to_message_id'] for m in posted} == {asked}
    # The owner said something since, so the "done" answers an older message: the app quotes it.
    ids = [m['id'] for m in on_the_phone(hub)]
    assert ids.index(asked) < ids.index(later) < ids.index(posted[-1]['id'])


def test_a_task_started_without_a_message_quotes_nothing(hub: ChannelHub) -> None:
    task_id, sid = hub.task(TaskState.starting)
    with hub.connect() as ch:
        ch.hello(sid)
        ch.call('report', kind='started', text='Looking.')
    [started] = [m for m in on_the_phone(hub) if m.get('task_id') == task_id]
    assert 'reply_to_message_id' not in started


def test_a_task_started_from_another_conversation_quotes_nothing_here(hub: ChannelHub) -> None:
    side_id, _ = hub.side()
    asked = ask(hub, 'Could you check the disk?', side_id)
    task_id, sid = hub.task(TaskState.starting, parent_message_id=asked)
    with hub.connect() as ch:
        ch.hello(sid)
        ch.call('report', kind='started', text='Looking.')
    [started] = [m for m in on_the_phone(hub) if m.get('task_id') == task_id]
    assert 'reply_to_message_id' not in started
