"""Operator surface (FEAT-260707-6130cd): users, telegram-setup, doctor, CLI."""

from __future__ import annotations

import pytest

from marcel_core.ops.telegram import TelegramSetupError, setup_webhook
from marcel_core.ops.users import UserOpError, add_user, remove_user
from marcel_core.storage import _root


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    from marcel_core.config import settings

    monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
    monkeypatch.setattr(settings, 'marcel_data_dir', str(tmp_path))
    return tmp_path


class TestAddUser:
    def test_creates_profile_role_and_memory(self, data_root):
        msg = add_user('alice', 'admin')
        assert 'created' in msg
        udir = data_root / 'users' / 'alice'
        assert (udir / 'memory').is_dir()
        profile = (udir / 'profile.md').read_text()
        assert 'role: admin' in profile

        from marcel_core.storage.users import get_user_role

        assert get_user_role('alice') == 'admin'

    def test_idempotent_preserves_body(self, data_root):
        add_user('bob', 'user')
        (data_root / 'users' / 'bob' / 'profile.md').write_text('---\nrole: user\n---\n\nBob likes hiking.\n')
        msg = add_user('bob', 'user')
        assert 'updated' in msg
        assert 'Bob likes hiking.' in (data_root / 'users' / 'bob' / 'profile.md').read_text()

    def test_role_upgrade_on_existing_user(self, data_root):
        add_user('carol', 'user')
        add_user('carol', 'admin')
        from marcel_core.storage.users import get_user_role

        assert get_user_role('carol') == 'admin'

    def test_invalid_slug_and_role_refused(self, data_root):
        with pytest.raises(UserOpError, match='Invalid user slug'):
            add_user('../etc', 'user')
        with pytest.raises(UserOpError, match='Invalid role'):
            add_user('dave', 'superuser')


class TestRemoveUser:
    def test_archives_never_deletes(self, data_root):
        add_user('erin', 'user')
        (data_root / 'users' / 'erin' / 'note.txt').write_text('keep me')
        msg = remove_user('erin')

        assert 'archived' in msg
        assert not (data_root / 'users' / 'erin').exists()  # gone from live
        archives = list((data_root / 'archive' / 'users').iterdir())
        assert len(archives) == 1 and archives[0].name.startswith('erin-')
        assert (archives[0] / 'note.txt').read_text() == 'keep me'  # data recoverable

    def test_unknown_user_refused(self, data_root):
        with pytest.raises(UserOpError, match="No user 'ghost'"):
            remove_user('ghost')


class TestTelegramSetup:
    @pytest.mark.asyncio
    async def test_registers_and_verifies(self, monkeypatch, respx_mock):
        import httpx

        from marcel_core.config import settings

        monkeypatch.setattr(settings, 'telegram_bot_token', '123:ABC')
        monkeypatch.setattr(settings, 'telegram_webhook_secret', 's3cr3t')
        respx_mock.post('https://api.telegram.org/bot123:ABC/setWebhook').mock(
            return_value=httpx.Response(200, json={'ok': True, 'result': True})
        )
        respx_mock.post('https://api.telegram.org/bot123:ABC/getWebhookInfo').mock(
            return_value=httpx.Response(
                200,
                json={
                    'ok': True,
                    'result': {'url': 'https://m.example.test/telegram/webhook', 'pending_update_count': 0},
                },
            )
        )
        msg = await setup_webhook('https://m.example.test')
        assert 'registered and verified' in msg
        assert 'with a secret token' in msg

    @pytest.mark.asyncio
    async def test_missing_token_fails_readably(self, monkeypatch):
        from marcel_core.config import settings

        monkeypatch.setattr(settings, 'telegram_bot_token', '')
        with pytest.raises(TelegramSetupError, match='TELEGRAM_BOT_TOKEN is not set'):
            await setup_webhook('https://m.example.test')

    @pytest.mark.asyncio
    async def test_non_https_refused(self, monkeypatch):
        from marcel_core.config import settings

        monkeypatch.setattr(settings, 'telegram_bot_token', '123:ABC')
        with pytest.raises(TelegramSetupError, match='must be https'):
            await setup_webhook('http://m.example.test')

    @pytest.mark.asyncio
    async def test_verification_mismatch_fails(self, monkeypatch, respx_mock):
        import httpx

        from marcel_core.config import settings

        monkeypatch.setattr(settings, 'telegram_bot_token', '123:ABC')
        monkeypatch.setattr(settings, 'telegram_webhook_secret', '')
        respx_mock.post('https://api.telegram.org/bot123:ABC/setWebhook').mock(
            return_value=httpx.Response(200, json={'ok': True, 'result': True})
        )
        respx_mock.post('https://api.telegram.org/bot123:ABC/getWebhookInfo').mock(
            return_value=httpx.Response(
                200, json={'ok': True, 'result': {'url': 'https://wrong.test/telegram/webhook'}}
            )
        )
        with pytest.raises(TelegramSetupError, match='verification failed'):
            await setup_webhook('https://m.example.test')

    @pytest.mark.asyncio
    async def test_bad_token_surfaces_bot_api_reason(self, monkeypatch, respx_mock):
        import httpx

        from marcel_core.config import settings

        monkeypatch.setattr(settings, 'telegram_bot_token', 'bad')
        monkeypatch.setattr(settings, 'telegram_webhook_secret', '')
        respx_mock.post('https://api.telegram.org/botbad/setWebhook').mock(
            return_value=httpx.Response(401, json={'ok': False, 'description': 'Unauthorized'})
        )
        with pytest.raises(TelegramSetupError, match='401.*Unauthorized'):
            await setup_webhook('https://m.example.test')


