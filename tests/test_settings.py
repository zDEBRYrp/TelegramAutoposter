"""Настройки: миграция, тумблеры, лог-чат, массовые действия."""
import asyncio
import sqlite3

import main
from sqliter import DBConnection


def _memdb():
    return DBConnection(db_path=':memory:')


def test_migration_adds_columns_to_old_db(tmp_path):
    p = str(tmp_path / 'old.db')
    c = sqlite3.connect(p)
    c.execute('CREATE TABLE SETTINGS (ID INTEGER PRIMARY KEY, PHOTO TEXT DEFAULT "", '
              'VIDEO TEXT DEFAULT "", TEXT TEXT DEFAULT "x", SPAM INTEGER DEFAULT 0, '
              'TIMEOUT INTEGER DEFAULT 5)')
    c.execute('CREATE TABLE CHANNELS (CHANNEL INTEGER PRIMARY KEY, ADDITIONAL TEXT DEFAULT "", '
              'SPAM_ENABLED INTEGER DEFAULT 1, TIMEOUT INTEGER DEFAULT 5, '
              'POST_PHOTO TEXT DEFAULT "", POST_VIDEO TEXT DEFAULT "", POST_TEXT TEXT DEFAULT "")')
    c.execute('INSERT INTO SETTINGS (ID) VALUES (1)')
    c.commit()
    c.close()
    db = DBConnection(db_path=p)
    cols = [r[1] for r in db.c.execute('PRAGMA table_info(SETTINGS)').fetchall()]
    assert 'LOG_ENABLED' in cols and 'LOG_CHAT' in cols and 'REPORT_ENABLED' in cols
    ch_cols = [r[1] for r in db.c.execute('PRAGMA table_info(CHANNELS)').fetchall()]
    assert 'SEND_NEXT' in ch_cols
    assert db.get_log_config() == (0, '')
    assert db.get_report_enabled() == 1
    db.c.close()
    db.conn.close()


def test_log_and_report_flags():
    db = _memdb()
    try:
        assert db.get_log_config() == (0, '')
        assert db.get_report_enabled() == 1
        assert db.set_log_chat(-100123) is True
        assert db.set_log_enabled(1) is True
        assert db.get_log_config() == (1, '-100123')
        assert db.set_report_enabled(0) is True
        assert db.get_report_enabled() == 0
    finally:
        db.c.close()
        db.conn.close()


def test_set_all_channels():
    db = _memdb()
    try:
        db.add_channel(-1)
        db.add_channel(-2)
        assert db.set_all_channels(1) == 2
        assert db.get_channel_spam_status(-1) == 1
        assert db.set_all_channels(0) == 2
        assert db.get_channel_spam_status(-2) == 0
    finally:
        db.c.close()
        db.conn.close()


class StubClient:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    async def send_message(self, chat_id, text, parse_mode=None):
        if self.fail:
            raise RuntimeError('nope')
        self.sent.append((chat_id, text))
        return True

    async def send_photo(self, chat_id, photo, caption=None, parse_mode=None):
        self.sent.append((chat_id, photo, caption))
        return True

    async def send_video(self, chat_id, video, caption=None, parse_mode=None):
        self.sent.append((chat_id, video, caption))
        return True


def run(coro):
    return asyncio.run(coro)


class StubDB:
    def __init__(self, enabled=1, target='-100999'):
        self.enabled = enabled
        self.target = target

    def get_log_config(self):
        return self.enabled, self.target


def test_log_send_quote_and_tag(monkeypatch):
    import user
    stub = StubClient()
    monkeypatch.setattr(user, 'client', stub)
    run(user._log_send(StubDB(), {'id': -1, 'title': 'C'}, 'hello'))
    assert len(stub.sent) == 1  # всё одним сообщением
    text = stub.sent[0][1]
    assert '#log' in text
    assert '<blockquote>hello</blockquote>' in text
    assert '-1' in text


def test_log_send_media_caption_plus_quote(monkeypatch, tmp_path):
    import user
    pic = tmp_path / 'p.jpg'
    pic.write_bytes(b'x')
    stub = StubClient()
    monkeypatch.setattr(user, 'client', stub)
    run(user._log_send(StubDB(), {'id': -1, 'title': 'C'}, 'hi',
                       photo_path=str(pic)))
    assert len(stub.sent) == 1  # влезло в подпись — одним сообщением
    assert '#log' in stub.sent[0][2]
    assert '<blockquote>hi</blockquote>' in stub.sent[0][2]


