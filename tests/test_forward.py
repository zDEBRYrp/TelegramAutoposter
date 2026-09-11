"""Посты-пересылки: БД, отправка форвардом, превью, спойлер-fix."""
import asyncio
from types import SimpleNamespace as NS

import user
import main
from sqliter import DBConnection


def run(coro):
    return asyncio.run(coro)


def memdb():
    return DBConnection(db_path=':memory:')


def test_migration_fwd_columns():
    db = memdb()
    try:
        scols = [r[1] for r in db.c.execute('PRAGMA table_info(SETTINGS)').fetchall()]
        ccols = [r[1] for r in db.c.execute('PRAGMA table_info(CHANNELS)').fetchall()]
        assert 'FWD_CHAT' in scols and 'FWD_MSG' in scols
        assert 'POST_FWD_CHAT' in ccols and 'POST_FWD_MSG' in ccols
    finally:
        db.c.close()
        db.conn.close()


def test_global_forward_mutual_exclusivity():
    db = memdb()
    try:
        assert db.get_forward() == (0, 0)
        assert db.set_forward(-1001, 55) is True
        assert db.get_forward() == (-1001, 55)
        assert db.change_photo('p.jpg') is True  # фото вытесняет форвард
        assert db.get_forward() == (0, 0)
        assert db.set_forward(-1001, 55) is True
        assert db.change_video('v.mp4') is True
        assert db.get_forward() == (0, 0)
        assert db.set_forward(-1001, 55) is True
        assert db.clear_global_media() is True
        s = db.settings()
        assert (s[1], s[2]) == ('', '')
        assert db.get_forward() == (0, 0)
    finally:
        db.c.close()
        db.conn.close()


def test_channel_forward():
    db = memdb()
    try:
        db.add_channel(-5)
        assert db.get_channel_forward(-5) == (0, 0)
        assert db.set_channel_forward(-5, -1001, 77) is True
        assert db.get_channel_forward(-5) == (-1001, 77)
        assert db.set_channel_post(-5, photo='p.jpg') is True  # фото вытесняет
        assert db.get_channel_forward(-5) == (0, 0)
        assert db.set_channel_forward(-5, -1001, 77) is True
        assert db.set_channel_post(-5, text='hi') is True  # текст живёт рядом
        assert db.get_channel_forward(-5) == (-1001, 77)
        assert db.clear_channel_post(-5) is True
        assert db.get_channel_forward(-5) == (0, 0)
        assert db.get_channel_post(-5) == ('', '', '')
    finally:
        db.c.close()
        db.conn.close()


class FwdClient:
    def __init__(self, fail_forward=False):
        self.calls = []
        self.fail_forward = fail_forward

    async def forward_messages(self, chat_id, from_chat_id, message_ids):
        self.calls.append(('fwd', chat_id, from_chat_id, list(message_ids)))
        if self.fail_forward:
            raise RuntimeError('CHANNEL_PRIVATE')
        return True

    async def send_message(self, chat_id, text, parse_mode=None):
        self.calls.append(('msg', chat_id, text, parse_mode))
        return True

    async def send_photo(self, chat_id, photo, caption=None, parse_mode=None):
        self.calls.append(('photo', chat_id, caption))
        return True

    async def send_video(self, chat_id, video, caption=None, parse_mode=None):
        self.calls.append(('video', chat_id, caption))
        return True


def test_deliver_forward_then_text(monkeypatch):
    stub = FwdClient()
    monkeypatch.setattr(user, 'client', stub)
    ok, err = run(user._deliver(-10, 'hello', fwd=(-1001, 55)))
    assert (ok, err) == (True, '')
    assert stub.calls[0] == ('fwd', -10, -1001, [55])
    assert stub.calls[1][0] == 'msg' and stub.calls[1][2] == 'hello'


def test_deliver_forward_no_text(monkeypatch):
    stub = FwdClient()
    monkeypatch.setattr(user, 'client', stub)
    ok, err = run(user._deliver(-10, '', fwd=(-1001, 55)))
    assert (ok, err) == (True, '')
    assert len(stub.calls) == 1


def test_deliver_forward_failure(monkeypatch):
    stub = FwdClient(fail_forward=True)
    monkeypatch.setattr(user, 'client', stub)
    ok, err = run(user._deliver(-10, 'hello', fwd=(-1001, 55)))
    assert ok is False and 'CHANNEL_PRIVATE' in err


def test_to_html_spoiler_normalized():
    assert user._to_html('||x||') == '<spoiler>x</spoiler>'


def test_preview_forward(monkeypatch):
    from unittest.mock import AsyncMock, patch
    stub = FwdClient()
    monkeypatch.setattr(user, 'client', stub)
    with patch.object(user, 'ensure_connected', new=AsyncMock(return_value=True)):
        ok, err = run(main.preview_forward(-1, -1001, 55))
    assert (ok, err) == (True, '')
    assert stub.calls[0] == ('fwd', -1, -1001, [55])


def test_capture_forward():
    fwd = NS(forward_from_chat=NS(id=-1001), forward_from_message_id=77)
    assert run(main._capture_forward(fwd)) == (True, -1001, 77, '')
    no_msg = NS(forward_from_chat=NS(id=-1001), forward_from_message_id=None)
    ok, _, _, err = run(main._capture_forward(no_msg))
    assert ok is False and err
    plain = NS(forward_from_chat=None, forward_from_message_id=None,
               text='hi', entities=None)
    ok, _, _, err = run(main._capture_forward(plain))
    assert ok is False and 'не пересылка' in err


def test_forward_buttons_and_handlers():
    import inspect
    src = inspect.getsource(main.callback_handler)
    assert "'EDIT_FORWARD'" in src and 'CHANNEL_EDIT_FORWARD' in src
    names = [h.callback.__name__ for h in main.router.message.handlers]
    assert 'input_global_forward' in names
    assert 'input_channel_post_forward' in names
