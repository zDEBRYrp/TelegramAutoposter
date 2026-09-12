"""Отмена вводов, чистые подтверждения, порядок по свежести, мультивыбор."""
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import main
from sqliter import DBConnection


def run(coro):
    return asyncio.run(coro)


def memdb():
    return DBConnection(db_path=':memory:')


def test_cancel_button_everywhere():
    import inspect
    src = inspect.getsource(main.callback_handler)
    assert "'CANCEL_INPUT'" in src
    assert main.cancel_kb().inline_keyboard[0][0].callback_data == 'CANCEL_INPUT'
    # запросы ввода несут кнопку отмены
    for branch in ('CHANGE_TIMEOUT', 'ADD_ADDITIONAL', 'CHANNEL_EDIT_TEXT',
                   'EDIT_TEXT', 'EDIT_PHOTO', 'INTERVAL', 'INPUT_CHAT_ID',
                   'SET_LOG_CHAT', 'TOPIC_MANUAL', 'MULTI_TIMEOUT'):
        assert branch in src
    assert src.count('cancel_kb()') >= 10


class StubBot:
    def __init__(self):
        self.deleted = []
        self.sent = []

    async def delete_message(self, chat_id, msg_id):
        self.deleted.append((chat_id, msg_id))

    async def send_message(self, chat_id, text, reply_markup=None, parse_mode=None):
        self.sent.append((chat_id, text, reply_markup))
        return NS(message_id=999)


class StubState:
    def __init__(self, data=None):
        self.data = data or {}
        self.cleared = False

    async def get_data(self):
        return dict(self.data)

    async def clear(self):
        self.cleared = True


class StubMsg:
    def __init__(self):
        self.chat = NS(id=-100)
        self.deleted = False

    async def delete(self):
        self.deleted = True


def test_complete_input_deletes_prompt_and_sends_fresh(monkeypatch):
    stub = StubBot()
    monkeypatch.setattr(main, 'bot', stub)
    m = StubMsg()
    st = StubState({'prompt_id': 10})
    run(main._complete_input(m, st, '✅ Готово!', main.back_to_chats_kb()))
    assert m.deleted is True
    assert (m.chat.id, 10) in stub.deleted  # запрос удалён
    assert len(stub.sent) == 1 and stub.sent[0][1] == '✅ Готово!'
    assert st.cleared is True


def test_complete_input_without_prompt(monkeypatch):
    stub = StubBot()
    monkeypatch.setattr(main, 'bot', stub)
    m = StubMsg()
    st = StubState({})
    run(main._complete_input(m, st, 'ok'))
    assert stub.deleted == [] or (m.chat.id, 10) not in stub.deleted
    assert len(stub.sent) == 1


def test_bulk_db_methods():
    db = memdb()
    try:
        for cid in (-1, -2, -3):
            db.add_channel(cid)
        assert db.set_spam_many([-1, -2], 1) == 2
        assert db.get_channel_spam_status(-1) == 1
        assert db.get_channel_spam_status(-3) == 0
        assert db.set_tag_many([-1, -2, -3], 1) == 3
        assert db.get_tag_all(-2) == 1
        assert db.set_spam_many([], 1) == 0
        cols = [r[1] for r in db.c.execute('PRAGMA table_info(CHANNELS)').fetchall()]
        assert 'SORT_TS' in cols
    finally:
        db.c.close()
        db.conn.close()


def test_bump_sort_marks_recency():
    db = memdb()
    try:
        db.add_channel(-1)
        assert db.bump_sort(-1) is True
        ts = db.c.execute('SELECT SORT_TS FROM CHANNELS WHERE CHANNEL = ?',
                          [str(-1)]).fetchone()[0]
        assert ts > 0
    finally:
        db.c.close()
        db.conn.close()


def _seed_ordered(db):
    # A вкл давно, B вкл недавно, C выкл недавно, D выкл никогда
    for cid, title in [(-1, 'Bbb'), (-2, 'Aaa'), (-3, 'Ccc'), (-4, 'Ddd')]:
        db.add_channel(cid)
    db.c.execute('UPDATE CHANNELS SET SPAM_ENABLED = 1, SORT_TS = 100 WHERE CHANNEL = ?',
                 [str(-1)])
    db.c.execute('UPDATE CHANNELS SET SPAM_ENABLED = 1, SORT_TS = 300 WHERE CHANNEL = ?',
                 [str(-2)])
    db.c.execute('UPDATE CHANNELS SET SPAM_ENABLED = 0, SORT_TS = 200 WHERE CHANNEL = ?',
                 [str(-3)])
    db.c.execute('UPDATE CHANNELS SET SPAM_ENABLED = 0, SORT_TS = 0 WHERE CHANNEL = ?',
                 [str(-4)])
    db.conn.commit()


def _toggle_order(kb):
    return [b.callback_data.split(':')[1]
            for row in kb.inline_keyboard[1:]
            for b in row if b.callback_data.startswith('TOGGLE_SPAM:')]


def test_order_enabled_recent_first_then_disabled_recent():
    db = memdb()
    old = main.db
    main.db = db
    try:
        _seed_ordered(db)
        chats = [{'id': -1, 'title': 'Bbb'}, {'id': -2, 'title': 'Aaa'},
                 {'id': -3, 'title': 'Ccc'}, {'id': -4, 'title': 'Ddd'}]
        with patch.object(main.user, 'get_chats', new=AsyncMock(return_value=chats)):
            kb, _ = run(main.get_chats_keyboard(0, 'all'))
        # вкл: сначала недавно (-2), потом давно (-1); выкл: недавно (-3), потом (-4)
        assert _toggle_order(kb) == ['-2', '-1', '-3', '-4']
    finally:
        main.db = old
        db.c.close()
        db.conn.close()


def test_select_mode_keyboard():
    db = memdb()
    old = main.db
    main.db = db
    try:
        db.add_channel(-1)
        db.add_channel(-2)
        chats = [{'id': -1, 'title': 'A'}, {'id': -2, 'title': 'B'}]
        with patch.object(main.user, 'get_chats', new=AsyncMock(return_value=chats)):
            kb, header = run(main.get_chats_keyboard(0, 'all', select_mode=True,
                                                     selected=frozenset({-1})))
        assert 'Выбрано: 1' in header or 'выбрано: 1' in header
        cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
        assert 'SELECT_CHAT:-1:0:all' in cbs
        assert 'MULTI_ON' in cbs and 'MULTI_TAG_OFF' in cbs and 'MULTI_TIMEOUT' in cbs
        texts = [b.text for row in kb.inline_keyboard for b in row]
        assert any(t.startswith('☑️') for t in texts)
        # без выбора — строк действий нет
        kb2, _ = run(main.get_chats_keyboard(0, 'all', select_mode=True,
                                             selected=frozenset()))
        cbs2 = [b.callback_data for row in kb2.inline_keyboard for b in row]
        assert 'MULTI_ON' not in cbs2
    finally:
        main.db = old
        db.c.close()
        db.conn.close()


def test_multi_branches_and_state():
    import inspect
    src = inspect.getsource(main.callback_handler)
    for branch in ('MULTI_MODE:', 'SELECT_CHAT:', 'SELECT_PAGE:', 'SELECT_FILTER:',
                   'MULTI_ON', 'MULTI_TAG_OFF', 'MULTI_TIMEOUT'):
        assert branch in src
    names = [h.callback.__name__ for h in main.router.message.handlers]
    assert 'input_multi_timeout' in names
    assert hasattr(main, 'multi_state')
