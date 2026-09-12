"""Цвета кнопок (Bot API 9.4 style) и disabled-индикаторы (10.3)."""
import asyncio
from unittest.mock import AsyncMock, patch

import main
from aiogram.enums import ButtonStyle
from sqliter import DBConnection


def test_toggle_style_semantics():
    assert main._toggle_style(True) == ButtonStyle.SUCCESS  # включено — зелёный
    assert main._toggle_style(False) == ButtonStyle.DANGER  # выключено — красный


def test_reply_keyboards_colored():
    start = [b for row in main.welcome_keyboard().keyboard for b in row
             if b.text == main.BTN_START][0]
    assert start.style == ButtonStyle.PRIMARY
    stop = [b for row in main.spam_running_keyboard().keyboard for b in row
            if b.text == main.BTN_STOP][0]
    assert stop.style == ButtonStyle.DANGER


def test_page_indicator_disabled():
    b = main._page_indicator('2/5')
    assert b.text == '2/5'
    assert b.disabled is not None  # серая некликаемая (10.3)


def _memdb():
    return DBConnection(db_path=':memory:')


def test_chat_list_toggle_colors():
    db = _memdb()
    old = main.db
    main.db = db
    try:
        db.add_channel(-1)
        db.c.execute('UPDATE CHANNELS SET SPAM_ENABLED = 1 WHERE CHANNEL = ?',
                     [str(-1)])
        db.conn.commit()
        db.add_channel(-2)
        with patch.object(main.user, 'get_chats',
                          new=AsyncMock(return_value=[{'id': -1, 'title': 'A'},
                                                     {'id': -2, 'title': 'B'}])):
            kb, _ = asyncio.run(main.get_chats_keyboard(0, 'all'))
        toggles = {b.callback_data: b for row in kb.inline_keyboard[1:]
                   for b in row if b.callback_data.startswith('TOGGLE_SPAM:')}
        assert toggles['TOGGLE_SPAM:-1:0:all'].style == ButtonStyle.SUCCESS
        assert toggles['TOGGLE_SPAM:-2:0:all'].style == ButtonStyle.DANGER
        add = [b for row in kb.inline_keyboard for b in row
               if b.callback_data == 'ADD_CHAT'][0]
        assert add.style == ButtonStyle.SUCCESS
    finally:
        main.db = old
        db.c.close()
        db.conn.close()


def test_destructive_buttons_red():
    db = _memdb()
    old = main.db
    main.db = db
    try:
        db.add_channel(-1)
        db.add_additional_text(-1, 'x')
        kb = main.get_chat_settings_keyboard(-1)
        by_cb = {b.callback_data: b for row in kb.inline_keyboard for b in row}
        assert by_cb['CLEAR_ADDITIONAL:-1'].style == ButtonStyle.DANGER
        text, card = main.build_settings_card()
        cbs = {b.callback_data: b for row in card.inline_keyboard for b in row}
        assert cbs['SET_ALL_OFF'].style == ButtonStyle.DANGER
        assert cbs['SET_ALL_ON'].style == ButtonStyle.SUCCESS
        gtext, gkb = main.build_global_post_card()
        gbtns = {b.callback_data: b for row in gkb.inline_keyboard for b in row}
        assert gbtns['VIEW_GLOBAL_POST'].style == ButtonStyle.PRIMARY
    finally:
        main.db = old
        db.c.close()
        db.conn.close()
