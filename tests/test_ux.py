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
    assert 'Остановить спам' in texts
    assert 'Настройки чатов' in texts
