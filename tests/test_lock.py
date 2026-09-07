"""Одиночный инстанс + тихие сетевые ошибки."""
import os

from aiogram.exceptions import TelegramNetworkError

import main


def test_acquire_and_release(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert main.acquire_lock() is True
    assert (tmp_path / 'bot.lock').read_text() == str(os.getpid())
    # второй запуск в том же процессе видит живой pid — блок
    assert main.acquire_lock() is False
    main.release_lock()
    assert not (tmp_path / 'bot.lock').exists()


def test_stale_lock_overwritten(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'bot.lock').write_text('99999999')
    assert main.acquire_lock() is True
    main.release_lock()


def test_garbage_lock_overwritten(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'bot.lock').write_text('not-a-pid')
    assert main.acquire_lock() is True
    main.release_lock()


def test_quiet_errors():
    err = TelegramNetworkError.__new__(TelegramNetworkError)
    assert main._is_quiet_error(err) is True
    assert main._is_quiet_error(ValueError('x')) is False


def test_display_normalizes_double_escape():
    # новая запись (уже экранирована) и старая (сырая) — одинаково чисто
    assert main._display('a &lt; b', 60) == 'a &lt; b'
    assert main._display('a < b', 60) == 'a &lt; b'
