"""Кэш диалогов, bulk-статусы, переименование time->global_time."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import user
import main
from pyrogram import enums


def _dlg(cid, title='C'):
    return SimpleNamespace(chat=SimpleNamespace(
        type=enums.ChatType.SUPERGROUP, title=title, id=cid))


class _StubClient:
    def __init__(self, dialogs):
        self.dialogs = dialogs
        self.calls = 0

    async def get_dialogs(self):
        self.calls += 1
        for d in self.dialogs:
            yield d


def _patch_conn(monkeypatch, stub):
    monkeypatch.setattr(user, 'ensure_connected', AsyncMock(return_value=True))
    monkeypatch.setattr(user, 'client', stub)
    user._chats_cache['at'] = 0.0
    user._chats_cache['data'] = []


def test_get_chats_cached(monkeypatch):
    stub = _StubClient([_dlg(-1)])
    _patch_conn(monkeypatch, stub)
    try:
        first = asyncio.run(user.get_chats())
        second = asyncio.run(user.get_chats())
        assert first == [{'title': 'C', 'id': -1}]
        assert stub.calls == 1  # второй раз — из кэша, без сети
        assert first is not second  # копии, мутации не портят кэш
    finally:
        user._chats_cache['at'] = 0.0
        user._chats_cache['data'] = []


def test_get_chats_force_and_ttl(monkeypatch):
    stub = _StubClient([_dlg(-1)])
    _patch_conn(monkeypatch, stub)
    try:
        asyncio.run(user.get_chats())
        asyncio.run(user.get_chats(force=True))
        assert stub.calls == 2
        user._chats_cache['at'] = 0.0  # протухший кэш
        asyncio.run(user.get_chats())
        assert stub.calls == 3
    finally:
        user._chats_cache['at'] = 0.0
        user._chats_cache['data'] = []


def test_keyboard_uses_bulk_statuses(monkeypatch):
    from sqliter import DBConnection
    from unittest.mock import AsyncMock, patch
    db = DBConnection(db_path=':memory:')
    db.add_channel(-1)
    db.c.execute('UPDATE CHANNELS SET SPAM_ENABLED = 1 WHERE CHANNEL = ?',
                 [str(-1)])
    db.conn.commit()
    db.add_channel(-2)

    def boom(cid):
        raise AssertionError('поштучный запрос статуса вместо bulk')

    old = main.db
    main.db = db
    try:
        with patch.object(main.user, 'get_chats',
                          new=AsyncMock(return_value=[{'id': -1, 'title': 'A'},
                                                     {'id': -2, 'title': 'B'}])):
            monkeypatch.setattr(db, 'get_channel_spam_status', boom)
            kb, header = asyncio.run(main.get_chats_keyboard(0, 'all'))
        assert 'активно 1 из 2' in header
        toggles = [b.text for row in kb.inline_keyboard[1:]
                   for b in row if b.callback_data.startswith('TOGGLE_SPAM:')]
        assert toggles[0].startswith('✅') and toggles[1].startswith('❌')
    finally:
        main.db = old


def test_global_time_renamed():
    assert hasattr(main, 'global_time')
    assert not hasattr(main, 'time')
