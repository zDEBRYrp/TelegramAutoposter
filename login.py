"""DEPRECATED: логика входа переехала в user.py + main.py.

Файл оставлен чтобы старые импорты (`import login`) не падали.
Новых функций здесь нет — используйте user.do_login / do_sign_in / do_check_password.
"""
import logging

logger = logging.getLogger(__name__)

# Совместимость со старым кодом: пустые заглушки
pending_login_chat = None
login_phone = None
login_code = None
login_client = None
login_bot = None


def init_login_bot(bot):
    """Deprecated: бот теперь инициализируется через user.init_bot()."""
    global login_bot
    login_bot = bot
    logger.warning("login.init_login_bot() deprecated, используйте user.init_bot()")


async def login_phone_step(chat_id):
    logger.warning("login.login_phone_step() deprecated, вход идёт через /login в main.py")
    if login_bot is not None:
        try:
            await login_bot.send_message(chat_id, "Вход выполняется через команду /login")
        except Exception:
            pass


async def login_code_step(phone):
    logger.warning("login.login_code_step() deprecated")


async def complete_login(phone, code):
    logger.warning("login.complete_login() deprecated, используйте user.do_sign_in()")
    return False
