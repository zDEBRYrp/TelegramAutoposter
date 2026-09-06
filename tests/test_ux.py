"""Тесты UX-хелперов: нарезка текста, короткие строки, колбэки с номером страницы."""
import main


def test_split_html_short():
    assert main.split_html('abc') == ['abc']


def test_split_html_long_splits_by_lines():
    long = '\n'.join(f'line{i}' for i in range(500))
    chunks = main.split_html(long, limit=100)
    assert len(chunks) > 1
    assert '\n'.join(chunks) == long


def test_short_truncates():
    assert main._short('a' * 100, 10).endswith('…')
    assert main._short('abc', 10) == 'abc'
    assert main._short('a\nb') == 'a b'


def test_toggle_spam_callback_has_page():
    src = open('main.py', encoding='utf-8').read()
    assert 'TOGGLE_SPAM:{chat["id"]}:{page}' in src or "TOGGLE_SPAM:{chat[\"id\"]}:{page}" in src


def test_help_and_cancel_registered():
    names = [h.callback.__name__ for h in main.router.message.handlers]
    assert 'help_command' in names
    assert 'cancel_command' in names


def test_clear_additional_callback_exists():
    src = open('main.py', encoding='utf-8').read()
    assert 'CLEAR_ADDITIONAL:' in src


def test_format_chat_info_escapes_html():
    from sqliter import DBConnection
    db = DBConnection(db_path=':memory:')
    db.add_additional_text(-1, '<script>')
    db.set_channel_post(-1, text='<b>x</b>')
    # подменяем глобальный db в main на тестовый
    old = main.db
    main.db = db
    try:
        info = main.format_chat_info(-1)
    finally:
        main.db = old
    assert '<script>' not in info
    assert '&lt;script&gt;' in info


def test_spam_running_keyboard_has_stop():
    kb = main.spam_running_keyboard()
    texts = [b.text for row in kb.keyboard for b in row]
    assert main.BTN_STOP in texts
    assert main.BTN_CHATS in texts
    assert main.BTN_CPOSTS in texts


def test_welcome_keyboard_has_channel_posts():
    kb = main.welcome_keyboard()
    texts = [b.text for row in kb.keyboard for b in row]
    for btn in (main.BTN_START, main.BTN_POST, main.BTN_CHATS,
                main.BTN_CPOSTS, main.BTN_INFO, main.BTN_UPDATE):
        assert btn in texts


def test_all_reply_buttons_have_handlers():
    import re
    src = open('main.py', encoding='utf-8').read()
    names = re.findall(r"^(BTN_\w+) = '.+'", src, flags=re.M)
    assert len(names) >= 9
    for name in names:
        # константа: определение + минимум одно использование
        assert src.count(name) >= 3, name
    # ключевые кнопки обязаны быть в хендлерах F.text
    for name in ('BTN_START', 'BTN_STOP', 'BTN_POST', 'BTN_CHATS', 'BTN_CPOSTS',
                 'BTN_INFO', 'BTN_UPDATE', 'BTN_HOME', 'BTN_CLIST', 'BTN_CADD'):
        assert f'F.text == {name}' in src, name


def test_no_old_plain_labels_as_handlers():
    import re
    src = open('main.py', encoding='utf-8').read()
    for old in ["F.text == 'Запустить спам'", 'F.text == \'Остановить спам\'',
                'F.text == \'Настройки чатов\'', 'F.text == \'Вернуться\'',
                'F.text == \'Пост\'', 'F.text == \'Информация\'']:
        assert old not in src, old


def test_chat_settings_keyboard_indicators():
    from sqliter import DBConnection
    db = DBConnection(db_path=':memory:')
    db.add_channel(-5)
    db.set_channel_timeout(-5, 9)
    old = main.db
    main.db = db
    try:
        kb = main.get_chat_settings_keyboard(-5)
    finally:
        main.db = old
    texts = [b.text for row in kb.inline_keyboard for b in row]
    assert any('9 мин' in t for t in texts)
    assert any('ВЫКЛ' in t for t in texts)
