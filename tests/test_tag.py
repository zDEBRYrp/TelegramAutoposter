"""Скрытые отметки: участники, невидимки, отправка, тумблер."""
import asyncio
from types import SimpleNamespace as NS

import user
import main
from sqliter import DBConnection


def run(coro):
    return asyncio.run(coro)


def memdb():
    return DBConnection(db_path=':memory:')


def test_anchor_is_word_joiner():
    assert user.TAG_ANCHOR == chr(0x2060)
    assert len(user.TAG_ANCHOR) == 1


def test_tag_flag_roundtrip_and_migration():
    db = memdb()
    try:
        cols = [r[1] for r in db.c.execute('PRAGMA table_info(CHANNELS)').fetchall()]
        assert 'TAG_ALL' in cols
        db.add_channel(-1)
        assert db.get_tag_all(-1) == 0
        assert db.set_tag_all(-1, 1) is True
        assert db.get_tag_all(-1) == 1
    finally:
        db.c.close()
        db.conn.close()


class TagClient:
    def __init__(self, members):
        self.members = members
        self.gen_calls = 0
        self.warmed = []

    async def get_chat_members(self, chat_id, limit=None):
        self.gen_calls += 1
        for m in self.members:
            yield m

    async def get_users(self, ids):
        self.warmed.append(list(ids))
        return [NS(id=i) for i in ids]


def _member(uid, bot=False, deleted=False):
    return NS(user=NS(id=uid, is_bot=bot, is_deleted=deleted))


def test_get_tag_members_filters_and_limits(monkeypatch):
    stub = TagClient([_member(1), _member(2, bot=True), _member(3, deleted=True),
                      _member(4), _member(5)])
    monkeypatch.setattr(user, 'client', stub)
    monkeypatch.setattr(user, '_tag_cache', {})
    from unittest.mock import AsyncMock
    monkeypatch.setattr(user, 'ensure_connected', AsyncMock(return_value=True))
    ids = run(user.get_tag_members(-100, limit=2))
    assert ids == [1, 4]
    # второй раз — из кэша
    run(user.get_tag_members(-100, limit=2))
    assert stub.gen_calls == 1


def test_build_mentions(monkeypatch):
    stub = TagClient([_member(11), _member(22)])
    monkeypatch.setattr(user, 'client', stub)
    monkeypatch.setattr(user, '_tag_cache', {})
    from unittest.mock import AsyncMock
    monkeypatch.setattr(user, 'ensure_connected', AsyncMock(return_value=True))

    class DB:
        def get_tag_all(self, cid):
            return 1

    out = run(user.build_mentions(-100, DB()))
    assert out.count('tg://user?id=') == 2
    assert all(chr(0x2060) in part for part in out.split('><')[1:])
    assert stub.warmed == [[11, 22]]  # пиры прогреты


def test_mentions_capped_at_five(monkeypatch):
    # Telegram уведомляет только о первых ~5 упоминаниях — больше не пихаем
    assert user.TAG_LIMIT == 5
    stub = TagClient([_member(i) for i in range(1, 20)])
    monkeypatch.setattr(user, 'client', stub)
    monkeypatch.setattr(user, '_tag_cache', {})
    from unittest.mock import AsyncMock
    monkeypatch.setattr(user, 'ensure_connected', AsyncMock(return_value=True))

    class DB:
        def get_tag_all(self, cid):
            return 1

    out = run(user.build_mentions(-100, DB()))
    assert out.count('tg://user?id=') == 5
    for i in range(1, 6):
        assert f'tg://user?id={i}' in out


def test_build_mentions_off_or_empty(monkeypatch):
    from unittest.mock import AsyncMock
    monkeypatch.setattr(user, 'ensure_connected', AsyncMock(return_value=True))

    class OffDB:
        def get_tag_all(self, cid):
            return 0

    assert run(user.build_mentions(-100, OffDB())) == ''
    assert run(user.build_mentions(-100, object())) == ''  # нет метода — тихо


class SendClient:
    def __init__(self):
        self.calls = []

    async def send_message(self, chat_id, text, parse_mode=None, reply_to_message_id=None):
        self.calls.append(('msg', text))
        return True

    async def send_photo(self, *a, **k):
        raise AssertionError('no photo here')

    async def send_video(self, *a, **k):
        raise AssertionError('no video here')

    async def forward_messages(self, chat_id, from_chat_id, message_ids):
        self.calls.append(('fwd', from_chat_id, list(message_ids)))
        return True

    async def get_users(self, ids):
        return []


def test_deliver_appends_mentions(monkeypatch):
    stub = SendClient()
    monkeypatch.setattr(user, 'client', stub)
    mention = f'<a href="tg://user?id=7">{user.TAG_ANCHOR}</a>'
    ok, err = run(user._deliver(-10, 'hi', mention=mention))
    assert (ok, err) == (True, '')
    assert stub.calls[0][1].endswith(mention)


def test_deliver_ghost_mentions_for_forward(monkeypatch):
    stub = SendClient()
    monkeypatch.setattr(user, 'client', stub)
    mention = f'<a href="tg://user?id=7">{user.TAG_ANCHOR}</a>'
    ok, err = run(user._deliver(-10, '', fwd=(-1001, 55), mention=mention))
    assert (ok, err) == (True, '')
    assert stub.calls[0][0] == 'fwd'
    assert stub.calls[1] == ('msg', mention)


def test_tag_toggle_ui():
    import inspect
    assert 'TOGGLE_TAG:' in inspect.getsource(main.callback_handler)
    db = memdb()
    old = main.db
    main.db = db
    try:
        db.add_channel(-1)
        kb = main.get_chat_settings_keyboard(-1)
        cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
        assert 'TOGGLE_TAG:-1' in cbs
        assert 'Отметки' in main.format_chat_info(-1)
    finally:
        main.db = old
        db.c.close()
        db.conn.close()
