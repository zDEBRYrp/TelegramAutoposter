"""Одиночный инстанс (файловый lock) + тихие сетевые ошибки."""
import os
import pathlib
import subprocess
import sys
import time

from aiogram.exceptions import TelegramNetworkError

import main

ROOT = pathlib.Path(main.__file__).resolve().parent
CHILD_CODE = (
    'import sys; sys.path.insert(0, %r); ' % str(ROOT) +
    'import main, time; '
    'assert main.acquire_lock(), "child blocked?!"; '
    'time.sleep(60)'
)


def _locked_pid(path) -> str:
    with open(path, 'rb') as f:
        f.seek(1)
        return f.read().decode('utf-8', 'ignore').replace('\x00', '').strip()


def test_acquire_and_release(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    try:
        assert main.acquire_lock() is True
        assert _locked_pid(tmp_path / 'bot.lock') == str(os.getpid())
    finally:
        main.release_lock()
    assert (tmp_path / 'bot.lock').read_text().replace('\x00', '') == str(os.getpid())


def test_stale_content_never_blocks(monkeypatch, tmp_path):
    # мусор/старый pid в файле — не причина для блокировки:
    # решает живой хендл, а не содержимое
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'bot.lock').write_text('99999999')
    try:
        assert main.acquire_lock() is True
    finally:
        main.release_lock()


def test_second_process_blocked_until_first_dies(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    main.release_lock()
    child = subprocess.Popen(
        [sys.executable, '-c', CHILD_CODE],
        cwd=str(tmp_path),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        # ждём пока потомок возьмёт lock (пишет свой pid мимо лока)
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                if _locked_pid(tmp_path / 'bot.lock') == str(child.pid):
                    break
            except OSError:
                pass
            time.sleep(0.2)
        else:
            raise AssertionError('child did not acquire lock')
        assert main.acquire_lock() is False  # живой процесс держит
    finally:
        child.kill()
        child.wait(timeout=15)
    try:
        assert main.acquire_lock() is True  # умер — lock свободен
    finally:
        main.release_lock()


def test_quiet_errors():
    err = TelegramNetworkError.__new__(TelegramNetworkError)
    assert main._is_quiet_error(err) is True
    assert main._is_quiet_error(ValueError('x')) is False


def test_display_normalizes_double_escape():
    # новая запись (уже экранирована) и старая (сырая) — одинаково чисто
    assert main._display('a &lt; b', 60) == 'a &lt; b'
    assert main._display('a < b', 60) == 'a &lt; b'
