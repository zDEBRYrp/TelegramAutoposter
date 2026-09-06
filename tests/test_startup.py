"""Тест: мёртвая сессия не должна вешать старт бота навсегда."""
import asyncio

import user


class _HangingClient:
    is_connected = False

    async def start(self):
        await asyncio.sleep(3600)

    async def stop(self):
        return None


def test_start_client_times_out(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'session.session').write_bytes(b'fake')
    monkeypatch.setattr(user, 'make_client', lambda: _HangingClient())
    monkeypatch.setattr(user, 'client', None)
    # патчим таймаут: подменяем asyncio.wait_for коротким ожиданием
    orig_wait_for = asyncio.wait_for

    async def fast_wait_for(coro, timeout):
        return await orig_wait_for(coro, 0.1)

    monkeypatch.setattr(asyncio, 'wait_for', fast_wait_for)
    assert asyncio.run(user.start_client()) is False


def test_start_client_no_session_file(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(user, 'client', None)
    assert asyncio.run(user.start_client()) is False