class TestDoctor:
    @pytest.mark.asyncio
    async def test_all_green(self, data_root, monkeypatch, respx_mock):
        import httpx

        from marcel_core.config import settings
        from marcel_core.ops.doctor import run_doctor

        add_user('shaun', 'admin')
        zoo = data_root / 'zoo'
        (zoo / 'skills').mkdir(parents=True)
        monkeypatch.setattr(settings, 'marcel_port', 8000)
        monkeypatch.setattr(settings, 'marcel_zoo_dir', str(zoo))
        monkeypatch.setattr(settings, 'telegram_bot_token', '')  # telegram optional → soft-pass
        respx_mock.get('http://localhost:8000/health').mock(return_value=httpx.Response(200, json={'status': 'ok'}))

        code, report = await run_doctor()
        assert code == 0
        assert 'Marcel is healthy' in report
        assert '✅ server' in report and '✅ zoo' in report and '✅ users' in report

    @pytest.mark.asyncio
    async def test_server_down_fails_hard(self, data_root, monkeypatch, respx_mock):
        import httpx

        from marcel_core.config import settings
        from marcel_core.ops.doctor import run_doctor

        monkeypatch.setattr(settings, 'marcel_port', 8000)
        monkeypatch.setattr(settings, 'telegram_bot_token', '')
        monkeypatch.setattr(settings, 'marcel_zoo_dir', str(data_root / 'zoo'))
        respx_mock.get('http://localhost:8000/health').mock(side_effect=httpx.ConnectError('refused'))

        code, report = await run_doctor()
        assert code == 1
        assert '❌ server' in report and 'need attention' in report

    @pytest.mark.asyncio
    async def test_webhook_unregistered_flagged(self, data_root, monkeypatch, respx_mock):
        import httpx

        from marcel_core.config import settings
        from marcel_core.ops.doctor import run_doctor

        add_user('shaun', 'admin')
        monkeypatch.setattr(settings, 'marcel_port', 8000)
        monkeypatch.setattr(settings, 'marcel_zoo_dir', str(data_root / 'zoo'))
        monkeypatch.setattr(settings, 'telegram_bot_token', '123:ABC')
        respx_mock.get('http://localhost:8000/health').mock(return_value=httpx.Response(200, json={}))
        respx_mock.post('https://api.telegram.org/bot123:ABC/getWebhookInfo').mock(
            return_value=httpx.Response(200, json={'ok': True, 'result': {'url': ''}})
        )
        code, report = await run_doctor()
        assert code == 1
        assert 'no webhook registered' in report


class TestCli:
    def test_add_and_remove_round_trip(self, data_root, capsys):
        from marcel_core.ops.cli import main

        assert main(['add-user', '--user', 'frank', '--role', 'admin']) == 0
        assert 'created' in capsys.readouterr().out
        assert main(['remove-user', '--user', 'frank']) == 0
        assert 'archived' in capsys.readouterr().out

    def test_errors_are_readable_nonzero(self, data_root, capsys):
        from marcel_core.ops.cli import main

        assert main(['remove-user', '--user', 'nobody']) == 1
        assert 'error:' in capsys.readouterr().err


class TestCliTelegramAndDoctor:
    def test_telegram_setup_via_cli(self, monkeypatch, respx_mock):
        import httpx

        from marcel_core.config import settings

        monkeypatch.setattr(settings, 'telegram_bot_token', '9:Z')
        monkeypatch.setattr(settings, 'telegram_webhook_secret', '')
        respx_mock.post('https://api.telegram.org/bot9:Z/setWebhook').mock(
            return_value=httpx.Response(200, json={'ok': True, 'result': True})
        )
        respx_mock.post('https://api.telegram.org/bot9:Z/getWebhookInfo').mock(
            return_value=httpx.Response(200, json={'ok': True, 'result': {'url': 'https://x.test/telegram/webhook'}})
        )
        from marcel_core.ops.cli import main as _m

        assert _m(['telegram-setup', '--url', 'https://x.test']) == 0

    def test_doctor_via_cli_returns_exit_code(self, data_root, monkeypatch, respx_mock, capsys):
        import httpx

        from marcel_core.config import settings
        from marcel_core.ops.cli import main

        monkeypatch.setattr(settings, 'marcel_port', 8000)
        monkeypatch.setattr(settings, 'telegram_bot_token', '')
        monkeypatch.setattr(settings, 'marcel_zoo_dir', str(data_root / 'zoo'))
        respx_mock.get('http://localhost:8000/health').mock(side_effect=httpx.ConnectError('x'))
        assert main(['doctor']) == 1
        assert 'need attention' in capsys.readouterr().out

    def test_telegram_setup_error_via_cli_is_nonzero(self, monkeypatch, capsys):
        from marcel_core.config import settings
        from marcel_core.ops.cli import main

        monkeypatch.setattr(settings, 'telegram_bot_token', '')
        assert main(['telegram-setup', '--url', 'https://x.test']) == 1
        assert 'error:' in capsys.readouterr().err


