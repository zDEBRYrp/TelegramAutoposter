"""Топики форума и автослоумод: БД, кэш, отправка в тред."""
import asyncio
import time as _t
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

import user
import main
from sqliter import DBConnection


def run(coro):
    return asyncio.run(coro)


def memdb():
    return DBConnection(db_path=':memory:')


def test_migration_topic_slowmode_columns():
    db = memdb()
    try:
        cols = [r[1] for r in db.c.execute('PRAGMA table_info(CHANNELS)').fetchall()]
        for c in ('SLOWMODE', 'SLOWMODE_AT', 'TOPIC_ID', 'TOPIC_NAME', 'SYNC_SLOW',
                  'IS_FORUM', 'FORUM_AT'):
            assert c in cols
        scols = [r[1] for r in db.c.execute('PRAGMA table_info(SETTINGS)').fetchall()]
        assert 'SYNC_SLOWMODE' in scols
    finally:
        db.c.close()
        db.conn.close()


def test_forum_flag_default_and_roundtrip():
    db = memdb()
    try:
        db.add_channel(-1)
        assert db.get_forum(-1) == (1, 0.0)  # дефолт: показываем выбор
        assert db.set_forum(-1, False) is True
        flag, at = db.get_forum(-1)
        assert flag == 0 and at > 0
    finally:
        db.c.close()
        db.conn.close()


def test_slowmode_and_topic_roundtrip():
    db = memdb()
    try:
        db.add_channel(-1)
        assert db.get_slowmode(-1) == (0, 0.0)
        assert db.set_slowmode(-1, 90) is True
        sec, at = db.get_slowmode(-1)
        assert sec == 90 and at > 0
        assert db.get_topic(-1) == (0, '')
        assert db.set_topic(-1, 11, 'News') is True
        assert db.get_topic(-1) == (11, 'News')
        assert db.get_sync_slowmode() == 1
        assert db.set_sync_slowmode(0) is True
        assert db.get_sync_slowmode() == 0
    finally:
        db.c.close()
        db.conn.close()


def test_effective_delay():
    assert user._effective_delay(5, 0, True) == 300
    assert user._effective_delay(5, 30, True) == 300  # слоумод меньше КД
    assert user._effective_delay(1, 300, True) == 300  # впритык к слоумоду
    assert user._effective_delay(1, 300, False) == 60  # синх выкл
    assert user._effective_delay(None, 0, True) == 300


class SlowClient:
    def __init__(self, seconds=90, fail=False):
        self.seconds = seconds
        self.fail = fail
        self.invokes = 0

    async def resolve_peer(self, cid):
        return ('peer', cid)

    async def invoke(self, req):
        self.invokes += 1
        if self.fail:
            raise RuntimeError('net down')
        return NS(full_chat=NS(slowmode_seconds=self.seconds))


class StubDB:
    def __init__(self):
        self.saved = {}
        self.store = {}

    def get_slowmode(self, cid):
        return self.store.get(cid, (0, 0.0))

    def set_slowmode(self, cid, sec):
        self.saved[cid] = sec
        return True


def test_slowmode_cached_no_api(monkeypatch):
    db = StubDB()
    db.store[-1] = (30, _t.time())
    stub = SlowClient()
    monkeypatch.setattr(user, 'client', stub)
    assert run(user.get_channel_slowmode(-1, db)) == 30
    assert stub.invokes == 0  # свежий кэш — сеть не дёргаем


def test_slowmode_stale_refetch(monkeypatch):
    from unittest.mock import AsyncMock
    db = StubDB()
    db.store[-1] = (0, _t.time() - 7200)
    stub = SlowClient(seconds=120)
    monkeypatch.setattr(user, 'client', stub)
    monkeypatch.setattr(user, 'ensure_connected', AsyncMock(return_value=True))
    assert run(user.get_channel_slowmode(-1, db)) == 120
    assert stub.invokes == 1
    assert db.saved[-1] == 120


def test_slowmode_api_error_returns_cached(monkeypatch):
    from unittest.mock import AsyncMock
    db = StubDB()
    db.store[-1] = (45, 0.0)
    stub = SlowClient(fail=True)
    monkeypatch.setattr(user, 'client', stub)
    monkeypatch.setattr(user, 'ensure_connected', AsyncMock(return_value=True))
    assert run(user.get_channel_slowmode(-1, db)) == 45


class TopicClient:
    def __init__(self, topics=None, fail=False):
        self.topics = topics or []
        self.fail = fail

    async def resolve_peer(self, cid):
        return ('peer', cid)

    async def invoke(self, req):
        if self.fail:
            raise RuntimeError('not a forum')
        return NS(topics=self.topics)


