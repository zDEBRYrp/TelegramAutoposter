"""Тесты wiring бота: хендлеры зарегистрированы, старых багов нет."""
import pathlib

import main

ROOT = pathlib.Path(__file__).resolve().parent.parent
MAIN_SRC = (ROOT / 'main.py').read_text(encoding='utf-8')
UPDATER_SRC = (ROOT / 'updater.py').read_text(encoding='utf-8')


def _message_handler_names():
    return [h.callback.__name__ for h in main.router.message.handlers]


def test_password_input_handler_exists():
    assert 'handle_password_input' in _message_handler_names()


def test_global_media_handlers_exist():
    names = _message_handler_names()
    assert 'download_global_photo' in names
    assert 'download_global_video' in names


def test_channel_media_handlers_exist():
    names = _message_handler_names()
    assert 'input_channel_post_photo' in names
    assert 'input_channel_post_video' in names


def test_no_old_download_calls():
    # aiogram 3.x: у PhotoSize/Video нет .download(), только bot.download
    assert '.download()' not in MAIN_SRC


def test_fsinputfile_used_for_preview():
    assert 'FSInputFile' in MAIN_SRC


def test_admin_guard_present():
    assert '_deny_if_not_admin' in MAIN_SRC
    assert '_deny_callback_if_not_admin' in MAIN_SRC


def test_resolve_media_empty():
    assert main.resolve_media_path('') is None


def test_resolve_media_finds_photos_dir(tmp_path, monkeypatch):
    f = tmp_path / 'pic.jpg'
    f.write_bytes(b'x')
    monkeypatch.chdir(tmp_path)
    assert main.resolve_media_path('pic.jpg') is not None


def test_updater_covers_version_file():
    assert 'version.txt' in UPDATER_SRC
    assert '.bak' in UPDATER_SRC


def test_dead_code_removed():
    assert 'def post_settings_keyboard' not in MAIN_SRC
    assert 'class update_state' not in MAIN_SRC


def test_dialogs_pagination_exists():
    assert 'DIALOGS_PAGE:' in MAIN_SRC


def test_channel_posts_query_null_safe():
    assert 'COALESCE(POST_PHOTO' in MAIN_SRC