def test_log_send_media_no_text_single(monkeypatch, tmp_path):
    import user
    pic = tmp_path / 'p.jpg'
    pic.write_bytes(b'x')
    stub = StubClient()
    monkeypatch.setattr(user, 'client', stub)
    run(user._log_send(StubDB(), {'id': -1, 'title': 'C'}, '',
                       photo_path=str(pic)))
    assert len(stub.sent) == 1  # только медиа с подписью


def test_log_send_disabled_or_empty(monkeypatch):
    import user
    stub = StubClient()
    monkeypatch.setattr(user, 'client', stub)
    run(user._log_send(StubDB(enabled=0), {'id': -1}, 'hello'))
    run(user._log_send(StubDB(enabled=1, target=''), {'id': -1}, 'hello'))
    assert stub.sent == []


def test_log_send_never_raises(monkeypatch):
    import user
    stub = StubClient(fail=True)
    monkeypatch.setattr(user, 'client', stub)
    run(user._log_send(StubDB(), {'id': -1}, 'hello'))  # не должно упасть


def test_settings_card_builds(monkeypatch):
    db = _memdb()
    old = main.db
    main.db = db
    try:
        db.set_log_enabled(1)
        db.set_log_chat('@logchan')
        text, kb = main.build_settings_card()
    finally:
        main.db = old
        db.c.close()
        db.conn.close()
    assert 'Настройки' in text and '@logchan' in text
    cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
    for cb in ('SET_TOGGLE_LOG', 'SET_LOG_CHAT', 'SET_TOGGLE_REPORT', 'SET_ALL_ON',
               'SET_ALL_OFF', 'SET_REFRESH_CHATS', 'SET_CLEAR_STATUS', 'SETTINGS_CLOSE'):
        assert cb in cbs


def test_settings_handlers_registered():
    names = [h.callback.__name__ for h in main.router.message.handlers]
    assert 'settings_menu' in names
    assert 'input_log_chat' in names
    assert 'echo_forward' in names


def test_parse_log_target():
    from types import SimpleNamespace as NS
    assert main._parse_log_target(NS(forward_from_chat=NS(id=-1001), text=None)) == -1001
    assert main._parse_log_target(NS(forward_from_chat=None, text='-1002227496698')) == -1002227496698
    assert main._parse_log_target(NS(forward_from_chat=None, text='@durov')) == '@durov'
    assert main._parse_log_target(NS(forward_from_chat=None, text='https://t.me/durov')) == '@durov'
    assert main._parse_log_target(NS(forward_from_chat=None, text='')) is None
    assert main._parse_log_target(NS(forward_from_chat=None, text=None)) is None


def test_idle_hint():
    from types import SimpleNamespace as NS
    assert 'перезапуск' in main._idle_hint(NS(forward_from_chat=NS(id=1), text='x'))
    assert 'ID чата' in main._idle_hint(NS(forward_from_chat=None, text='-100123'))
    assert main._idle_hint(NS(forward_from_chat=None, text='привет')) is None


def test_start_bat_russian():
    import pathlib
    raw = pathlib.Path('start.bat').read_bytes()
    assert b'\xef\xbb\xbf' not in raw[:3]  # без BOM — иначе cmd давится первой строкой
    total_lf = raw.count(b'\n')
    crlf = raw.count(b'\r\n')
    assert total_lf > 0 and crlf == total_lf, 'start.bat должен быть CRLF (cmd глючит на LF-блоках)'
    src = raw.decode('utf-8')
    assert 'chcp 65001' in src
    assert 'Запуск бота' in src
    assert 'Zapusk' not in src
    assert 'cd /d "%~dp0"' in src


def test_input_log_chat_never_silent():
    import inspect
    src = inspect.getsource(main.input_log_chat)
    assert '_parse_log_target' in src
    assert 'input_log_chat упал' in src  # внешний guard с сообщением


def test_bulk_actions_need_confirm():
    import inspect
    src = inspect.getsource(main.callback_handler)
    assert 'SET_ALL_ON_YES' in src and 'SET_ALL_OFF_YES' in src


def test_send_next_roundtrip():
    db = _memdb()
    try:
        db.add_channel(-1)
        assert db.get_send_next(-1) == 0.0
        assert db.set_send_next(-1, 1234.5) is True
        assert db.get_send_next(-1) == 1234.5
        assert db.get_send_next(-999) == 0.0
    finally:
        db.c.close()
        db.conn.close()
