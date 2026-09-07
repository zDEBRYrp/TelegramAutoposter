"""Чистый чат: правим прошлое сообщение, у каждого итога есть кнопка назад."""
import asyncio
from types import SimpleNamespace

from aiogram.exceptions import TelegramBadRequest

import main


class StubBot:
    def __init__(self, fail_edit=False, not_modified=False):
        self.edited = []
        self.sent = []
        self.fail_edit = fail_edit
        self.not_modified = not_modified

    async def edit_message_text(self, text, chat_id, msg_id, reply_markup=None, parse_mode=None):
        if self.not_modified:
            raise TelegramBadRequest(method=None, message='Bad Request: message is not modified')
        if self.fail_edit:
            raise RuntimeError('deleted')
        self.edited.append((chat_id, msg_id, text, reply_markup))

    async def edit_message_reply_markup(self, chat_id, msg_id, reply_markup=None):
        self.edited.append((chat_id, msg_id, 'MARKUP', reply_markup))

    async def send_message(self, chat_id, text, reply_markup=None, parse_mode=None):
        self.sent.append((chat_id, text, reply_markup))
        return SimpleNamespace(message_id=777)


def run(coro):
    return asyncio.run(coro)


def test_edit_or_send_edits_prompt(monkeypatch):
    stub = StubBot()
    monkeypatch.setattr(main, 'bot', stub)
    mid = run(main._edit_or_send(-1, 10, 'ok', main.back_to_chats_kb()))
    assert mid == 10
    assert len(stub.edited) == 1 and not stub.sent


def test_edit_or_send_sends_when_no_prompt(monkeypatch):
    stub = StubBot()
    monkeypatch.setattr(main, 'bot', stub)
    mid = run(main._edit_or_send(-1, None, 'ok'))
    assert mid == 777
    assert len(stub.sent) == 1 and not stub.edited


def test_edit_or_send_falls_back_to_send(monkeypatch):
    stub = StubBot(fail_edit=True)
    monkeypatch.setattr(main, 'bot', stub)
    mid = run(main._edit_or_send(-1, 10, 'ok'))
    assert mid == 777
    assert len(stub.sent) == 1


def test_edit_or_send_not_modified_no_duplicate(monkeypatch):
    stub = StubBot(not_modified=True)
    monkeypatch.setattr(main, 'bot', stub)
    mid = run(main._edit_or_send(-1, 10, 'same'))
    assert mid == 10
    assert not stub.sent  # дубликат не шлём


def test_back_keyboards_point_right():
    assert main.back_to_chats_kb().inline_keyboard[0][0].callback_data == 'BACK_TO_CHATS'
    assert main.back_to_chat_kb(-5).inline_keyboard[0][0].callback_data == 'EDIT_CHAT:-5'
    assert main.back_to_channel_post_kb(-5).inline_keyboard[0][0].callback_data == 'EDIT_CHANNEL_POST:-5'
    assert main.back_to_global_kb().inline_keyboard[0][0].callback_data == 'BACK_TO_GLOBAL'


def test_back_to_global_branch_exists():
    import inspect
    assert "data == 'BACK_TO_GLOBAL'" in inspect.getsource(main.callback_handler)


def test_global_card_builds():
    from sqliter import DBConnection
    db = DBConnection(db_path=':memory:')
    db.change_text('hello')
    old = main.db
    main.db = db
    try:
        text, kb = main.build_global_post_card()
    finally:
        main.db = old
    assert 'Глобальный пост' in text
    btns = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert 'VIEW_GLOBAL_POST' in btns


def test_input_prompts_store_prompt_id():
    import inspect
    src = inspect.getsource(main.callback_handler)
    for branch in ('EDIT_TEXT', 'EDIT_PHOTO', 'EDIT_VIDEO', 'INTERVAL',
                   'CHANGE_TIMEOUT', 'ADD_ADDITIONAL', 'CHANNEL_EDIT_TEXT',
                   'INPUT_CHAT_ID', 'INPUT_CHAT_USERNAME', 'INPUT_CHAT_LINK'):
        assert branch in src
    assert src.count("'prompt_id'") >= 8


def test_save_telegram_file_works(monkeypatch, tmp_path):
    import asyncio

    class B:
        async def download(self, file_id, destination=None):
            with open(destination, 'w') as f:
                f.write('x')

    monkeypatch.setattr(main, 'bot', B())
    monkeypatch.setattr(main, 'MEDIA_DIR', str(tmp_path))
    name = asyncio.run(main.save_telegram_file('abc123', 'global', '.jpg'))
    assert name.endswith('.jpg') and (tmp_path / name).exists()