class TestDoctorEdgeCases:
    @pytest.mark.asyncio
    async def test_server_non_200_flagged(self, data_root, monkeypatch, respx_mock):
        import httpx

        from marcel_core.config import settings
        from marcel_core.ops.doctor import run_doctor

        monkeypatch.setattr(settings, 'marcel_port', 8000)
        monkeypatch.setattr(settings, 'telegram_bot_token', '')
        monkeypatch.setattr(settings, 'marcel_zoo_dir', str(data_root / 'zoo'))
        respx_mock.get('http://localhost:8000/health').mock(return_value=httpx.Response(503))
        code, report = await run_doctor()
        assert code == 1 and 'HTTP 503' in report

    @pytest.mark.asyncio
    async def test_webhook_last_error_flagged(self, data_root, monkeypatch, respx_mock):
        import httpx

        from marcel_core.config import settings
        from marcel_core.ops.doctor import run_doctor

        monkeypatch.setattr(settings, 'marcel_port', 8000)
        monkeypatch.setattr(settings, 'marcel_zoo_dir', str(data_root / 'zoo'))
        monkeypatch.setattr(settings, 'telegram_bot_token', '1:A')
        respx_mock.get('http://localhost:8000/health').mock(return_value=httpx.Response(200))
        respx_mock.post('https://api.telegram.org/bot1:A/getWebhookInfo').mock(
            return_value=httpx.Response(
                200,
                json={
                    'ok': True,
                    'result': {'url': 'https://x.test/telegram/webhook', 'last_error_message': 'timeout'},
                },
            )
        )
        code, report = await run_doctor()
        assert code == 1 and 'last error: timeout' in report

    @pytest.mark.asyncio
    async def test_webhook_getinfo_http_error(self, data_root, monkeypatch, respx_mock):
        import httpx

        from marcel_core.config import settings
        from marcel_core.ops.doctor import run_doctor

        monkeypatch.setattr(settings, 'marcel_port', 8000)
        monkeypatch.setattr(settings, 'marcel_zoo_dir', str(data_root / 'zoo'))
        monkeypatch.setattr(settings, 'telegram_bot_token', '1:A')
        respx_mock.get('http://localhost:8000/health').mock(return_value=httpx.Response(200))
        respx_mock.post('https://api.telegram.org/bot1:A/getWebhookInfo').mock(side_effect=httpx.ConnectError('x'))
        code, report = await run_doctor()
        assert code == 1 and 'getWebhookInfo failed' in report

    def test_zoo_unset_and_missing(self, data_root, monkeypatch):
        from marcel_core.config import settings
        from marcel_core.ops.doctor import _check_zoo

        monkeypatch.setattr(settings, 'marcel_zoo_dir', None)
        assert _check_zoo().ok is False and _check_zoo().hard is False  # unset = soft
        monkeypatch.setattr(settings, 'marcel_zoo_dir', str(data_root / 'nope'))
        c = _check_zoo()
        assert c.ok is False and c.hard is True  # points at nothing = hard

    def test_users_missing_profile_flagged(self, data_root, monkeypatch):
        from marcel_core.ops.doctor import _check_users

        (data_root / 'users' / 'ghost').mkdir(parents=True)  # dir but no profile.md
        c = _check_users()
        assert c.ok is False and 'missing profile.md: ghost' in c.detail


class TestTelegramTransportError:
    @pytest.mark.asyncio
    async def test_network_error_surfaces(self, monkeypatch, respx_mock):
        import httpx

        from marcel_core.config import settings
        from marcel_core.ops.telegram import TelegramSetupError, setup_webhook

        monkeypatch.setattr(settings, 'telegram_bot_token', '1:A')
        monkeypatch.setattr(settings, 'telegram_webhook_secret', '')
        respx_mock.post('https://api.telegram.org/bot1:A/setWebhook').mock(side_effect=httpx.ConnectError('down'))
        with pytest.raises(TelegramSetupError, match='Could not reach the Bot API'):
            await setup_webhook('https://x.test')


class TestRemoveUserUnlinks:
    def test_unlink_channels_is_best_effort(self, data_root, monkeypatch):
        add_user('gail', 'user')
        # A plugin whose unlink_user raises must not break offboarding.
        monkeypatch.setattr('marcel_core.plugin.channels.discover', lambda: None)
        monkeypatch.setattr('marcel_core.plugin.channels.list_channels', lambda: ['telegram'])

        class _P:
            def unlink_user(self, slug):
                raise RuntimeError('boom')

        monkeypatch.setattr('marcel_core.plugin.channels.get_channel', lambda n: _P())
        msg = remove_user('gail')
        assert 'archived' in msg
