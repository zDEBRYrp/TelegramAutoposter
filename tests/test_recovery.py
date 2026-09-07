"""Автовосстановление: стартовый отчёт, watchdog, ретрай поллинга."""
import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramNetworkError

import main
import user


def _net_err():
    err = TelegramNetworkError.__new__(TelegramNetworkError)
    err.message = 'boom'  # __new__ без __init__: str() требует message
    return err


def test_startup_report_variants():
    main.startup_info.update({'spam_was': True, 'resumed': 3, 'pyro_ok': True})
    out = main._build_startup_report()
    assert 'возобновлена (3 чатов)' in out and '✅' in out
    main.startup_info.update({'spam_was': True, 'resumed': 0, 'pyro_ok': False})
    out = main._build_startup_report()
    assert 'watchdog' in out and '❌' in out
    main.startup_info.update({'spam_was': False, 'resumed': 0, 'pyro_ok': False})
    out = main._build_startup_report()
    assert 'ВЫКЛ' in out


def test_watchdog_decision():
    done = SimpleNamespace(done=lambda: True)
    running = SimpleNamespace(done=lambda: False)
    assert main._watchdog_should_start(1, None) is True
    assert main._watchdog_should_start(1, done) is True
    assert main._watchdog_should_start(1, running) is False
    assert main._watchdog_should_start(0, None) is False


class StubDB:
    def __init__(self):
        self.spam_set = []

    def settings(self):
        return (1, '', '', 'x', 1, 5)

    def setSpam(self, v):
        self.spam_set.append(v)
        return True


def _patch_loop_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    db = StubDB()
    monkeypatch.setattr(main, 'db', db)
    bot = AsyncMock()
    monkeypatch.setattr(main, 'bot', bot)
    monkeypatch.setattr(user, 'ensure_connected', AsyncMock(return_value=False))
    main.spam_task = None
    return db, bot


def run(coro):
    return asyncio.run(coro)


def test_start_loop_no_session_disables(monkeypatch, tmp_path):
    db, bot = _patch_loop_env(monkeypatch, tmp_path)
    assert run(main.start_spam_loop()) == 0
    assert db.spam_set == [0]  # сессии нет — флаг гасим
    assert bot.send_message.await_count == 1


def test_start_loop_transient_keeps_flag(monkeypatch, tmp_path):
    db, bot = _patch_loop_env(monkeypatch, tmp_path)
    (tmp_path / 'session.session').write_bytes(b'fake')
    assert run(main.start_spam_loop()) == 0
    assert db.spam_set == []  # обрыв сети — флаг НЕ трогаем, watchdog поднимет
    text = bot.send_message.await_args[0][1]
    assert 'сама' in text or 'сеть' in text


def test_run_polling_retries_network(monkeypatch):
    err = _net_err()
    calls = []

    async def fake_polling(bot):
        calls.append(1)
        if len(calls) == 1:
            raise err

    monkeypatch.setattr(main.dp, 'start_polling', fake_polling)
    slept = []
    real_sleep = asyncio.sleep

    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr(main.asyncio, 'sleep', fake_sleep)
    run(main._run_polling())
    assert len(calls) == 2 and slept == [main.POLL_RETRY_SEC]


def test_run_polling_conflict_reraises(monkeypatch):
    from aiogram.exceptions import TelegramConflictError
    err = TelegramConflictError.__new__(TelegramConflictError)

    async def fake_polling(bot):
        raise err

    monkeypatch.setattr(main.dp, 'start_polling', fake_polling)
    try:
        run(main._run_polling())
        raise AssertionError('should reraise')
    except TelegramConflictError:
        pass


def test_on_startup_sends_report():
    main.startup_info.update({'spam_was': False, 'resumed': 0, 'pyro_ok': False})
    bot = AsyncMock()
    run(main.on_startup(bot))
    text = bot.send_message.await_args[0][1]
    assert 'Бот запущен' in text


def test_pyrogram_loggers_quiet():
    for name in ('pyrogram.connection', 'pyrogram.session', 'pyrogram.dispatcher'):
        assert logging.getLogger(name).getEffectiveLevel() >= logging.WARNING
