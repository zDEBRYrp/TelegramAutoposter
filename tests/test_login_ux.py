"""Код входа — только кнопками; диагностика молчаливых сбоев на месте."""
import inspect

import main


def test_code_prompt_warns_buttons_only():
    assert 'кнопкам' in main.CODE_PROMPT
    assert 'не отправляйте' in main.CODE_PROMPT.lower()


def test_code_text_handler_rejects_typed_code():
    src = inspect.getsource(main.handle_code_text)
    assert 'do_sign_in' not in src  # сообщением код не принимаем
    assert 'delete' in src  # сообщение с кодом удаляем из чата


def test_error_handler_registered():
    assert len(main.dp.error.handlers) >= 1


def test_main_handles_polling_conflict():
    src = inspect.getsource(main.main)
    assert 'TelegramConflictError' in src


def test_startup_banner_logged():
    src = inspect.getsource(main.main)
    assert 'стартует' in src


def test_deny_logs_user_id():
    src = inspect.getsource(main._deny_if_not_admin)
    assert 'Отказ в доступе' in src
    assert 'uid=' in src