def test_get_forum_topics_normalized(monkeypatch):
    from unittest.mock import AsyncMock
    stub = TopicClient(topics=[NS(id=1, top_message=11, title='News'),
                               NS(id=0, top_message=0, title='skip')])
    monkeypatch.setattr(user, 'client', stub)
    monkeypatch.setattr(user, 'ensure_connected', AsyncMock(return_value=True))
    assert run(user.get_forum_topics(-100)) == [{'id': 11, 'title': 'News'}]


def test_get_forum_topics_error(monkeypatch):
    from unittest.mock import AsyncMock
    stub = TopicClient(fail=True)
    monkeypatch.setattr(user, 'client', stub)
    monkeypatch.setattr(user, 'ensure_connected', AsyncMock(return_value=True))
    with pytest.raises(RuntimeError):
        run(user.get_forum_topics(-100))


def test_forward_to_topic_invokes_raw(monkeypatch):
    calls = []

    class C:
        async def resolve_peer(self, cid):
            return ('peer', cid)

        async def invoke(self, req):
            calls.append(req)
            return True

    monkeypatch.setattr(user, 'client', C())
    run(user._forward_to_topic(-10, -1001, [55], 11))
    assert calls[0].top_msg_id == 11
    assert list(calls[0].id) == [55]
    assert len(calls[0].random_id) == 1


def test_detect_forum_matrix(monkeypatch):
    from pyrogram import raw as _raw

    class PeerChannel:
        pass

    # подменяем isinstance-проверку через настоящий класс
    RealInputPeerChannel = _raw.types.InputPeerChannel

    class C:
        def __init__(self, peer, chats=None, fail=None):
            self.peer = peer
            self.chats = chats
            self.fail = fail

        async def resolve_peer(self, cid):
            if isinstance(self.fail, Exception) and getattr(self, '_stage', '') == 'resolve':
                raise self.fail
            return self.peer

        async def invoke(self, req):
            if isinstance(self.fail, Exception):
                raise self.fail
            from types import SimpleNamespace as NS
            return NS(chats=self.chats)

    async def no_conn():
        return False

    monkeypatch.setattr(user, 'ensure_connected', no_conn)
    assert run(user.detect_forum(-1)) is None  # нет соединения

    from unittest.mock import AsyncMock
    monkeypatch.setattr(user, 'ensure_connected', AsyncMock(return_value=True))

    # обычная группа (не канал) — сразу False без запросов
    from types import SimpleNamespace as NS
    basic = NS()
    monkeypatch.setattr(user, 'client', C(basic))
    # подменяем isinstance: проще подсунуть настоящий InputPeerChannel только для True-ветки
    assert run(user.detect_forum(-1)) is False

    # супергруппа-форум
    class FakePeer(RealInputPeerChannel):
        def __init__(self):
            pass  # без инициализации базового

    monkeypatch.setattr(user, 'client', C(FakePeer(), chats=[NS(forum=True)]))
    assert run(user.detect_forum(-1)) is True
    monkeypatch.setattr(user, 'client', C(FakePeer(), chats=[NS(forum=False)]))
    assert run(user.detect_forum(-1)) is False
    # пусто — неизвестно
    monkeypatch.setattr(user, 'client', C(FakePeer(), chats=[]))
    assert run(user.detect_forum(-1)) is None


def test_refresh_forum_flag_writes_only_answers(monkeypatch):
    from unittest.mock import AsyncMock

    class DB:
        def __init__(self):
            self.writes = []
            self.flag = (1, 0.0)

        def get_forum(self, cid):
            return self.flag

        def set_forum(self, cid, v):
            self.writes.append((cid, v))
            return True

    db = DB()
    monkeypatch.setattr(user, 'detect_forum', AsyncMock(return_value=True))
    assert run(user.refresh_forum_flag(-1, db, force=True)) is True
    assert db.writes == [(-1, True)]
    monkeypatch.setattr(user, 'detect_forum', AsyncMock(return_value=None))
    assert run(user.refresh_forum_flag(-1, db, force=True)) is None
    assert db.writes == [(-1, True)]  # 'не знаю' не пишем


def test_topic_button_hidden_without_forum():
    db = memdb()
    old = main.db
    main.db = db
    try:
        db.add_channel(-1)
        kb = main.get_chat_settings_keyboard(-1)
        cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
        assert any(c.startswith('TOPIC:') for c in cbs)  # дефолт: показываем
        db.set_forum(-1, False)
        kb = main.get_chat_settings_keyboard(-1)
        cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
        assert not any(c.startswith('TOPIC:') for c in cbs)  # точно не форум — прячем
        assert 'Тема' not in main.format_chat_info(-1)
        db.set_forum(-1, True)
        assert 'Тема' in main.format_chat_info(-1)
    finally:
        main.db = old
        db.c.close()
        db.conn.close()


