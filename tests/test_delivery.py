"""Доставка: статусы, plain-фолбэк, фатальные ошибки, отчёты."""
import asyncio

import pytest

import user
import main


@pytest.fixture(autouse=True)
def clean_status():
    user.send_status.clear()
    yield
    user.send_status.clear()


class StubClient:
    """send_*: fail_html раз — кинуть parse-ошибку при parse_mode, иначе ок."""

    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail

    async def send_message(self, chat_id, text, parse_mode=None):
        self.calls.append(('msg', text, parse_mode))
        if callable(self.fail):
            err = self.fail(parse_mode)
            if err is not None:
                raise err
        elif isinstance(self.fail, Exception):
            raise self.fail
        return True

    async def send_photo(self, chat_id, photo, caption=None, parse_mode=None):
        self.calls.append(('photo', caption, parse_mode))
        if isinstance(self.fail, Exception):
            raise self.fail
        return True

    async def send_video(self, chat_id, video, caption=None, parse_mode=None):
        self.calls.append(('video', caption, parse_mode))
        if isinstance(self.fail, Exception):
            raise self.fail
        return True


def parse_failer(parse_mode):
    if parse_mode is not None:
        return Exception("can't parse entities: ...")
    return None


def run(coro):
    return asyncio.run(coro)


def test_strip_html():
    assert user.strip_html_tags('<b>a</b> &lt;x&gt;') == 'a <x>'
    assert user.strip_html_tags('plain') == 'plain'


def test_is_parse_error():
    assert user._is_parse_error(Exception("can't parse entities: foo"))
    assert user._is_parse_error(Exception('MESSAGE_EMPTY'))
    assert not user._is_parse_error(ValueError('nope'))


def test_is_fatal():
    assert user._is_fatal_error_msg('PEER_ID_INVALID')
    assert user._is_fatal_error_msg('USER_BANNED_IN_CHANNEL test')
    assert not user._is_fatal_error_msg('FloodWait 30')
    assert not user._is_fatal_error_msg('')


class StubDB:
    def __init__(self):
        self.stopped = []

    def stop_spam_for_channel(self, cid):
        self.stopped.append(cid)
        return True


def test_register_ok_keeps_chat():
    db = StubDB()
    assert user.register_send_result(db, -1, True, '') is False
    assert db.stopped == []
    assert user.send_status[-1]['ok'] is True


def test_register_fatal_disables_chat():
    db = StubDB()
    assert user.register_send_result(db, -2, False, 'PEER_ID_INVALID') is True
    assert db.stopped == [-2]
    assert user.send_status[-2]['disabled'] is True


def test_register_transient_keeps_chat():
    db = StubDB()
    assert user.register_send_result(db, -3, False, 'Timeout blah') is False
    assert db.stopped == []


def test_deliver_success(monkeypatch):
    monkeypatch.setattr(user, 'client', StubClient())
    assert run(user._deliver(-1, '<b>hi</b>')) == (True, '')


def test_deliver_empty():
    assert run(user._deliver(-1, '', None, None)) == (True, '')


def test_deliver_parse_fallback_to_plain(monkeypatch):
    stub = StubClient(fail=parse_failer)
    monkeypatch.setattr(user, 'client', stub)
    ok, err = run(user._deliver(-1, '<b>hi</b>'))
    assert (ok, err) == (True, '')
    assert stub.calls[0][2] is not None  # первая — с HTML
    assert stub.calls[-1][2] is None  # повтор — plain
    assert stub.calls[-1][1] == 'hi'


def test_deliver_hard_error(monkeypatch):
    monkeypatch.setattr(user, 'client', StubClient(fail=Exception('PEER_ID_INVALID')))
    ok, err = run(user._deliver(-1, 'hi'))
    assert ok is False and 'PEER_ID_INVALID' in err


def test_deliver_flood_reraises(monkeypatch):
    fw = user.FloodWait.__new__(user.FloodWait)
    fw.value = 5
    monkeypatch.setattr(user, 'client', StubClient(fail=fw))
    with pytest.raises(user.FloodWait):
        run(user._deliver(-1, 'hi'))


def test_format_send_report_mixed():
    out = main._format_send_report({-1: {'ok': True, 'error': '', 'at': 0},
                                    -2: {'ok': False, 'error': 'BOOM <x>', 'at': 0}})
    assert '✅ -1' in out and '❌ -2' in out
    assert '<x>' not in out  # экранировано


def test_format_send_report_empty():
    assert '⏳' in main._format_send_report({})


def test_send_state_line():
    assert 'не было' in main._send_state_line(-999)
    user.send_status[-1] = {'ok': True, 'error': '', 'at': 0}
    assert '✅' in main._send_state_line(-1)
    user.send_status[-2] = {'ok': False, 'error': 'FLOOD', 'at': 0}
    assert '❌' in main._send_state_line(-2)
