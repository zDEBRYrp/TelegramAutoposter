"""Пост = предсказуемое число сообщений (никаких тихих разбиений)."""
import asyncio

import user


def run(coro):
    return asyncio.run(coro)


class CountClient:
    def __init__(self):
        self.calls = []

    async def send_message(self, chat_id, text, parse_mode=None, reply_to_message_id=None):
        self.calls.append(('msg', text))
        return True

    async def send_photo(self, chat_id, photo, caption=None, parse_mode=None,
                         reply_to_message_id=None):
        self.calls.append(('photo', caption))
        return True

    async def send_video(self, chat_id, video, caption=None, parse_mode=None,
                         reply_to_message_id=None):
        self.calls.append(('video', caption))
        return True

    async def forward_messages(self, chat_id, from_chat_id, message_ids):
        self.calls.append(('fwd', from_chat_id))
        return True

    async def get_users(self, ids):
        return []


def test_text_post_single_message(monkeypatch, tmp_path):
    monkeypatch.setattr(user, 'client', CountClient())
    monkeypatch.chdir(tmp_path)
    c = CountClient()
    monkeypatch.setattr(user, 'client', c)
    assert run(user._deliver(-10, 'hello world')) == (True, '')
    assert len(c.calls) == 1


def test_photo_text_single_captioned_message(monkeypatch, tmp_path):
    pic = tmp_path / 'p.jpg'
    pic.write_bytes(b'x')
    c = CountClient()
    monkeypatch.setattr(user, 'client', c)
    assert run(user._deliver(-10, 'hello', photo_path=str(pic))) == (True, '')
    assert len(c.calls) == 1
    assert c.calls[0][0] == 'photo' and 'hello' in (c.calls[0][1] or '')


def test_forward_text_is_two_by_design(monkeypatch):
    # форвард нельзя подписать — это ограничение Telegram, а не баг
    c = CountClient()
    monkeypatch.setattr(user, 'client', c)
    assert run(user._deliver(-10, 'comment', fwd=(-1001, 55))) == (True, '')
    assert [k for k, *_ in c.calls] == ['fwd', 'msg']


def test_forward_alone_single(monkeypatch):
    c = CountClient()
    monkeypatch.setattr(user, 'client', c)
    assert run(user._deliver(-10, '', fwd=(-1001, 55))) == (True, '')
    assert len(c.calls) == 1


def test_log_text_single(monkeypatch):
    c = CountClient()
    monkeypatch.setattr(user, 'client', c)

    class DB:
        def get_log_config(self):
            return 1, '-100999'

    run(user._log_send(DB(), {'id': -1, 'title': 'C'}, 'hello'))
    assert len(c.calls) == 1
    assert '#log' in c.calls[0][1] and '<blockquote>' in c.calls[0][1]


def test_log_media_short_merged_single(monkeypatch, tmp_path):
    pic = tmp_path / 'p.jpg'
    pic.write_bytes(b'x')
    c = CountClient()
    monkeypatch.setattr(user, 'client', c)

    class DB:
        def get_log_config(self):
            return 1, '-100999'

    run(user._log_send(DB(), {'id': -1, 'title': 'C'}, 'hi', photo_path=str(pic)))
    assert len(c.calls) == 1  # подпись + цитата влезли — одним сообщением
    assert c.calls[0][0] == 'photo'
    assert '#log' in (c.calls[0][1] or '') and '<blockquote>' in (c.calls[0][1] or '')


def test_log_media_long_stays_two(monkeypatch, tmp_path):
    pic = tmp_path / 'p.jpg'
    pic.write_bytes(b'x')
    c = CountClient()
    monkeypatch.setattr(user, 'client', c)

    class DB:
        def get_log_config(self):
            return 1, '-100999'

    run(user._log_send(DB(), {'id': -1, 'title': 'C'}, 'x' * 2000, photo_path=str(pic)))
    assert len(c.calls) == 2  # не влезло в подпись — медиа + цитата