def test_deliver_topic_passthrough(monkeypatch):
    got = {}

    async def fake_send(chat_id, text, photo_path=None, video_path=None, topic=0, mention=''):
        got['topic'] = topic
        return True, ''

    async def fake_fwd(*a, **k):
        raise AssertionError('no fwd here')

    monkeypatch.setattr(user, '_send_with_fallback', fake_send)
    ok, err = run(user._deliver(-10, 'hi', topic=7))
    assert (ok, err) == (True, '')
    assert got['topic'] == 7


def test_chat_card_topic_slow_lines():
    db = memdb()
    old = main.db
    main.db = db
    try:
        db.add_channel(-1)
        db.set_topic(-1, 11, 'News')
        db.set_slowmode(-1, 90)
        info = main.format_chat_info(-1)
    finally:
        main.db = old
        db.c.close()
        db.conn.close()
    assert 'News' in info and '90с' in info


def test_topic_button_and_handlers():
    import inspect
    src = inspect.getsource(main.callback_handler)
    assert 'TOPIC_SET:' in src and 'TOPIC_MANUAL:' in src and 'SET_TOGGLE_SYNC' in src
    assert 'TOGGLE_SYNC:' in src
    names = [h.callback.__name__ for h in main.router.message.handlers]
    assert 'input_topic_id' in names
    kb = main.get_chat_settings_keyboard(-1)
    cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert any(c.startswith('TOPIC:') for c in cbs)
    text, card = main.build_settings_card()
    cbs2 = [b.callback_data for row in card.inline_keyboard for b in row]
    assert 'SET_TOGGLE_SYNC' in cbs2


def test_sync_slow_roundtrip():
    from sqliter import DBConnection
    db = DBConnection(db_path=':memory:')
    try:
        db.add_channel(-1)
        assert db.get_sync_slow(-1) == 1  # по умолчанию участвуем
        assert db.set_sync_slow(-1, 0) is True
        assert db.get_sync_slow(-1) == 0
    finally:
        db.c.close()
        db.conn.close()


def test_migration_sync_slow_column():
    from sqliter import DBConnection
    db = DBConnection(db_path=':memory:')
    try:
        cols = [r[1] for r in db.c.execute('PRAGMA table_info(CHANNELS)').fetchall()]
        assert 'SYNC_SLOW' in cols
    finally:
        db.c.close()
        db.conn.close()


def test_chat_sync_on_matrix():
    import user as _u

    class FullDB:
        def __init__(self, g, p):
            self.g, self.p = g, p

        def get_sync_slowmode(self):
            return self.g

        def get_sync_slow(self, cid):
            return self.p

    assert _u._chat_sync_on(FullDB(1, 1), -1) is True
    assert _u._chat_sync_on(FullDB(1, 0), -1) is False
    assert _u._chat_sync_on(FullDB(0, 1), -1) is False
    assert _u._chat_sync_on(object(), -1) is True  # без методов — вкл


def test_floor_holds_when_slowmode_drops():
    import user as _u
    # слоумод упал 30мин -> 2с, минимум юзера 30мин — не спамим
    assert _u._effective_delay(30, 1800, True) == 1800
    assert _u._effective_delay(30, 2, True) == 1800


def test_sync_button_only_with_slowmode():
    from sqliter import DBConnection
    db = DBConnection(db_path=':memory:')
    old = main.db
    main.db = db
    try:
        db.add_channel(-1)
        kb = main.get_chat_settings_keyboard(-1)
        cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
        assert not any(c.startswith('TOGGLE_SYNC:') for c in cbs)  # слоумода нет — кнопки нет
        db.set_slowmode(-1, 30)
        kb = main.get_chat_settings_keyboard(-1)
        cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
        assert 'TOGGLE_SYNC:-1' in cbs  # слоумод есть — кнопка есть
        info = main.format_chat_info(-1)
        assert 'Слоумод' in info and 'минимум' in info
    finally:
        main.db = old
        db.c.close()
        db.conn.close()


def test_card_no_slowmode_no_line():
    from sqliter import DBConnection
    db = DBConnection(db_path=':memory:')
    old = main.db
    main.db = db
    try:
        db.add_channel(-2)
        assert 'Слоумод' not in main.format_chat_info(-2)
    finally:
        main.db = old
        db.c.close()
        db.conn.close()
