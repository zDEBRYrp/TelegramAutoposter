from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram import Router, F
from aiogram.enums import ParseMode
from aiogram.enums import ButtonStyle
from aiogram.exceptions import TelegramBadRequest, TelegramConflictError, TelegramNetworkError
from aiogram.types import (
    Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, ErrorEvent, DisabledButton
)

import html
import asyncio
import logging
import os
import re
import time as _time

import config
import user
import updater

from sqliter import DBConnection, markdown_to_html, message_to_html

router = Router()
from aiogram.client.default import DefaultBotProperties
from aiogram.types import FSInputFile

CODE_PROMPT = ('Введите код из Telegram:\nКод: {code}\n\n'
               '⚠️ Код вводите <b>только кнопками ниже</b> — '
               'не отправляйте его сообщением в чат, такой код не приму.')

MARKDOWN_HINT = ('\n\nЛибо разметка текстом: **жирный**, *курсив*, ~~зачёрк~~, '
                 '||спойлер||, `код`, [текст](ссылка), > цитата\n'
                 'Либо форматируй прямо в приложении — подхвачу и его.') 

def get_version():
    try:
        with open("version.txt", "r") as f:
            return f.read().strip()
    except:
        return "unknown"


LOCK_FILE = 'bot.lock'
_lock_fh = None


def _lock_file(fh) -> None:
    """Неблокирующая эксклюзивная блокировка 1 байта (Windows/posix)."""
    if os.name == 'nt':
        import msvcrt
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(fh) -> None:
    if os.name == 'nt':
        import msvcrt
        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    else:
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _lock_owner_hint() -> str:
    # pid пишем со смещения 1: нулевой байт под локом читать нельзя
    # (Windows запрещает чтение заблокированного диапазона даже нам).
    try:
        with open(LOCK_FILE, 'rb') as f:
            f.seek(1)
            pid = f.read().decode('utf-8', 'ignore').replace('\x00', '').strip()
        if pid:
            return f' (pid {pid})'
    except OSError:
        pass
    return ''


def release_lock():
    global _lock_fh
    try:
        if _lock_fh is not None:
            try:
                _unlock_file(_lock_fh)
            except OSError:
                pass
            try:
                _lock_fh.close()
            except OSError:
                pass
    finally:
        _lock_fh = None


def acquire_lock() -> bool:
    """Только один экземпляр: два процесса делят обновления и команды молчат.

    Блокировка держится ОТКРЫТЫМ хендлом файла и умирает вместе с процессом —
    протухнуть не может (в отличие от проверки PID: в Windows PID
    переиспользуются и старт может блокироваться из-за чужого процесса).
    """
    global _lock_fh
    import atexit
    try:
        _lock_fh = open(LOCK_FILE, 'a+b')
    except OSError as e:
        logger.error(f'Не смог открыть lock-файл: {e}')
        return False
    try:
        _lock_file(_lock_fh)
    except OSError:
        try:
            _lock_fh.close()
        except OSError:
            pass
        _lock_fh = None
        logger.error(f'Бот уже запущен{_lock_owner_hint()}. Останови дубль '
                     f'(второе окно start.bat / процесс python main.py) — иначе '
                     f'обновления делят два процесса и команды молчат.')
        return False
    try:
        # pid — со смещения 1, нулевой байт занят локом (его нельзя читать)
        _lock_fh.seek(1)
        _lock_fh.truncate()
        _lock_fh.write(str(os.getpid()).encode('utf-8'))
        _lock_fh.flush()
    except OSError:
        pass
    atexit.register(release_lock)
    return True


def _is_quiet_error(exc: BaseException) -> bool:
    """Переходные сетевые сбои: aiogram сам ретраит, админа не спамим."""
    return isinstance(exc, (TelegramNetworkError, TelegramConflictError))

bot = Bot(token=config.TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
dp.include_router(router)
db = DBConnection()

user.init_bot(bot)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Гасим шум pyrogram (коннекты/пинги) — свои события логируем сами
for _noisy in ('pyrogram.connection', 'pyrogram.session', 'pyrogram.dispatcher'):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

# Активная задача спам-цикла (чтобы не плодить дубликаты)
spam_task: asyncio.Task | None = None

POLL_RETRY_SEC = 30  # пауза перед повтором поллинга при обрыве сети

# Итоги старта для отчёта админу
startup_info: dict = {'spam_was': False, 'resumed': 0, 'pyro_ok': False}
_wd_started = False


def _build_startup_report() -> str:
    lines = [f'🚀 <b>Бот запущен</b> · v{get_version()}']
    if startup_info.get('spam_was'):
        n = startup_info.get('resumed', 0)
        if n:
            lines.append(f'📝 Рассылка была ВКЛ → возобновлена ({n} чатов)')
        else:
            lines.append('📝 Рассылка была ВКЛ → сразу возобновить не вышло, '
                         'watchdog поднимет её сам')
    else:
        lines.append('📝 Рассылка была ВЫКЛ')
    if startup_info.get('pyro_ok'):
        lines.append('📡 Pyrogram: ✅ подключён')
    else:
        lines.append('📡 Pyrogram: ❌ нет — нужен /login или жду сеть')
    return '\n'.join(lines)


@dp.startup()
async def on_startup(bot: Bot):
    try:
        await bot.send_message(config.ADMINS[0], _build_startup_report())
    except Exception as e:
        logger.error(f'Не смог отправить стартовый отчёт: {e}')


def _watchdog_should_start(spam_flag: int, task) -> bool:
    return spam_flag == 1 and (task is None or task.done())


async def _spam_watchdog():
    """Раз в минуту проверяет: флаг ВКЛ, а цикл мёртв — поднимает заново.
    Так рассылка самовосстанавливается после обрывов сети/перезапусков."""
    while True:
        await asyncio.sleep(60)
        try:
            st = db.settings()
            flag = st[4] if st else 0
        except Exception as e:
            logger.error(f'Watchdog: не прочитал настройки: {e}')
            continue
        try:
            global spam_task
            if _watchdog_should_start(flag, spam_task):
                logger.info('Watchdog: перезапускаю цикл рассылки')
                await start_spam_loop()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f'Watchdog: {e}')


async def _run_polling():
    """Поллинг с ретраями: при обрыве сети ждём и пробуем снова,
    а не падаем с трейсбеком."""
    while True:
        try:
            await dp.start_polling(bot)
            return
        except TelegramConflictError:
            logger.error('Telegram Conflict: токен опрашивает ДРУГОЙ процесс. '
                         'Останови дубль.')
            raise
        except TelegramNetworkError as e:
            logger.error(f'Нет связи с Telegram ({e}). Повтор через {POLL_RETRY_SEC}с…')
            await asyncio.sleep(POLL_RETRY_SEC)

MEDIA_DIR = getattr(config, 'DIR', '') or ''
if MEDIA_DIR:
    os.makedirs(MEDIA_DIR, exist_ok=True)
else:
    os.makedirs('photos', exist_ok=True)


def is_admin(user_id: int) -> bool:
    return user_id in config.ADMINS


def resolve_media_path(stored: str) -> str | None:
    """Найти файл медиа на диске.

    Поддерживает старые записи без расширения (подбор) и новые
    с полным именем. Ищет в DIR, затем в photos/, затем в текущей папке.
    """
    if not stored:
        return None
    candidates_dirs = []
    if MEDIA_DIR:
        candidates_dirs.append(MEDIA_DIR)
    candidates_dirs += ['photos', '']
    # 1. точное совпадение
    for d in candidates_dirs:
        p = os.path.join(d, stored) if d else stored
        if os.path.isfile(p):
            return p
    # 2. старые записи без расширения — подбор
    photo_exts = ['.jpg', '.jpeg', '.png', '.webp']
    video_exts = ['.mp4', '.mov', '.webm']
    for d in candidates_dirs:
        base = os.path.join(d, stored) if d else stored
        for ext in photo_exts + video_exts:
            if os.path.isfile(base + ext):
                return base + ext
    return None


async def save_telegram_file(file_id: str, prefix: str, ext: str) -> str:
    """Скачать файл через Bot API в MEDIA_DIR (или photos/) и вернуть имя файла."""
    import time
    dest_dir = MEDIA_DIR or 'photos'
    os.makedirs(dest_dir, exist_ok=True)
    filename = f"{prefix}_{int(_time.time())}_{file_id[-8:]}{ext}"
    dest = os.path.join(dest_dir, filename)
    await bot.download(file_id, destination=dest)
    return filename


CAPTION_LIMIT = 950  # запас до лимита Telegram 1024
TEXT_LIMIT = 4000  # запас до лимита 4096


def split_html(text_html: str, limit: int = TEXT_LIMIT) -> list[str]:
    """Нарезать длинный HTML на куски, по возможности по переносам строк."""
    if len(text_html) <= limit:
        return [text_html]
    chunks, cur = [], ''
    for line in text_html.split('\n'):
        if len(cur) + len(line) + 1 > limit and cur:
            chunks.append(cur)
            cur = ''
        cur = f'{cur}\n{line}' if cur else line
    if cur:
        chunks.append(cur)
    return chunks or [text_html]


async def send_post_preview(chat_id: int, media_path: str | None, text_html: str, is_video: bool = False):
    """Предпросмотр поста: длинную подпись шлёт отдельным сообщением,
    чтобы не упереться в лимит caption 1024."""
    if media_path:
        media = FSInputFile(media_path)
        if text_html and len(text_html) > CAPTION_LIMIT:
            if is_video:
                await bot.send_video(chat_id, media)
            else:
                await bot.send_photo(chat_id, media)
            for chunk in split_html(text_html):
                await bot.send_message(chat_id, chunk, parse_mode=ParseMode.HTML)
        elif is_video:
            await bot.send_video(chat_id, media, caption=text_html or None, parse_mode=ParseMode.HTML)
        else:
            await bot.send_photo(chat_id, media, caption=text_html or None, parse_mode=ParseMode.HTML)
    elif text_html:
        for chunk in split_html(text_html):
            await bot.send_message(chat_id, chunk, parse_mode=ParseMode.HTML)


def _short(text: str, limit: int = 60) -> str:
    text = (text or '').replace('\n', ' ').strip()
    return text if len(text) <= limit else text[:limit - 1] + '…'


def _display(text: str, limit: int = 60) -> str:
    """Текст из БД для HTML-карточек: нормализует двойное экранирование
    (новые записи уже содержат &lt; из entities, старые — сырой текст)."""
    return html.escape(_short(html.unescape(text or ''), limit))


async def _clean_trigger(m: Message):
    """Удаляем сообщение пользователя (команда/кнопка/ввод) — чистый чат."""
    try:
        await m.delete()
    except Exception:
        pass


async def _prompt_id(state: FSMContext) -> int | None:
    try:
        return (await state.get_data()).get('prompt_id')
    except Exception:
        return None


async def _edit_or_send(chat_id: int, msg_id: int | None, text: str,
                        kb: InlineKeyboardMarkup | None = None) -> int | None:
    """Правило чистого чата: правим прошлое сообщение бота, новое шлём
    только если править нечего (удалено и т.п.). Возвращает id сообщения."""
    if msg_id:
        try:
            await bot.edit_message_text(text, chat_id, msg_id,
                                        reply_markup=kb, parse_mode=ParseMode.HTML)
            return msg_id
        except TelegramBadRequest as e:
            if 'message is not modified' in str(e).lower():
                try:
                    await bot.edit_message_reply_markup(chat_id, msg_id, reply_markup=kb)
                except Exception:
                    pass
                return msg_id
        except Exception:
            pass
    sent = await bot.send_message(chat_id, text, reply_markup=kb, parse_mode=ParseMode.HTML)
    return sent.message_id


async def _auto_delete(chat_id: int, msg_id: int, delay: int = 15):
    """Тихо удаляем временную подсказку через delay секунд."""
    try:
        await asyncio.sleep(delay)
        await bot.delete_message(chat_id, msg_id)
    except Exception:
        pass


def back_to_chats_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='⬅️ К чатам', callback_data='BACK_TO_CHATS')]])


def back_to_chat_kb(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='⬅️ К чату', callback_data=f'EDIT_CHAT:{chat_id}')]])


def back_to_channel_post_kb(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='⬅️ К посту', callback_data=f'EDIT_CHANNEL_POST:{chat_id}')]])


def back_to_global_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='⬅️ К посту', callback_data='BACK_TO_GLOBAL')]])


def back_to_settings_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='⬅️ К настройкам', callback_data='SETTINGS_BACK')]])


def build_settings_card() -> tuple[str, InlineKeyboardMarkup]:
    """Карточка ⚙️ Настройки: тумблеры + массовые действия."""
    try:
        log_on, log_chat = db.get_log_config()
    except Exception:
        log_on, log_chat = 0, ''
    try:
        report_on = db.get_report_enabled()
    except Exception:
        report_on = 1
    try:
        sync_on = db.get_sync_slowmode() == 1
    except Exception:
        sync_on = True
    try:
        all_chats = db.c.execute('SELECT COUNT(*), COALESCE(SUM(SPAM_ENABLED), 0) FROM CHANNELS').fetchone()
        total, active = (all_chats[0] or 0), (all_chats[1] or 0)
    except Exception:
        total, active = 0, 0

    log_target = _display(str(log_chat), 24) if log_chat else '— не выбран —'
    lines = [
        '<b>⚙️ Настройки</b>',
        f'📤 Лог отправок: {"✅ вкл" if log_on else "⬜ выкл"} → {log_target}',
        f'📊 Отчёт о старте: {"✅ вкл" if report_on else "⬜ выкл"}',
        f'🐢 КД впритык к слоумоду: {"✅ вкл" if sync_on else "⬜ выкл"}',
        f'💬 Чаты: {active} активно из {total}',
    ]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f'📤 Лог отправок: {"✅" if log_on else "⬜"}',
            callback_data='SET_TOGGLE_LOG',
            style=_toggle_style(bool(log_on)))],
        [InlineKeyboardButton(text=f'📋 Лог-чат: {log_target}', callback_data='SET_LOG_CHAT')],
        [InlineKeyboardButton(
            text=f'📊 Отчёт о старте: {"✅" if report_on else "⬜"}',
            callback_data='SET_TOGGLE_REPORT',
            style=_toggle_style(bool(report_on)))],
        [InlineKeyboardButton(
            text=f'🐢 КД впритык: {"✅" if sync_on else "⬜"}',
            callback_data='SET_TOGGLE_SYNC',
            style=_toggle_style(sync_on))],
        [InlineKeyboardButton(text='✅ Включить все', callback_data='SET_ALL_ON',
                              style=ButtonStyle.SUCCESS),
         InlineKeyboardButton(text='⛔ Выключить все', callback_data='SET_ALL_OFF',
                              style=ButtonStyle.DANGER)],
        [InlineKeyboardButton(text='♻️ Обновить список чатов', callback_data='SET_REFRESH_CHATS'),
         InlineKeyboardButton(text='🧹 Сбросить статусы', callback_data='SET_CLEAR_STATUS')],
        [InlineKeyboardButton(text='❌ Закрыть', callback_data='SETTINGS_CLOSE')],
    ])
    return '\n'.join(lines), kb


# --- Подписи reply-кнопок (единый источник правды: меню и хендлеры используют их) ---
BTN_START = '▶️ Запустить рассылку'
BTN_STOP = '⏹ Остановить рассылку'
BTN_POST = '📝 Общий пост'
BTN_CHATS = '💬 Чаты'
BTN_CPOSTS = '📢 Посты чатов'
BTN_SETTINGS = '⚙️ Настройки'
BTN_INFO = 'ℹ️ Инфо'
BTN_UPDATE = '🔄 Обновление'
BTN_HOME = '🏠 Главное меню'
BTN_CLIST = '📋 Список постов'
BTN_CADD = '➕ Новый пост'


def welcome_keyboard():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text=BTN_START, style=ButtonStyle.PRIMARY)],
        [KeyboardButton(text=BTN_POST), KeyboardButton(text=BTN_CHATS)],
        [KeyboardButton(text=BTN_CPOSTS), KeyboardButton(text=BTN_SETTINGS)],
        [KeyboardButton(text=BTN_INFO), KeyboardButton(text=BTN_UPDATE)]
    ], resize_keyboard=True)


def spam_running_keyboard():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text=BTN_STOP, style=ButtonStyle.DANGER)],
        [KeyboardButton(text=BTN_POST), KeyboardButton(text=BTN_CHATS)],
        [KeyboardButton(text=BTN_CPOSTS), KeyboardButton(text=BTN_SETTINGS)],
        [KeyboardButton(text=BTN_INFO)]
    ], resize_keyboard=True)


def _toggle_style(currently_on: bool):
    """Цвет кнопки-тоггла по действию: включает → зелёная, выключает → красная."""
    return ButtonStyle.SUCCESS if not currently_on else ButtonStyle.DANGER


def _page_indicator(text: str) -> InlineKeyboardButton:
    """Некликаемый серый индикатор страницы (Bot API 10.3 disabled)."""
    return InlineKeyboardButton(text=text, callback_data='PAGINATION', disabled=DisabledButton())


def _send_state_line(chat_id: int) -> str:
    """Строка последней отправки для карточек: ✅/❌ + время."""
    st = user.send_status.get(chat_id)
    if not st:
        return '📤 Отправок ещё не было'
    when = _time.strftime('%H:%M', _time.localtime(st.get('at', 0)))
    if st.get('ok'):
        return f'📤 Последняя: ✅ {when}'
    err = _short(st.get('error') or 'ошибка', 50)
    dis = ', чат выключен' if st.get('disabled') else ''
    return f'📤 Последняя: ❌ {when} ({html.escape(err)}{dis})'


def _format_send_report(results: dict) -> str:
    """Отчёт о первых отправках после старта рассылки."""
    lines = ['📡 <b>Первые отправки:</b>']
    pending = [cid for cid, st in results.items() if not st]
    for cid, st in sorted(results.items()):
        if not st:
            continue
        if st.get('ok'):
            lines.append(f'✅ {cid}')
        else:
            err = _short(st.get('error') or 'ошибка', 60)
            dis = ' → чат выключен' if st.get('disabled') else ''
            lines.append(f'❌ {cid} — {html.escape(err)}{dis}')
    if pending:
        lines.append(f'⏳ Ещё ждут: {len(pending)} (смотрятся в ℹ️ Инфо)')
    if len(lines) == 1:
        return '⏳ Первые отправки ещё идут — итог появится в ℹ️ Инфо.'
    return '\n'.join(lines)


async def _report_first_sends(chat_id: int, msg_id: int, ids: list, timeout: int = 120):
    """Ждём первые итоги отправки и правим стартовое сообщение отчётом."""
    deadline = _time.time() + timeout
    while _time.time() < deadline:
        if all(i in user.send_status for i in ids):
            break
        try:
            if db.settings()[4] != 1:
                break  # рассылку остановили — нечего ждать
        except Exception:
            pass
        await asyncio.sleep(5)
    try:
        await bot.edit_message_text(
            _format_send_report({i: user.send_status.get(i) for i in ids}),
            chat_id, msg_id, parse_mode=ParseMode.HTML)
    except Exception:
        pass


def channel_post_keyboard():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text=BTN_CLIST), KeyboardButton(text=BTN_CADD)],
        [KeyboardButton(text=BTN_HOME)]
    ], resize_keyboard=True)


def get_chat_settings_keyboard(chat_id):
    spam_status = db.get_channel_spam_status(chat_id)
    spam_text = '✅ Рассылка ВКЛ — выключить' if spam_status == 1 else '⬜ Рассылка ВЫКЛ — включить'
    try:
        timeout_val = db.get_channel_timeout(chat_id)
    except Exception:
        timeout_val = None
    try:
        topic_id, topic_name = db.get_topic(chat_id)
    except Exception:
        topic_id, topic_name = 0, ''
    topic_label = 'General' if not topic_id else (topic_name or f'#{topic_id}')
    try:
        slow, _ = db.get_slowmode(chat_id)
    except Exception:
        slow = 0
    rows = [
        [InlineKeyboardButton(text=spam_text, callback_data=f'TOGGLE_SPAM_SETTINGS:{chat_id}',
                              style=_toggle_style(spam_status == 1))],
        [InlineKeyboardButton(text='📝 Пост чата', callback_data=f'EDIT_CHANNEL_POST:{chat_id}')],
        [InlineKeyboardButton(text=f'🧵 Тема: {_short(topic_label, 20)}', callback_data=f'TOPIC:{chat_id}')],
    ]
    if slow:
        # Кнопка впритык — только где слоумод реально есть
        try:
            chat_sync = db.get_sync_slow(chat_id) == 1
        except Exception:
            chat_sync = True
        rows.append([InlineKeyboardButton(
            text=f'🐢 Впритык к КД ({slow}с): {"✅" if chat_sync else "⬜"}',
            callback_data=f'TOGGLE_SYNC:{chat_id}',
            style=_toggle_style(chat_sync))])
    rows += [
        [InlineKeyboardButton(text=f'⏱ Интервал: {timeout_val} мин.', callback_data=f'CHANGE_TIMEOUT:{chat_id}')],
        [InlineKeyboardButton(text='💬 Доп. текст', callback_data=f'ADD_ADDITIONAL:{chat_id}')],
    ]
    try:
        addit = db.get_additional_text(chat_id)
        if addit and addit[0]:
            rows.append([InlineKeyboardButton(text='🗑 Убрать доп. текст', callback_data=f'CLEAR_ADDITIONAL:{chat_id}',
                                              style=ButtonStyle.DANGER)])
    except Exception:
        pass
    rows.append([InlineKeyboardButton(text='⬅️ К чатам', callback_data='BACK_TO_CHATS')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _next_send_line(chat_id: int) -> str:
    """Когда чату слать следующий пост (из расписания в БД)."""
    try:
        nxt = db.get_send_next(chat_id)
    except Exception:
        return ''
    if not nxt:
        return '⏳ Следующая: скоро (по расписанию)'
    left = nxt - _time.time()
    if left <= 0:
        return '⏳ Следующая: вот-вот'
    mins = int(left // 60)
    when = _time.strftime('%H:%M', _time.localtime(nxt))
    if mins < 1:
        return f'⏳ Следующая: меньше минуты ({when})'
    if mins < 60:
        return f'⏳ Следующая: через ~{mins} мин ({when})'
    return f'⏳ Следующая: через ~{mins // 60} ч {mins % 60} мин ({when})'


def _fmt_delay(sec: int) -> str:
    try:
        sec = int(sec)
    except (TypeError, ValueError):
        return '—'
    if sec < 60:
        return f'{sec}с'
    mins, sec = divmod(sec, 60)
    if mins < 60:
        return f'{mins} мин'
    hours, mins = divmod(mins, 60)
    return f'{hours} ч {mins} мин' if mins else f'{hours} ч'


def format_chat_info(chat_id: int) -> str:
    """Красивая карточка чата для EDIT_CHAT / TOGGLE_SPAM_SETTINGS."""
    spam_status = db.get_channel_spam_status(chat_id)
    addit_val = ''
    try:
        addit = db.get_additional_text(chat_id)
        addit_val = addit[0] if addit and addit[0] else ''
    except Exception:
        pass
    addit_val = _display(addit_val, 80) if addit_val else '—'
    post_data = db.get_channel_post(chat_id)
    try:
        fwd_chat, fwd_msg = db.get_channel_forward(chat_id)
    except Exception:
        fwd_chat, fwd_msg = 0, 0
    has_fwd = bool(fwd_chat and fwd_msg)
    if post_data and (post_data[0] or post_data[1] or post_data[2]) or has_fwd:
        parts = []
        if post_data and post_data[0]:
            parts.append('📷 фото')
        if post_data and post_data[1]:
            parts.append('📹 видео')
        if has_fwd:
            parts.append(f'📨 пересылка из {fwd_chat}')
        if post_data and post_data[2]:
            parts.append(f'«{_display(post_data[2], 50)}»')
        post_desc = ' + '.join(parts)
    else:
        post_desc = '—'
    timeout_val = db.get_channel_timeout(chat_id)
    try:
        topic_id, topic_name = db.get_topic(chat_id)
    except Exception:
        topic_id, topic_name = 0, ''
    topic_desc = 'General' if not topic_id else html.escape(topic_name or f'#{topic_id}')
    try:
        slow, slow_at = db.get_slowmode(chat_id)
    except Exception:
        slow, slow_at = 0, 0.0
    try:
        sync_on = user._chat_sync_on(db, chat_id)
    except Exception:
        sync_on = True
    slow_line = ''
    if slow:
        try:
            eff = user._effective_delay(timeout_val, slow, sync_on)
            eff_txt = _fmt_delay(eff)
        except Exception:
            eff_txt = None
        checked = ''
        try:
            if slow_at:
                checked = f' (проверено {_time.strftime("%H:%M", _time.localtime(slow_at))})'
        except Exception:
            pass
        if sync_on and eff_txt:
            slow_line = (f'\n🐢 Слоумод: {slow}с{checked} → шлём каждые ~{eff_txt}, '
                         f'но не чаще минимума {timeout_val} мин.')
        else:
            slow_line = f'\n🐢 Слоумод: {slow}с{checked} (впритык выкл)'
    return (f'💬 <b>Чат {chat_id}</b>\n'
            f'{"✅ Рассылка включена" if spam_status == 1 else "⬜ Рассылка выключена"}\n'
            f'⏱ Интервал: {timeout_val} мин.{slow_line}\n'
            f'🧵 Тема: {topic_desc}\n'
            f'{_next_send_line(chat_id)}\n'
            f'💬 Доп. текст: {addit_val}\n'
            f'📝 Пост: {post_desc}\n'
            f'{_send_state_line(chat_id)}')


CHAT_FILTERS = ('all', 'on', 'off')
FILTER_NAMES = {'all': 'все', 'on': 'включённые', 'off': 'выключенные'}


def chats_header(total: int, active: int, filt: str) -> str:
    return (f'💬 <b>Чаты</b> — {FILTER_NAMES.get(filt, "все")} '
            f'(активно {active} из {total})\n'
            f'Нажми на чат чтобы вкл/выкл, ⚙️ — настройки')


async def get_chats_keyboard(page=0, filt='all'):
    if filt not in CHAT_FILTERS:
        filt = 'all'
    chats = await user.get_chats()
    per_page = 10

    # Статусы одним запросом + дозапись новых чатов пачкой (было N+1 запросов)
    rows = db.c.execute('SELECT CHANNEL, SPAM_ENABLED FROM CHANNELS').fetchall()
    statuses = {str(r[0]): (r[1] or 0) for r in rows}
    missing = [str(ch['id']) for ch in chats if str(ch['id']) not in statuses]
    if missing:
        db.c.executemany(
            'INSERT OR IGNORE INTO CHANNELS (CHANNEL, ADDITIONAL, SPAM_ENABLED, TIMEOUT) '
            'VALUES (?, ?, ?, ?)',
            [(cid, '', 0, 5) for cid in missing])
        db.conn.commit()
        for cid in missing:
            statuses[cid] = 0

    total = len(chats)
    active = sum(1 for ch in chats if statuses.get(str(ch['id']), 0) == 1)

    # Фильтр + сортировка: включённые всегда наверху
    if filt == 'on':
        chats = [ch for ch in chats if statuses.get(str(ch['id']), 0) == 1]
    elif filt == 'off':
        chats = [ch for ch in chats if statuses.get(str(ch['id']), 0) != 1]

    def sort_key(chat):
        return 0 if statuses.get(str(chat['id']), 0) == 1 else 1

    chats.sort(key=sort_key)

    total_pages = max(1, (len(chats) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    end = start + per_page
    page_chats = chats[start:end]

    keyboard = []
    # Вкладки фильтра со счётчиками
    off_count = total - active
    tabs = [
        InlineKeyboardButton(
            text=f'{"● " if filt == "all" else ""}📋 Все ({total})',
            callback_data='CHATS_FILTER:all'),
        InlineKeyboardButton(
            text=f'{"● " if filt == "on" else ""}✅ Вкл ({active})',
            callback_data='CHATS_FILTER:on'),
        InlineKeyboardButton(
            text=f'{"● " if filt == "off" else ""}⬜ Выкл ({off_count})',
            callback_data='CHATS_FILTER:off'),
    ]
    keyboard.append(tabs)
    for chat in page_chats:
        spam_status = statuses.get(str(chat['id']), 0)
        icon = '✅' if spam_status == 1 else '⬜'
        keyboard.append([
            InlineKeyboardButton(
                text=f'{icon} {chat["title"]}',
                callback_data=f'TOGGLE_SPAM:{chat["id"]}:{page}:{filt}',
                style=_toggle_style(spam_status == 1)
            ),
            InlineKeyboardButton(text='⚙️', callback_data=f'EDIT_CHAT:{chat["id"]}')
        ])

    if len(chats) > per_page:
        pagination = []
        if page > 0:
            pagination.append(InlineKeyboardButton(text='⬅️', callback_data=f'CHATS_PAGE:{page-1}:{filt}'))
        pagination.append(_page_indicator(f'{page+1}/{total_pages}'))
        if page < total_pages - 1:
            pagination.append(InlineKeyboardButton(text='➡️', callback_data=f'CHATS_PAGE:{page+1}:{filt}'))
        keyboard.append(pagination)

    keyboard.append([InlineKeyboardButton(text='➕ Добавить чат', callback_data='ADD_CHAT',
                                          style=ButtonStyle.SUCCESS)])
    return InlineKeyboardMarkup(inline_keyboard=keyboard), chats_header(total, active, filt)


class login_phone(StatesGroup):
    phone = State()

class login_code(StatesGroup):
    code = State()

class login_password(StatesGroup):
    password = State()

class addition(StatesGroup):
    id = State()

class post(StatesGroup):
    text = State()


class global_post_photo(StatesGroup):
    photo = State()


class global_post_video(StatesGroup):
    video = State()


class global_post_forward(StatesGroup):
    forward = State()

class channel_post_text(StatesGroup):
    text = State()

class channel_post_photo(StatesGroup):
    photo = State()

class channel_post_video(StatesGroup):
    video = State()


class channel_post_forward(StatesGroup):
    forward = State()

class global_time(StatesGroup):
    timeout = State()

class channel_time(StatesGroup):
    timeout = State()

class add_chat_state(StatesGroup):
    id = State()
    username = State()
    link = State()


class settings_state(StatesGroup):
    log_chat = State()


class topic_state(StatesGroup):
    id = State()


@router.message(Command("start"))
async def process_start_command(m: Message):
    if m.chat.type != 'private':
        return  # в группах бот-панель молчит
    if m.chat.id in config.ADMINS:
        await _clean_trigger(m)
        await bot.send_message(m.chat.id,
            f"<b>Добро пожаловать!</b>\n\nВерсия скрипта: {get_version()}\n\nВоспользуйтесь клавиатурой ниже для управления",
            reply_markup=welcome_keyboard())
    else:
        await bot.send_message(m.chat.id, "Нет доступа")

HELP_TEXT = (
    "<b>Что умеет бот:</b>\n"
    "• ▶️ Запустить рассылку — постить во все включённые чаты\n"
    "• 📝 Общий пост — текст + фото/видео для всех чатов\n"
    "• 💬 Чаты — вкл/выкл, свой интервал, свой пост и доп. текст\n"
    "• 📢 Посты чатов — у кого задан свой пост\n"
    "• ⚙️ Настройки — лог отправок, отчёты, вкл/выкл всех чатов\n"
    "• 🔄 Обновление — новая версия с GitHub\n\n"
    "<b>Команды:</b> /start /help /login /update /cancel"
)

@router.message(Command("help"))
async def help_command(m: Message):
    if m.chat.type != 'private':
        return
    if await _deny_if_not_admin(m):
        return
    await _clean_trigger(m)
    await m.answer(HELP_TEXT, reply_markup=welcome_keyboard())

@router.message(Command("cancel"))
async def cancel_command(m: Message, state: FSMContext):
    if await _deny_if_not_admin(m):
        return
    await _clean_trigger(m)
    await state.clear()
    await m.answer('Ввод отменён.', reply_markup=welcome_keyboard())

async def _deny_if_not_admin(message: Message) -> bool:
    """True если доступа нет. В не-приватных чатах молча игнорируем,
    чтобы бот не спамил 'Нет доступа' в группах."""
    uid = message.from_user.id if message.from_user else 0
    if message.chat.id in config.ADMINS or uid in config.ADMINS:
        return False
    logger.warning(f"Отказ в доступе: uid={uid} chat={message.chat.id} "
                   f"type={message.chat.type} text={_short(message.text or '', 40)!r}")
    if message.chat.type != 'private':
        return True
    await message.answer("Нет доступа")
    return True


async def _deny_callback_if_not_admin(c: CallbackQuery) -> bool:
    uid = c.from_user.id if c.from_user else 0
    if uid not in config.ADMINS and c.message.chat.id not in config.ADMINS:
        try:
            await c.answer()
        except Exception:
            pass
        return True
    return False

@router.message(Command("login"))
async def login_command(m: Message, state: FSMContext):
    if await _deny_if_not_admin(m):
        return
    await _clean_trigger(m)
    prompt_msg = await do_login(m.chat.id)
    if prompt_msg is not None:
        await state.update_data({'prompt_id': prompt_msg.message_id})
    await state.set_state(login_phone.phone)

@router.message(Command("update"))
async def update_command(m: Message):
    if await _deny_if_not_admin(m):
        return
    await _clean_trigger(m)
    await do_update_menu(m.chat.id)

@router.message(F.text == BTN_INFO)
async def send_info(message: Message):
    if await _deny_if_not_admin(message):
        return
    await _clean_trigger(message)
    version = get_version()
    latest = updater.get_latest_version()
    upd = '✅ актуально' if not latest or version == latest else f'🔄 доступна {latest}'
    settings = db.settings()
    spam = settings[4] if settings else 0
    try:
        all_chats = db.c.execute('SELECT COUNT(*), COALESCE(SUM(SPAM_ENABLED), 0) FROM CHANNELS').fetchone()
        total, active = (all_chats[0] or 0), (all_chats[1] or 0)
    except Exception:
        total, active = 0, 0
    send_lines = ''
    if user.send_status:
        rows = []
        for cid, st in sorted(user.send_status.items()):
            mark = '✅' if st.get('ok') else '❌'
            when = _time.strftime('%H:%M', _time.localtime(st.get('at', 0)))
            extra = '' if st.get('ok') else f' ({html.escape(_short(st.get("error") or "ошибка", 30))})'
            rows.append(f'{mark} {cid} · {when}{extra}')
        shown = rows[:8]
        send_lines = '\n📤 Отправки:\n' + '\n'.join(shown)
        if len(rows) > 8:
            send_lines += f'\n… и ещё {len(rows) - 8}'
    await message.answer(
        f'ℹ️ <b>Autoposter {version}</b>\n'
        f'Обновление: {upd}\n'
        f'📝 Рассылка: {"✅ запущена" if spam == 1 else "⬜ остановлена"}\n'
        f'💬 Чаты: {active} активно из {total}'
        f'{send_lines}\n\nSupport: @support')

@router.message(F.text == BTN_UPDATE)
async def update_btn(message: Message):
    if await _deny_if_not_admin(message):
        return
    await _clean_trigger(message)
    await do_update_menu(message.chat.id)

@router.message(F.text == BTN_HOME)
async def return_menu(message: Message):
    if await _deny_if_not_admin(message):
        return
    await _clean_trigger(message)
    await message.answer('🏠 Главное меню:', reply_markup=welcome_keyboard())

def build_global_post_card() -> tuple[str, InlineKeyboardMarkup]:
    settings = db.settings()
    # settings: [0]=ID, [1]=PHOTO, [2]=VIDEO, [3]=TEXT, [4]=SPAM, [5]=TIMEOUT,
    # [6]=FWD_CHAT, [7]=FWD_MSG
    photo = settings[1]
    video = settings[2]
    text = settings[3]
    spam = settings[4]
    timeout = settings[5]
    has_photo = bool(photo)
    has_video = bool(video)
    fwd = (0, 0)
    try:
        fwd = db.get_forward()
    except Exception:
        pass
    has_fwd = bool(fwd[0] and fwd[1])

    lines = ['<b>📝 Глобальный пост</b>']
    if has_photo:
        lines.append(f'📷 Фото: {html.escape(photo)}')
    if has_video:
        lines.append(f'📹 Видео: {html.escape(video)}')
    if has_fwd:
        lines.append(f'📨 Пересылка из {fwd[0]}')
    if text:
        lines.append(f'💬 Текст: «{_display(text, 120)}»')
    if not (has_photo or has_video or has_fwd or text):
        lines.append('❌ Пост пуст')
    lines.append(f'⏱ Интервал по умолчанию: {timeout} мин.')
    lines.append(f'{"✅ Рассылка запущена" if spam == 1 else "⬜ Рассылка остановлена"}')

    keyboard_rows = []
    if has_photo or has_video or has_fwd or text:
        keyboard_rows.append([InlineKeyboardButton(text='👁 Просмотреть пост', callback_data='VIEW_GLOBAL_POST',
                                                   style=ButtonStyle.PRIMARY)])
    keyboard_rows.append([InlineKeyboardButton(
        text=f'📝 Текст {"✅" if text else ""}', callback_data='EDIT_TEXT')])
    keyboard_rows.append([
        InlineKeyboardButton(text=f'📷 Фото {"✅" if has_photo else ""}', callback_data='EDIT_PHOTO'),
        InlineKeyboardButton(text=f'📹 Видео {"✅" if has_video else ""}', callback_data='EDIT_VIDEO')
    ])
    keyboard_rows.append([InlineKeyboardButton(
        text=f'📨 Пересылка {"✅" if has_fwd else ""}', callback_data='EDIT_FORWARD')])
    if has_photo or has_video or has_fwd:
        keyboard_rows.append([InlineKeyboardButton(text='🗑 Убрать медиа', callback_data='DEL_MEDIA',
                                                   style=ButtonStyle.DANGER)])
    keyboard_rows.append([InlineKeyboardButton(text=f'⏱ Интервал: {timeout} мин.', callback_data='INTERVAL')])

    return '\n'.join(lines), InlineKeyboardMarkup(inline_keyboard=keyboard_rows)


@router.message(F.text == BTN_POST)
async def post_settings(message: Message):
    if await _deny_if_not_admin(message):
        return
    await _clean_trigger(message)
    text, kb = build_global_post_card()
    await message.answer(text, reply_markup=kb)

@router.message(F.text == BTN_START)
async def start_spam_cmd(message: Message):
    if await _deny_if_not_admin(message):
        return
    await _clean_trigger(message)
    db.setSpam(1)
    try:
        enabled = await start_spam_loop()
    except Exception as e:
        logger.exception('start_spam_loop упал')
        db.setSpam(0)
        await message.answer(f'❌ Рассылка не запустилась: {e}',
                             reply_markup=welcome_keyboard())
        return
    if not enabled:
        return  # причина уже отправлена админу из start_spam_loop
    try:
        report_on = db.get_report_enabled() == 1
    except Exception:
        report_on = True
    sent = await message.answer(
        f'🚀 Рассылка запущена! Чатов: {enabled}.' +
        ('\n⏳ Проверяю первую отправку…' if report_on else ''),
        reply_markup=spam_running_keyboard())
    if not report_on:
        return
    try:
        ids = [ch['id'] for ch in await user.get_chats()
               if db.get_channel_spam_status(ch['id']) == 1]
    except Exception:
        ids = []
    asyncio.create_task(_report_first_sends(message.chat.id, sent.message_id, ids))

@router.message(F.text == BTN_STOP)
async def stop_spam_cmd(message: Message):
    if await _deny_if_not_admin(message):
        return
    await _clean_trigger(message)
    global spam_task
    db.setSpam(0)
    if spam_task and not spam_task.done():
        spam_task.cancel()
    await message.answer('⏹ Рассылка остановлена.', reply_markup=welcome_keyboard())

@router.message(F.text == BTN_CHATS)
async def chat_settings_menu(message: Message):
    if await _deny_if_not_admin(message):
        return
    await _clean_trigger(message)
    keyboard, header = await get_chats_keyboard(0)
    await message.answer(header, reply_markup=keyboard)

@router.message(F.text == BTN_CPOSTS)
async def channel_posts_menu(message: Message):
    if await _deny_if_not_admin(message):
        return
    await _clean_trigger(message)
    await message.answer('📢 <b>Посты чатов</b> — свой текст/медиа для отдельных чатов.\n'
                         'Либо: 💬 Чаты → ⚙️ → 📝 Пост чата.',
                         reply_markup=channel_post_keyboard())

@router.message(F.text == BTN_CADD)
async def add_channel_post_hint(message: Message):
    if await _deny_if_not_admin(message):
        return
    await _clean_trigger(message)
    await message.answer(
        'Индивидуальный пост задаётся для конкретного чата:\n'
        '💬 Чаты → ⚙️ → 📝 Пост чата → Текст/Фото/Видео.')

@router.message(F.text == BTN_SETTINGS)
async def settings_menu(message: Message):
    if await _deny_if_not_admin(message):
        return
    await _clean_trigger(message)
    text, kb = build_settings_card()
    await message.answer(text, reply_markup=kb)

@router.message(F.text == BTN_CLIST)
async def list_channel_posts(message: Message):
    if await _deny_if_not_admin(message):
        return
    await _clean_trigger(message)
    try:
        db.c.execute('SELECT CHANNEL, POST_PHOTO, POST_VIDEO, POST_TEXT, POST_FWD_CHAT, POST_FWD_MSG FROM CHANNELS '
                     'WHERE COALESCE(POST_PHOTO, "") != "" OR COALESCE(POST_VIDEO, "") != "" OR '
                     'COALESCE(POST_TEXT, "") != "" OR COALESCE(POST_FWD_CHAT, 0) != 0')
        posts = db.c.fetchall()
        if posts:
            text = '📋 <b>Посты чатов:</b>\n' + '\n'.join(
                f'• Чат {p[0]}: {"📷" if p[1] else ""}{"📹" if p[2] else ""}'
                f'{"📝" if p[3] else ""}{"📨" if (p[4] and p[5]) else ""}' for p in posts)
            await message.answer(text)
        else:
            await message.answer('Постов чатов пока нет. Нажми «➕ Новый пост» — подскажу где создать.')
    except Exception as e:
        await message.answer(f'Ошибка: {e}')


@router.callback_query(F.data.startswith('code_'))
async def handle_code_button(c: CallbackQuery, state: FSMContext):
    if await _deny_callback_if_not_admin(c):
        return
    code_part = c.data.split('_')[1]
    current_code_val = user.current_code.get(c.message.chat.id, "")

    if code_part == 'back':
        if current_code_val:
            user.current_code[c.message.chat.id] = current_code_val[:-1]
            await c.message.edit_text(
                CODE_PROMPT.format(code=user.current_code[c.message.chat.id]),
                reply_markup=c.message.reply_markup)
            await c.answer()
        else:
            await c.answer("Код пустой")
    elif code_part == 'enter':
        if current_code_val and user.login_phone:
            await c.message.edit_text(f'Код: {current_code_val}\n\nПроверяю...')
            result = await user.do_sign_in(user.login_phone, current_code_val)
            if result.get("ok"):
                await c.message.edit_text("Вход выполнен успешно!")
                user.current_code[c.message.chat.id] = ""
                await state.clear()
                await c.answer()
            elif result.get("need_password"):
                hint = result.get("hint", "")
                hint_text = f"\nПодсказка: {hint}" if hint else ""
                await c.message.edit_text(
                    f"Требуется пароль 2FA.{hint_text}\n\nВведите пароль:")
                await state.update_data({'pwd_prompt_id': c.message.message_id})
                await state.set_state(login_password.password)
                await c.answer()
            else:
                await c.answer(f"Ошибка: {result.get('error')}", show_alert=True)
                user.current_code[c.message.chat.id] = ""
                await c.message.edit_text(CODE_PROMPT.format(code=''),
                                          reply_markup=c.message.reply_markup)
        else:
            await c.answer("Введите код", show_alert=True)
    else:
        if len(current_code_val) < 6:
            user.current_code[c.message.chat.id] = current_code_val + code_part
            await c.message.edit_text(
                CODE_PROMPT.format(code=user.current_code[c.message.chat.id]),
                reply_markup=c.message.reply_markup)
            await c.answer()
        else:
            await c.answer("Максимум 6 цифр")


@router.callback_query(F.data == 'password_enter')
async def handle_password_confirm(c: CallbackQuery, state: FSMContext):
    if await _deny_callback_if_not_admin(c):
        return
    if user.login_password:
        result = await user.do_check_password(user.login_password)
        if result.get("ok"):
            await c.message.edit_text("✅ Вход выполнен успешно! /start — меню.")
            # чистим окно "требуется пароль"
            try:
                pwd_prompt = (await state.get_data()).get('pwd_prompt_id')
                if pwd_prompt and pwd_prompt != c.message.message_id:
                    await bot.delete_message(c.message.chat.id, pwd_prompt)
            except Exception:
                pass
            await state.clear()
        else:
            await c.answer(f"Ошибка: {result.get('error')}", show_alert=True)
    else:
        await c.answer("Сначала введите пароль", show_alert=True)


@router.callback_query(F.data == 'password_cancel')
async def handle_password_cancel(c: CallbackQuery, state: FSMContext):
    if await _deny_callback_if_not_admin(c):
        return
    user.login_password = None
    await state.clear()
    await c.message.edit_text("Вход отменен.")


@router.message(login_password.password)
async def handle_password_input(m: Message, state: FSMContext):
    if await _deny_if_not_admin(m):
        return
    password = (m.text or '').strip()
    await _clean_trigger(m)
    if not password:
        await m.answer('Пароль пустой. Введите пароль 2FA:')
        return
    user.login_password = password
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='✅ Подтвердить', callback_data='password_enter',
                              style=ButtonStyle.SUCCESS)],
        [InlineKeyboardButton(text='❌ Отмена', callback_data='password_cancel',
                              style=ButtonStyle.DANGER)],
    ])
    # убираем прошлое окно подтверждения, если пароль вводят повторно
    try:
        old_confirm = (await state.get_data()).get('pwd_confirm_id')
        if old_confirm:
            await bot.delete_message(m.chat.id, old_confirm)
    except Exception:
        pass
    sent = await m.answer('Пароль сохранён. Нажмите «Подтвердить» для входа:', reply_markup=keyboard)
    await state.update_data({'pwd_confirm_id': sent.message_id})


async def preview_forward(to_chat: int, fwd_chat: int, fwd_msg: int) -> tuple[bool, str]:
    """Показать пересылку админу: форвардим исходник ему в личку."""
    if not await user.ensure_connected():
        return False, 'Pyrogram не подключён'
    try:
        await user.client.forward_messages(to_chat, fwd_chat, [fwd_msg])
        return True, ''
    except Exception as e:
        return False, str(e)


async def _capture_forward(m: Message) -> tuple[bool, int, int, str]:
    """(ok, fwd_chat, fwd_msg, err) из пересланного сообщения."""
    fc = m.forward_from_chat
    fm = m.forward_from_message_id
    if fc is not None and fm:
        return True, fc.id, fm, ''
    if fc is not None:
        return False, 0, 0, 'у пересланного нет id сообщения (скрытый источник?)'
    return False, 0, 0, 'это не пересылка — перешли пост из канала'


async def _forward_source_title(fwd_chat: int) -> str:
    try:
        if await user.ensure_connected():
            ch = await user.client.get_chat(fwd_chat)
            return getattr(ch, 'title', None) or str(fwd_chat)
    except Exception:
        pass
    return str(fwd_chat)


@router.callback_query(F.data)
async def callback_handler(c: CallbackQuery, state: FSMContext):
    if await _deny_callback_if_not_admin(c):
        return
    data = c.data

    if data.startswith('CHATS_PAGE:'):
        sp = data.split(':')
        page = int(sp[1]) if len(sp) > 1 and sp[1].lstrip('-').isdigit() else 0
        filt = sp[2] if len(sp) > 2 and sp[2] in CHAT_FILTERS else 'all'
        keyboard, header = await get_chats_keyboard(page, filt)
        try:
            await c.message.edit_text(header, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        except Exception:
            try:
                await c.message.edit_reply_markup(reply_markup=keyboard)
            except Exception:
                pass
        await c.answer()

    elif data.startswith('CHATS_FILTER:'):
        filt = data.split(':')[1] if ':' in data else 'all'
        if filt not in CHAT_FILTERS:
            filt = 'all'
        keyboard, header = await get_chats_keyboard(0, filt)
        try:
            await c.message.edit_text(header, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        except Exception:
            try:
                await c.message.edit_reply_markup(reply_markup=keyboard)
            except Exception:
                pass
        await c.answer()

    elif data.startswith('TOGGLE_SPAM:'):
        parts = data.split(':')
        chat_id = int(parts[1])
        page = int(parts[2]) if len(parts) > 2 and parts[2].lstrip('-').isdigit() else 0
        filt = parts[3] if len(parts) > 3 and parts[3] in CHAT_FILTERS else 'all'
        current = db.get_channel_spam_status(chat_id)
        if current == 1:
            db.stop_spam_for_channel(chat_id)
            toast = '⬜ Рассылка выключена'
        else:
            db.c.execute('UPDATE CHANNELS SET SPAM_ENABLED = 1 WHERE CHANNEL = ?', [str(chat_id)])
            db.conn.commit()
            toast = '✅ Рассылка включена'
        keyboard, header = await get_chats_keyboard(page, filt)
        try:
            await c.message.edit_text(header, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        except Exception:
            try:
                await c.message.edit_reply_markup(reply_markup=keyboard)
            except Exception:
                pass
        await c.answer(toast)

    elif data.startswith('TOGGLE_SPAM_SETTINGS:'):
        chat_id = int(data.split(':')[1])
        current = db.get_channel_spam_status(chat_id)
        if current == 1:
            db.stop_spam_for_channel(chat_id)
        else:
            db.c.execute('UPDATE CHANNELS SET SPAM_ENABLED = 1 WHERE CHANNEL = ?', [str(chat_id)])
            db.conn.commit()
        await c.message.edit_text(format_chat_info(chat_id),
                                  reply_markup=get_chat_settings_keyboard(chat_id),
                                  parse_mode=ParseMode.HTML)
        await c.answer()

    elif data.startswith('TOGGLE_SYNC:'):
        chat_id = int(data.split(':')[1])
        try:
            cur = db.get_sync_slow(chat_id) == 1
        except Exception:
            cur = True
        db.set_sync_slow(chat_id, 0 if cur else 1)
        await c.message.edit_text(format_chat_info(chat_id),
                                  reply_markup=get_chat_settings_keyboard(chat_id),
                                  parse_mode=ParseMode.HTML)
        await c.answer('🐢 Впритык выключен' if cur else '🐢 Впритык включён')

    elif data.startswith('EDIT_CHAT:'):
        chat_id = int(data.split(':')[1])
        await c.message.edit_text(format_chat_info(chat_id),
                                  reply_markup=get_chat_settings_keyboard(chat_id),
                                  parse_mode=ParseMode.HTML)
        await c.answer()

    elif data == 'ADD_CHAT':
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='🔢 По ID', callback_data='INPUT_CHAT_ID')],
            [InlineKeyboardButton(text='📛 По username (@channel)', callback_data='INPUT_CHAT_USERNAME')],
            [InlineKeyboardButton(text='🔗 По ссылке (t.me/...)', callback_data='INPUT_CHAT_LINK')],
            [InlineKeyboardButton(text='📋 Из моих диалогов', callback_data='INPUT_CHAT_DIALOGS')],
            [InlineKeyboardButton(text='⬅️ Назад', callback_data='BACK_TO_CHATS')]
        ])
        await c.message.edit_text('Выберите способ добавления чата:', reply_markup=keyboard)
        await c.answer()

    elif data == 'INPUT_CHAT_ID':
        await state.set_data({'prompt_id': c.message.message_id})
        await c.message.edit_text('Отправьте ID чата (например: -1001234567890):')
        await state.set_state(add_chat_state.id)
        await c.answer()

    elif data == 'INPUT_CHAT_USERNAME':
        await state.set_data({'prompt_id': c.message.message_id})
        await c.message.edit_text('Отправьте username чата (например: @mychannel):')
        await state.set_state(add_chat_state.username)
        await c.answer()

    elif data == 'INPUT_CHAT_LINK':
        await state.set_data({'prompt_id': c.message.message_id})
        await c.message.edit_text('Отправьте ссылку на чат (например: https://t.me/mychannel):')
        await state.set_state(add_chat_state.link)
        await c.answer()

    elif data == 'INPUT_CHAT_DIALOGS' or data.startswith('DIALOGS_PAGE:'):
        page = int(data.split(':')[1]) if data.startswith('DIALOGS_PAGE:') else 0
        if not await user.ensure_connected():
            await c.answer('Pyrogram не подключен. Используй /login', show_alert=True)
            return
        chats = await user.get_chats()
        if not chats:
            await c.message.edit_text('Нет доступных диалогов.')
            await c.answer()
            return
        db_chats = db.c.execute('SELECT CHANNEL FROM CHANNELS').fetchall()
        db_ids = {str(row[0]) for row in db_chats}
        new_chats = [ch for ch in chats if str(ch['id']) not in db_ids]
        if not new_chats:
            await c.message.edit_text('Все доступные чаты уже добавлены.')
            await c.answer()
            return
        per_page = 10
        total_pages = (len(new_chats) + per_page - 1) // per_page
        page = max(0, min(page, total_pages - 1))
        rows = []
        for ch in new_chats[page * per_page:(page + 1) * per_page]:
            rows.append([InlineKeyboardButton(
                text=f'➕ {ch["title"]}',
                callback_data=f'ADD_CHAT_FROM_DIALOG:{ch["id"]}',
                style=ButtonStyle.SUCCESS
            )])
        if total_pages > 1:
            nav = []
            if page > 0:
                nav.append(InlineKeyboardButton(text='⬅️', callback_data=f'DIALOGS_PAGE:{page-1}'))
            nav.append(_page_indicator(f'{page+1}/{total_pages}'))
            if page < total_pages - 1:
                nav.append(InlineKeyboardButton(text='➡️', callback_data=f'DIALOGS_PAGE:{page+1}'))
            rows.append(nav)
        rows.append([InlineKeyboardButton(text='⬅️ Назад', callback_data='ADD_CHAT')])
        await c.message.edit_text('Выберите чат для добавления:', reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        await c.answer()

    elif data.startswith('ADD_CHAT_FROM_DIALOG:'):
        chat_id = int(data.split(':')[1])
        try:
            db.add_channel(chat_id)
            await c.message.edit_text(f'Чат {chat_id} успешно добавлен!')
            await c.answer()
        except Exception as e:
            await c.answer(f'Ошибка: {e}', show_alert=True)

    elif data == 'BACK_TO_CHATS':
        keyboard, header = await get_chats_keyboard(0)
        try:
            await c.message.edit_text(header, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        except Exception:
            try:
                await c.message.edit_reply_markup(reply_markup=keyboard)
            except Exception:
                pass
        await c.answer()

    elif data.startswith('CHANGE_TIMEOUT:'):
        chat_id = int(data.split(':')[1])
        await state.set_data({'chat_id': chat_id, 'prompt_id': c.message.message_id})
        cur = db.get_channel_timeout(chat_id)
        await c.message.edit_text(f'Текущий интервал чата: {cur} мин.\nВведите новый интервал (в минутах):')
        await state.set_state(channel_time.timeout)
        await c.answer()

    elif data.startswith('ADD_ADDITIONAL:'):
        chat_id = int(data.split(':')[1])
        await state.set_data({'chat_id': chat_id, 'prompt_id': c.message.message_id})
        await c.message.edit_text('Введите дополнительный текст для чата:' + MARKDOWN_HINT)
        await state.set_state(addition.id)
        await c.answer()

    elif data.startswith('TOPIC:'):
        chat_id = int(data.split(':')[1])
        await state.set_data({'chat_id': chat_id, 'prompt_id': c.message.message_id})
        try:
            topics = await user.get_forum_topics(chat_id)
        except Exception as e:
            await c.message.edit_text(
                f'🧵 Темы не читаются: {html.escape(str(e))}\n'
                f'Если это не форум-группа — темы не нужны, шлём в общую ленту.',
                reply_markup=back_to_chat_kb(chat_id))
            await c.answer()
            return
        rows = [[InlineKeyboardButton(text='💬 General (общая лента)',
                                      callback_data=f'TOPIC_SET:{chat_id}:0')]]
        for t in topics[:20]:
            label = _short(t.get('title') or f"#{t['id']}", 30)
            rows.append([InlineKeyboardButton(
                text=f'🧵 {label}', callback_data=f"TOPIC_SET:{chat_id}:{t['id']}")])
        rows.append([InlineKeyboardButton(text='🔢 Ввести ID темы', callback_data=f'TOPIC_MANUAL:{chat_id}')])
        rows.append([InlineKeyboardButton(text='⬅️ К чату', callback_data=f'EDIT_CHAT:{chat_id}')])
        await c.message.edit_text(
            '🧵 <b>Выбери тему форума</b> — посты пойдут ответом в неё:' if topics
            else '🧵 Это не форум (тем нет) — шлём в общую ленту.\nМожешь задать ID вручную:',
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        await c.answer()

    elif data.startswith('TOPIC_SET:'):
        _, chat_s, topic_s = data.split(':')
        chat_id, topic_id = int(chat_s), int(topic_s)
        name = ''
        if topic_id:
            try:
                for t in await user.get_forum_topics(chat_id):
                    if int(t['id']) == topic_id:
                        name = t.get('title') or ''
                        break
            except Exception:
                pass
        else:
            name = 'General'
        db.set_topic(chat_id, topic_id, name)
        await c.message.edit_text(format_chat_info(chat_id),
                                  reply_markup=get_chat_settings_keyboard(chat_id),
                                  parse_mode=ParseMode.HTML)
        await c.answer(f'🧵 Тема: {name or "General"}')

    elif data.startswith('TOPIC_MANUAL:'):
        chat_id = int(data.split(':')[1])
        await state.set_data({'chat_id': chat_id, 'prompt_id': c.message.message_id})
        await c.message.edit_text('Отправь ID темы (число из ссылки на тему) '
                                  'или 0 / General для общей ленты:')
        await state.set_state(topic_state.id)
        await c.answer()

    elif data.startswith('CLEAR_ADDITIONAL:'):
        chat_id = int(data.split(':')[1])
        db.add_additional_text(chat_id, '')
        await c.message.edit_text(format_chat_info(chat_id),
                                  reply_markup=get_chat_settings_keyboard(chat_id),
                                  parse_mode=ParseMode.HTML)
        await c.answer('Доп. текст убран')

    elif data.startswith('EDIT_CHANNEL_POST:'):
        chat_id = int(data.split(':')[1])
        await state.set_data({'chat_id': chat_id})
        post_data = db.get_channel_post(chat_id)
        has_photo = bool(post_data and post_data[0])
        has_video = bool(post_data and post_data[1])
        has_text = bool(post_data and post_data[2])
        try:
            _fc, _fm = db.get_channel_forward(chat_id)
            has_fwd = bool(_fc and _fm)
        except Exception:
            has_fwd = False
        has_post = has_photo or has_video or has_text or has_fwd
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='👁 Посмотреть пост', callback_data=f'VIEW_CHANNEL_POST:{chat_id}',
                                  style=ButtonStyle.PRIMARY)] if has_post else [],
            [InlineKeyboardButton(text=f'📝 Текст {"✅" if has_text else ""}', callback_data=f'CHANNEL_EDIT_TEXT:{chat_id}')],
            [InlineKeyboardButton(text=f'📷 Фото {"✅" if has_photo else ""}', callback_data=f'CHANNEL_EDIT_PHOTO:{chat_id}'),
             InlineKeyboardButton(text=f'📹 Видео {"✅" if has_video else ""}', callback_data=f'CHANNEL_EDIT_VIDEO:{chat_id}')],
            [InlineKeyboardButton(text=f'📨 Пересылка {"✅" if has_fwd else ""}', callback_data=f'CHANNEL_EDIT_FORWARD:{chat_id}')],
            [InlineKeyboardButton(text='🗑 Очистить пост', callback_data=f'CHANNEL_CLEAR:{chat_id}',
                                  style=ButtonStyle.DANGER)] if has_post else [],
            [InlineKeyboardButton(text='⬅️ К чату', callback_data=f'EDIT_CHAT:{chat_id}')]
        ])
        # убираем пустые строки
        keyboard.inline_keyboard = [row for row in keyboard.inline_keyboard if row]
        status = '✅ Пост установлен' if has_post else '❌ Пост не установлен'
        await c.message.edit_text(f'📝 Пост чата {chat_id}\n{status}:', reply_markup=keyboard)
        await c.answer()

    elif data.startswith('VIEW_CHANNEL_POST:'):
        chat_id = int(data.split(':')[1])
        post_data = db.get_channel_post(chat_id)
        try:
            fwd_chat, fwd_msg = db.get_channel_forward(chat_id)
        except Exception:
            fwd_chat, fwd_msg = 0, 0
        has_fwd = bool(fwd_chat and fwd_msg)
        if not post_data or not (post_data[0] or post_data[1] or post_data[2] or has_fwd):
            await c.answer('Пост не установлен', show_alert=True)
            return
        photo, video, text = post_data
        text_html = markdown_to_html(text) if text else ''
        try:
            if has_fwd:
                ok, err = await preview_forward(c.message.chat.id, fwd_chat, fwd_msg)
                if not ok:
                    await bot.send_message(
                        c.message.chat.id,
                        f'📨 Пересылка {fwd_chat}:{fwd_msg} (показать не вышло: {html.escape(err)})'
                        + (f'\n{text_html}' if text_html else ''),
                        parse_mode=ParseMode.HTML)
                elif text_html:
                    for chunk in split_html(text_html):
                        await bot.send_message(c.message.chat.id, chunk, parse_mode=ParseMode.HTML)
            elif photo:
                found = resolve_media_path(photo)
                if found:
                    await send_post_preview(c.message.chat.id, found, text_html)
                else:
                    await bot.send_message(c.message.chat.id, f'Фото не найдено на диске.\n{text_html}', parse_mode=ParseMode.HTML)
            elif video:
                found = resolve_media_path(video)
                if found:
                    await send_post_preview(c.message.chat.id, found, text_html, is_video=True)
                else:
                    await bot.send_message(c.message.chat.id, f'Видео не найдено на диске.\n{text_html}', parse_mode=ParseMode.HTML)
            elif text_html:
                for chunk in split_html(text_html):
                    await bot.send_message(c.message.chat.id, chunk, parse_mode=ParseMode.HTML)
        except Exception as e:
            await c.answer(f'Ошибка просмотра: {e}', show_alert=True)
            return
        await c.answer()

    elif data.startswith('CHANNEL_EDIT_TEXT:'):
        chat_id = int(data.split(':')[1])
        await state.set_data({'chat_id': chat_id, 'prompt_id': c.message.message_id})
        await c.message.edit_text('Введите текст поста чата:' + MARKDOWN_HINT)
        await state.set_state(channel_post_text.text)
        await c.answer()

    elif data.startswith('CHANNEL_EDIT_PHOTO:'):
        chat_id = int(data.split(':')[1])
        await state.set_data({'chat_id': chat_id, 'prompt_id': c.message.message_id})
        await c.message.edit_text('Отправь фото для поста чата:')
        await state.set_state(channel_post_photo.photo)
        await c.answer()

    elif data.startswith('CHANNEL_EDIT_VIDEO:'):
        chat_id = int(data.split(':')[1])
        await state.set_data({'chat_id': chat_id, 'prompt_id': c.message.message_id})
        await c.message.edit_text('Отправь видео для поста чата:')
        await state.set_state(channel_post_video.video)
        await c.answer()

    elif data.startswith('CHANNEL_EDIT_FORWARD:'):
        chat_id = int(data.split(':')[1])
        await state.set_data({'chat_id': chat_id, 'prompt_id': c.message.message_id})
        await c.message.edit_text('📨 Перешли сюда пост из канала — этот чат будет получать его как есть.')
        await state.set_state(channel_post_forward.forward)
        await c.answer()

    elif data.startswith('CHANNEL_CLEAR:'):
        chat_id = int(data.split(':')[1])
        db.clear_channel_post(chat_id)
        await c.message.edit_text(f'🗑 Пост чата {chat_id} очищен.',
                                  reply_markup=back_to_channel_post_kb(chat_id))
        await c.answer()

    elif data == 'EDIT_TEXT':
        await state.set_data({'prompt_id': c.message.message_id})
        await c.message.edit_text('Введите текст глобального поста:' + MARKDOWN_HINT)
        await state.set_state(post.text)
        await c.answer()

    elif data == 'VIEW_GLOBAL_POST':
        settings = db.settings()
        # settings: [0]=ID, [1]=PHOTO, [2]=VIDEO, [3]=TEXT, [4]=SPAM, [5]=TIMEOUT,
        # [6]=FWD_CHAT, [7]=FWD_MSG
        photo = settings[1]
        video = settings[2]
        text = settings[3]
        fwd = (0, 0)
        try:
            fwd = db.get_forward()
            has_fwd = bool(fwd[0] and fwd[1])
        except Exception:
            has_fwd = False
        text_html = markdown_to_html(text) if text else ''
        try:
            if has_fwd:
                ok, err = await preview_forward(c.message.chat.id, fwd[0], fwd[1])
                if not ok:
                    await bot.send_message(
                        c.message.chat.id,
                        f'📨 Пересылка {fwd[0]}:{fwd[1]} (показать не вышло: {html.escape(err)})'
                        + (f'\n{text_html}' if text_html else ''),
                        parse_mode=ParseMode.HTML)
                elif text_html:
                    for chunk in split_html(text_html):
                        await bot.send_message(c.message.chat.id, chunk, parse_mode=ParseMode.HTML)
            elif photo:
                found = resolve_media_path(photo)
                if found:
                    await send_post_preview(c.message.chat.id, found, text_html)
                else:
                    await bot.send_message(c.message.chat.id, f'Файл не найден.\n{text_html}', parse_mode=ParseMode.HTML)
            elif video:
                found = resolve_media_path(video)
                if found:
                    await send_post_preview(c.message.chat.id, found, text_html, is_video=True)
                else:
                    await bot.send_message(c.message.chat.id, f'Файл не найден.\n{text_html}', parse_mode=ParseMode.HTML)
            elif text_html:
                for chunk in split_html(text_html):
                    await bot.send_message(c.message.chat.id, chunk, parse_mode=ParseMode.HTML)
            else:
                await c.answer('Пост пуст', show_alert=True)
                return
        except Exception as e:
            await c.answer(f'Ошибка просмотра: {e}', show_alert=True)
            return
        await c.answer()

    elif data == 'EDIT_PHOTO':
        await state.set_data({'prompt_id': c.message.message_id})
        await c.message.edit_text('Отправь фото для глобального поста:')
        await state.set_state(global_post_photo.photo)
        await c.answer()

    elif data == 'EDIT_VIDEO':
        await state.set_data({'prompt_id': c.message.message_id})
        await c.message.edit_text('Отправь видео для глобального поста:')
        await state.set_state(global_post_video.video)
        await c.answer()

    elif data == 'EDIT_FORWARD':
        await state.set_data({'prompt_id': c.message.message_id})
        await c.message.edit_text('📨 Перешли сюда пост из канала — буду рассылать его как есть '
                                  '(форматирование и премиум-эмодзи сохранятся).')
        await state.set_state(global_post_forward.forward)
        await c.answer()

    elif data == 'DEL_MEDIA':
        db.clear_global_media()
        text, kb = build_global_post_card()
        try:
            await c.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
        except Exception:
            pass
        await c.answer('🗑 Медиа убрано')

    elif data == 'BACK_TO_GLOBAL':
        text, kb = build_global_post_card()
        try:
            await c.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
        except Exception:
            try:
                await c.message.edit_reply_markup(reply_markup=kb)
            except Exception:
                pass
        await c.answer()

    elif data == 'SET_TOGGLE_LOG':
        cur_on, _ = db.get_log_config()
        db.set_log_enabled(0 if cur_on else 1)
        text, kb = build_settings_card()
        try:
            await c.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
        except Exception:
            pass
        await c.answer('📤 Лог выключен' if cur_on else '📤 Лог включён')

    elif data == 'SET_TOGGLE_REPORT':
        cur = db.get_report_enabled()
        db.set_report_enabled(0 if cur else 1)
        text, kb = build_settings_card()
        try:
            await c.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
        except Exception:
            pass
        await c.answer('📊 Отчёт выключен' if cur else '📊 Отчёт включён')

    elif data == 'SET_TOGGLE_SYNC':
        try:
            cur = db.get_sync_slowmode() == 1
        except Exception:
            cur = True
        db.set_sync_slowmode(0 if cur else 1)
        text, kb = build_settings_card()
        try:
            await c.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
        except Exception:
            pass
        await c.answer('🐢 Синх выключен' if cur else '🐢 КД впритык к слоумоду включён')

    elif data == 'SET_LOG_CHAT':
        await state.set_data({'prompt_id': c.message.message_id})
        await c.message.edit_text(
            '📋 Перешли сюда любое сообщение из канала/чата для лога — возьму его ID.\n'
            'Либо отправь ID (-100...) или @username.\n\n/cancel — отмена.',
            reply_markup=back_to_settings_kb())
        await state.set_state(settings_state.log_chat)
        await c.answer()

    elif data == 'SET_ALL_ON':
        try:
            total = db.c.execute('SELECT COUNT(*) FROM CHANNELS').fetchone()[0] or 0
        except Exception:
            total = 0
        try:
            await c.message.edit_text(
                f'Включить рассылку сразу во <b>все {total}</b> чатов?',
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text='✅ Да, включить все', callback_data='SET_ALL_ON_YES',
                                          style=ButtonStyle.SUCCESS)],
                    [InlineKeyboardButton(text='⬅️ К настройкам', callback_data='SETTINGS_BACK')],
                ]),
                parse_mode=ParseMode.HTML)
        except Exception:
            pass
        await c.answer()

    elif data == 'SET_ALL_ON_YES':
        n = db.set_all_channels(1)
        text, kb = build_settings_card()
        try:
            await c.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
        except Exception:
            pass
        await c.answer(f'✅ Включено чатов: {n}')

    elif data == 'SET_ALL_OFF':
        try:
            total = db.c.execute('SELECT COUNT(*) FROM CHANNELS').fetchone()[0] or 0
        except Exception:
            total = 0
        try:
            await c.message.edit_text(
                f'Выключить рассылку во <b>всех {total}</b> чатах?',
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text='⛔ Да, выключить все', callback_data='SET_ALL_OFF_YES',
                                          style=ButtonStyle.DANGER)],
                    [InlineKeyboardButton(text='⬅️ К настройкам', callback_data='SETTINGS_BACK')],
                ]),
                parse_mode=ParseMode.HTML)
        except Exception:
            pass
        await c.answer()

    elif data == 'SET_ALL_OFF_YES':
        n = db.set_all_channels(0)
        text, kb = build_settings_card()
        try:
            await c.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
        except Exception:
            pass
        await c.answer(f'⛔ Выключено чатов: {n}')

    elif data == 'SET_REFRESH_CHATS':
        chats = await user.get_chats(force=True)
        text, kb = build_settings_card()
        try:
            await c.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
        except Exception:
            pass
        await c.answer(f'♻️ Список обновлён: {len(chats)}')

    elif data == 'SET_CLEAR_STATUS':
        user.send_status.clear()
        text, kb = build_settings_card()
        try:
            await c.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
        except Exception:
            pass
        await c.answer('🧹 Статусы отправок сброшены')

    elif data == 'SETTINGS_BACK':
        text, kb = build_settings_card()
        try:
            await c.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
        except Exception:
            pass
        await c.answer()

    elif data == 'SETTINGS_CLOSE':
        try:
            await c.message.delete()
        except Exception:
            pass
        await c.answer()

    elif data == 'INTERVAL':
        settings = db.settings()
        await state.set_data({'prompt_id': c.message.message_id})
        await c.message.edit_text(f'Текущий интервал: {settings[5]} мин.\nВведите новый интервал:')
        await state.set_state(global_time.timeout)
        await c.answer()

    elif data == 'PAGINATION':
        await c.answer()

    elif data == 'update_confirm':
        await c.message.edit_text('Запускаю обновление...')
        try:
            result = updater.run_update()
            msg = result.get("error") or result.get("message", "Готово")
            await c.message.edit_text(f'{msg}\n\nПерезапустите скрипт для применения.')
        except Exception as e:
            await c.message.edit_text(f'Ошибка обновления: {e}')
        await state.clear()
        await c.answer()

    elif data == 'update_cancel':
        await c.message.edit_text('Обновление отменено')
        await state.clear()
        await c.answer()

    else:
        await c.answer()


@router.message(add_chat_state.id)
async def input_chat_id(m: Message, state: FSMContext):
    if await _deny_if_not_admin(m):
        await state.clear()
        return
    prompt = await _prompt_id(state)
    try:
        chat_id = int((m.text or '').strip())
    except (ValueError, AttributeError):
        await _clean_trigger(m)
        # состояние сохраняем — можно попробовать снова в том же окне
        await _edit_or_send(m.chat.id, prompt,
                            '❌ ID должен быть числом (например: -1001234567890).\nПопробуйте снова:')
        return
    await _clean_trigger(m)
    try:
        db.add_channel(chat_id)
        await _edit_or_send(m.chat.id, prompt,
                            f'✅ Чат {chat_id} добавлен!',
                            back_to_chats_kb())
    except Exception as e:
        await _edit_or_send(m.chat.id, prompt, f'Ошибка: {e}', back_to_chats_kb())
    finally:
        await state.clear()


@router.message(add_chat_state.username)
async def input_chat_username(m: Message, state: FSMContext):
    if await _deny_if_not_admin(m):
        await state.clear()
        return
    prompt = await _prompt_id(state)
    username = (m.text or '').strip()
    await _clean_trigger(m)
    if not await user.ensure_connected():
        await _edit_or_send(m.chat.id, prompt,
                            'Pyrogram не подключен. Используй /login',
                            back_to_chats_kb())
        await state.clear()
        return
    username = username.lstrip('@')
    if not username:
        # состояние сохраняем — повтор в том же окне
        await _edit_or_send(m.chat.id, prompt,
                            '❌ Пустой username. Пример: @mychannel')
        return
    try:
        chat = await user.client.get_chat(username)
    except Exception as e:
        await _edit_or_send(m.chat.id, prompt,
                            f'❌ Не нашёл чат: {html.escape(str(e))}\nПопробуйте снова.')
        return
    db.add_channel(chat.id)
    await _edit_or_send(m.chat.id, prompt,
                        f'✅ Чат @{html.escape(username)} (ID: {chat.id}) добавлен!',
                        back_to_chats_kb())
    await state.clear()


@router.message(add_chat_state.link)
async def input_chat_link(m: Message, state: FSMContext):
    if await _deny_if_not_admin(m):
        await state.clear()
        return
    prompt = await _prompt_id(state)
    link = (m.text or '').strip()
    await _clean_trigger(m)
    if not await user.ensure_connected():
        await _edit_or_send(m.chat.id, prompt,
                            'Pyrogram не подключен. Используй /login',
                            back_to_chats_kb())
        await state.clear()
        return
    import re as _re
    match = _re.search(r't\.me/(?:joinchat/|[+])?([a-zA-Z0-9_]+)', link)
    if not match:
        await _edit_or_send(m.chat.id, prompt,
                            '❌ Неверный формат ссылки. Пример: https://t.me/mychannel')
        return
    try:
        chat = await user.client.get_chat(match.group(1))
    except Exception as e:
        await _edit_or_send(m.chat.id, prompt,
                            f'❌ Не нашёл чат: {html.escape(str(e))}\nПопробуйте снова.')
        return
    db.add_channel(chat.id)
    await _edit_or_send(m.chat.id, prompt,
                        f'✅ Чат {html.escape(chat.title or match.group(1))} (ID: {chat.id}) добавлен!',
                        back_to_chats_kb())
    await state.clear()


def _parse_log_target(m: Message):
    """Куда писать лог: id из форварда, число, @username или t.me-ссылка.
    Возвращает int/str либо None (нечего парсить)."""
    fwd = m.forward_from_chat
    if fwd is not None:
        return fwd.id
    raw = (m.text or '').strip()
    if not raw:
        return None
    if re.fullmatch(r'-?\d+', raw):
        return int(raw)
    mm = re.search(r't\.me/(?:joinchat/|[+])?([a-zA-Z0-9_]+)', raw)
    uname = ('@' + mm.group(1)) if mm else raw
    if not uname.startswith('@'):
        uname = '@' + uname.lstrip('@')
    return uname


@router.message(settings_state.log_chat)
async def input_log_chat(m: Message, state: FSMContext):
    if await _deny_if_not_admin(m):
        await state.clear()
        return
    prompt = await _prompt_id(state)
    await _clean_trigger(m)
    try:
        dest = _parse_log_target(m)
        if dest is None:
            await _edit_or_send(m.chat.id, prompt,
                                '❌ Перешли сообщение из лог-канала или отправь ID/@username.')
            return
        if not await user.ensure_connected():
            await _edit_or_send(m.chat.id, prompt,
                                'Pyrogram не подключен. Используй /login',
                                back_to_settings_kb())
            await state.clear()
            return
        try:
            await user.client.send_message(dest, '✅ Лог отправок подключён: сюда будут падать копии постов.')
        except Exception as e:
            await _edit_or_send(m.chat.id, prompt,
                                f'❌ Не смог написать в {html.escape(str(dest))}: {html.escape(str(e))}\n'
                                f'Проверь что аккаунт участник чата и попробуй снова.')
            return
        db.set_log_chat(dest)
        db.set_log_enabled(1)
        text, kb = build_settings_card()
        await _edit_or_send(m.chat.id, prompt, f'✅ Лог-чат подключён!\n\n{text}', kb)
        await state.clear()
    except Exception as e:
        logger.exception('input_log_chat упал')
        try:
            await _edit_or_send(m.chat.id, prompt, f'❌ Ошибка: {html.escape(str(e))}',
                                back_to_settings_kb())
        except Exception:
            pass
        await state.clear()


@router.message(topic_state.id)
async def input_topic_id(m: Message, state: FSMContext):
    if await _deny_if_not_admin(m):
        await state.clear()
        return
    data = await state.get_data()
    chat_id = data.get('chat_id')
    prompt = data.get('prompt_id')
    await _clean_trigger(m)
    if not chat_id:
        await _edit_or_send(m.chat.id, prompt, 'Не найден ID чата.', back_to_chats_kb())
        await state.clear()
        return
    raw = (m.text or '').strip()
    if raw.lower() in ('general', 'общая', '0'):
        db.set_topic(chat_id, 0, 'General')
        await _edit_or_send(m.chat.id, prompt, '✅ Тема: General (общая лента).',
                            back_to_chat_kb(chat_id))
        await state.clear()
        return
    try:
        topic_id = int(raw)
        if topic_id <= 0:
            raise ValueError()
    except (ValueError, AttributeError):
        await _edit_or_send(m.chat.id, prompt,
                            '❌ Нужен числовой ID темы (или 0 для общей). Попробуй снова:')
        return
    name = ''
    try:
        for t in await user.get_forum_topics(chat_id):
            if int(t['id']) == topic_id:
                name = t.get('title') or ''
                break
    except Exception:
        pass
    db.set_topic(chat_id, topic_id, name)
    await _edit_or_send(m.chat.id, prompt,
                        f'✅ Тема: {html.escape(name) if name else f"#{topic_id}"}.',
                        back_to_chat_kb(chat_id))
    await state.clear()


@router.message(addition.id)
async def input_additional_text(m: Message, state: FSMContext):
    if await _deny_if_not_admin(m):
        await state.clear()
        return
    data = await state.get_data()
    chat_id = data.get('chat_id')
    prompt = data.get('prompt_id')
    await _clean_trigger(m)
    if not chat_id:
        await _edit_or_send(m.chat.id, prompt, 'Не найден ID чата.', back_to_chats_kb())
        await state.clear()
        return
    try:
        db.add_additional_text(chat_id, message_to_html(m.text, m.entities))
    except Exception as e:
        await _edit_or_send(m.chat.id, prompt, f'Ошибка: {e}', back_to_chat_kb(chat_id))
        await state.clear()
        return
    await _edit_or_send(m.chat.id, prompt,
                        f'✅ Доп. текст для чата {chat_id} обновлён.',
                        back_to_chat_kb(chat_id))
    await state.clear()


@router.message(post.text)
async def input_post_text(m: Message, state: FSMContext):
    if await _deny_if_not_admin(m):
        await state.clear()
        return
    prompt = await _prompt_id(state)
    await _clean_trigger(m)
    try:
        db.change_text(message_to_html(m.text, m.entities))
    except Exception as e:
        await _edit_or_send(m.chat.id, prompt, f'Ошибка: {e}', back_to_global_kb())
        await state.clear()
        return
    await _edit_or_send(m.chat.id, prompt,
                        '✅ Текст общего поста обновлён.',
                        back_to_global_kb())
    await state.clear()


@router.message(channel_post_text.text)
async def input_channel_post_text(m: Message, state: FSMContext):
    if await _deny_if_not_admin(m):
        await state.clear()
        return
    data = await state.get_data()
    chat_id = data.get('chat_id')
    prompt = data.get('prompt_id')
    await _clean_trigger(m)
    if not chat_id:
        await _edit_or_send(m.chat.id, prompt, 'Не найден ID чата.', back_to_chats_kb())
        await state.clear()
        return
    try:
        db.set_channel_post(chat_id, text=message_to_html(m.text, m.entities))
    except Exception as e:
        await _edit_or_send(m.chat.id, prompt, f'Ошибка: {e}',
                            back_to_channel_post_kb(chat_id))
        await state.clear()
        return
    await _edit_or_send(m.chat.id, prompt,
                        '✅ Текст поста чата обновлён.',
                        back_to_channel_post_kb(chat_id))
    await state.clear()


@router.message(channel_post_photo.photo, F.photo)
async def input_channel_post_photo(m: Message, state: FSMContext):
    if await _deny_if_not_admin(m):
        await state.clear()
        return
    data = await state.get_data()
    chat_id = data.get('chat_id')
    prompt = data.get('prompt_id')
    if not chat_id:
        await _edit_or_send(m.chat.id, prompt, 'Не найден ID чата.', back_to_chats_kb())
        await state.clear()
        return
    if not m.photo:
        return  # ждём именно фото, состояние сохраняем
    try:
        file_id = m.photo[-1].file_id
        filename = await save_telegram_file(file_id, f'channel_{chat_id}', '.jpg')
        db.set_channel_post(chat_id, photo=filename)
    except Exception as e:
        await _edit_or_send(m.chat.id, prompt, f'Ошибка: {e}',
                            back_to_channel_post_kb(chat_id))
        await state.clear()
        return
    await _edit_or_send(m.chat.id, prompt,
                        '✅ Фото поста чата обновлено.',
                        back_to_channel_post_kb(chat_id))
    await state.clear()


@router.message(channel_post_video.video, F.video)
async def input_channel_post_video(m: Message, state: FSMContext):
    if await _deny_if_not_admin(m):
        await state.clear()
        return
    data = await state.get_data()
    chat_id = data.get('chat_id')
    prompt = data.get('prompt_id')
    if not chat_id:
        await _edit_or_send(m.chat.id, prompt, 'Не найден ID чата.', back_to_chats_kb())
        await state.clear()
        return
    if not m.video:
        return
    try:
        file_id = m.video.file_id
        filename = await save_telegram_file(file_id, f'channel_{chat_id}', '.mp4')
        db.set_channel_post(chat_id, video=filename)
    except Exception as e:
        await _edit_or_send(m.chat.id, prompt, f'Ошибка: {e}',
                            back_to_channel_post_kb(chat_id))
        await state.clear()
        return
    await _edit_or_send(m.chat.id, prompt,
                        '✅ Видео поста чата обновлено.',
                        back_to_channel_post_kb(chat_id))
    await state.clear()


@router.message(global_post_photo.photo, F.photo)
async def download_global_photo(m: Message, state: FSMContext):
    if await _deny_if_not_admin(m):
        await state.clear()
        return
    prompt = await _prompt_id(state)
    if not m.photo:
        return
    try:
        file_id = m.photo[-1].file_id
        filename = await save_telegram_file(file_id, 'global', '.jpg')
        db.change_photo(filename)
    except Exception as e:
        await _edit_or_send(m.chat.id, prompt, f'Ошибка сохранения фото: {e}',
                            back_to_global_kb())
        await state.clear()
        return
    await _edit_or_send(m.chat.id, prompt,
                        '✅ Фото общего поста обновлено.',
                        back_to_global_kb())
    await state.clear()


@router.message(global_post_video.video, F.video)
async def download_global_video(m: Message, state: FSMContext):
    if await _deny_if_not_admin(m):
        await state.clear()
        return
    prompt = await _prompt_id(state)
    if not m.video:
        return
    try:
        file_id = m.video.file_id
        filename = await save_telegram_file(file_id, 'global', '.mp4')
        db.change_video(filename)
    except Exception as e:
        await _edit_or_send(m.chat.id, prompt, f'Ошибка сохранения видео: {e}',
                            back_to_global_kb())
        await state.clear()
        return
    await _edit_or_send(m.chat.id, prompt,
                        '✅ Видео общего поста обновлено.',
                        back_to_global_kb())
    await state.clear()


@router.message(global_post_forward.forward)
async def input_global_forward(m: Message, state: FSMContext):
    if await _deny_if_not_admin(m):
        await state.clear()
        return
    prompt = await _prompt_id(state)
    await _clean_trigger(m)
    try:
        ok, fc, fm, err = await _capture_forward(m)
        if not ok:
            await _edit_or_send(m.chat.id, prompt, f'❌ {err} Попробуй снова.')
            return
        db.set_forward(fc, fm)
        title = await _forward_source_title(fc)
        await _edit_or_send(
            m.chat.id, prompt,
            f'✅ Пост-пересылка сохранён (из {html.escape(title)}).\n'
            f'Текст (если задан) уйдёт следом отдельным сообщением.',
            back_to_global_kb())
        await state.clear()
    except Exception as e:
        logger.exception('input_global_forward упал')
        try:
            await _edit_or_send(m.chat.id, prompt, f'❌ Ошибка: {html.escape(str(e))}',
                                back_to_global_kb())
        except Exception:
            pass
        await state.clear()


@router.message(channel_post_forward.forward)
async def input_channel_post_forward(m: Message, state: FSMContext):
    if await _deny_if_not_admin(m):
        await state.clear()
        return
    data = await state.get_data()
    chat_id = data.get('chat_id')
    prompt = data.get('prompt_id')
    await _clean_trigger(m)
    if not chat_id:
        await _edit_or_send(m.chat.id, prompt, 'Не найден ID чата.', back_to_chats_kb())
        await state.clear()
        return
    try:
        ok, fc, fm, err = await _capture_forward(m)
        if not ok:
            await _edit_or_send(m.chat.id, prompt, f'❌ {err} Попробуй снова.')
            return
        db.set_channel_forward(chat_id, fc, fm)
        title = await _forward_source_title(fc)
        await _edit_or_send(
            m.chat.id, prompt,
            f'✅ Пост-пересылка для чата {chat_id} сохранён (из {html.escape(title)}).',
            back_to_channel_post_kb(chat_id))
        await state.clear()
    except Exception as e:
        logger.exception('input_channel_post_forward упал')
        try:
            await _edit_or_send(m.chat.id, prompt, f'❌ Ошибка: {html.escape(str(e))}',
                                back_to_channel_post_kb(chat_id))
        except Exception:
            pass
        await state.clear()


@router.message(global_time.timeout)
async def input_timeout(m: Message, state: FSMContext):
    if await _deny_if_not_admin(m):
        await state.clear()
        return
    prompt = await _prompt_id(state)
    await _clean_trigger(m)
    try:
        timeout = int((m.text or '').strip())
    except (ValueError, AttributeError):
        await _edit_or_send(m.chat.id, prompt, '❌ Введите число. Попробуйте снова:')
        return
    if timeout < 1:
        await _edit_or_send(m.chat.id, prompt, '❌ Введите число больше 1. Попробуйте снова:')
        return
    try:
        db.setTimeOut(timeout)
    except Exception as e:
        await _edit_or_send(m.chat.id, prompt, f'Ошибка: {e}', back_to_global_kb())
        await state.clear()
        return
    await _edit_or_send(m.chat.id, prompt,
                        f'✅ Интервал по умолчанию: {timeout} мин.',
                        back_to_global_kb())
    await state.clear()


@router.message(channel_time.timeout)
async def input_channel_timeout(m: Message, state: FSMContext):
    if await _deny_if_not_admin(m):
        await state.clear()
        return
    data = await state.get_data()
    chat_id = data.get('chat_id')
    prompt = data.get('prompt_id')
    await _clean_trigger(m)
    if not chat_id:
        await _edit_or_send(m.chat.id, prompt, 'Не найден ID чата.', back_to_chats_kb())
        await state.clear()
        return
    try:
        timeout = int((m.text or '').strip())
    except (ValueError, AttributeError):
        await _edit_or_send(m.chat.id, prompt, '❌ Введите число. Попробуйте снова:')
        return
    if timeout < 1:
        await _edit_or_send(m.chat.id, prompt, '❌ Введите число больше 1. Попробуйте снова:')
        return
    try:
        db.set_channel_timeout(chat_id, timeout)
    except Exception as e:
        await _edit_or_send(m.chat.id, prompt, f'Ошибка: {e}', back_to_chat_kb(chat_id))
        await state.clear()
        return
    await _edit_or_send(m.chat.id, prompt,
                        f'✅ Интервал чата {chat_id}: {timeout} мин.',
                        back_to_chat_kb(chat_id))
    await state.clear()


@router.message(login_phone.phone)
async def handle_phone_input(m: Message, state: FSMContext):
    if await _deny_if_not_admin(m):
        await state.clear()
        return
    phone = (m.text or '').strip().replace(' ', '').replace('-', '')
    if re.match(r'^\+?[0-9]{7,15}$', phone):
        user.login_phone = phone
        user.login_password = None
        user.current_code[m.chat.id] = ""
        phone_prompt = await _prompt_id(state)
        await _clean_trigger(m)

        prog = await m.answer('Отправляю код...')
        result = await user.do_login(phone)
        if result is None:
            detail = getattr(user, 'login_error', None) or 'Проверьте номер телефона.'
            try:
                await prog.delete()
            except Exception:
                pass
            await _edit_or_send(m.chat.id, phone_prompt,
                                f'❌ Ошибка отправки кода. {detail}\nНажми /login чтобы попробовать снова.')
            await state.clear()
            return

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='1', callback_data='code_1'),
             InlineKeyboardButton(text='2', callback_data='code_2'),
             InlineKeyboardButton(text='3', callback_data='code_3')],
            [InlineKeyboardButton(text='4', callback_data='code_4'),
             InlineKeyboardButton(text='5', callback_data='code_5'),
             InlineKeyboardButton(text='6', callback_data='code_6')],
            [InlineKeyboardButton(text='7', callback_data='code_7'),
             InlineKeyboardButton(text='8', callback_data='code_8'),
             InlineKeyboardButton(text='9', callback_data='code_9')],
            [InlineKeyboardButton(text='⌫', callback_data='code_back'),
             InlineKeyboardButton(text='0', callback_data='code_0'),
             InlineKeyboardButton(text='Войти', callback_data='code_enter',
                                  style=ButtonStyle.SUCCESS)]
        ])
        msg = await m.answer(CODE_PROMPT.format(code=''), reply_markup=keyboard)
        user.code_messages[m.chat.id] = msg.message_id
        # прошлое окно ("введите номер" + "отправляю код...") больше не нужно
        for old_id in (phone_prompt, prog.message_id if prog else None):
            if old_id:
                try:
                    await bot.delete_message(m.chat.id, old_id)
                except Exception:
                    pass
        await state.set_state(login_code.code)
    else:
        await m.answer('Неверный формат. Пример: +79001234567')




@router.message(login_code.code)
async def handle_code_text(m: Message, state: FSMContext):
    if await _deny_if_not_admin(m):
        return
    # Код сообщением не принимаем: сразу удаляем из чата (безопасность)
    # и просим ввести кнопками.
    try:
        await m.delete()
    except Exception:
        pass
    await m.answer('⚠️ Код сообщением не принимаю — он так не сработает.\n'
                   'Вводите код кнопками в сообщении выше ⬆️')


def _idle_hint(m: Message) -> str | None:
    """Подсказка когда админ шлёт что-то без активного ввода.
    Частый случай: бот перезапускался и выбор (напр. лог-чата) сброшен."""
    if m.forward_from_chat is not None:
        return ('Похоже, выбор сброшен (бот перезапускался?) — это пересланное '
                'сообщение ни к чему не привязано.\nОткрой ⚙️ Настройки → 📋 Лог-чат заново.')
    t = (m.text or '').strip()
    if re.fullmatch(r'-?\d{6,}', t):
        return ('Похоже на ID чата, но сейчас ничего не спрашиваю '
                '(выбор сбрасывается при перезапуске).\nЕсли выбирал лог-чат — '
                'открой ⚙️ Настройки → 📋 Лог-чат заново.')
    return None


@router.message(F.forward_from_chat)
async def echo_forward(m: Message, state: FSMContext):
    if m.chat.type != 'private':
        return
    uid = m.from_user.id if m.from_user else 0
    if m.chat.id not in config.ADMINS and uid not in config.ADMINS:
        return
    if await state.get_state() is not None:
        return  # идёт ввод — хендлер состояния разберётся сам
    sent = await m.answer(_idle_hint(m) or 'Неизвестная команда. /help — список команд.',
                          reply_markup=welcome_keyboard())
    asyncio.create_task(_auto_delete(m.chat.id, sent.message_id, 20))


@router.message(F.text)
async def echo_message(m: Message, state: FSMContext):
    if m.chat.type != 'private':
        return
    uid = m.from_user.id if m.from_user else 0
    if m.chat.id not in config.ADMINS and uid not in config.ADMINS:
        return
    if await state.get_state() is not None:
        return  # идёт ввод — молчим, ждём данные
    sent = await m.answer(_idle_hint(m) or 'Неизвестная команда. /help — список команд.',
                          reply_markup=welcome_keyboard())
    asyncio.create_task(_auto_delete(m.chat.id, sent.message_id, 15))


async def do_login(chat_id):
    user.login_phone = None
    user.login_password = None
    await user._delete_session()
    return await bot.send_message(chat_id, '📱 Введите номер телефона (в формате +79001234567):\n\n/cancel — отмена.')


async def do_update_menu(chat_id):
    check = updater.check_update()
    if check.get("error"):
        await bot.send_message(chat_id, f"Ошибка: {check['error']}")
        return
    if check["update_available"]:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='Обновить', callback_data='update_confirm',
                                  style=ButtonStyle.SUCCESS)],
            [InlineKeyboardButton(text='Отмена', callback_data='update_cancel')]
        ])
        await bot.send_message(chat_id,
            f"Доступно обновление!\nТекущая: {check['current']}\nПоследняя: {check['latest']}\n\nОбновить?",
            reply_markup=keyboard)
    else:
        await bot.send_message(chat_id, f"Актуальная версия: {check['current']}")


async def start_spam_loop() -> int:
    """Запустить спам-цикл. Возвращает число активных чатов (0 если не запущен)."""
    global spam_task
    settings = db.settings()
    if settings[4] != 1:
        return 0
    # Не плодим дубликаты: отменяем предыдущий цикл
    if spam_task and not spam_task.done():
        spam_task.cancel()
        try:
            await spam_task
        except asyncio.CancelledError:
            pass
    if not await user.ensure_connected():
        has_session = os.path.exists('session.session')
        try:
            await bot.send_message(
                config.ADMINS[0],
                'Pyrogram не подключен. Используй /login' if not has_session
                else '⚠️ Нет соединения с Telegram — рассылка подождёт сеть и стартует сама.')
        except Exception:
            pass
        if not has_session:
            # сессии нет вообще — включать нечего
            db.setSpam(0)
        # при обрыве сети флаг НЕ гасим: watchdog поднимет цикл позже
        return 0
    chats = await user.get_chats()
    # Фильтруем только включённые в БД
    enabled = [ch for ch in chats if db.get_channel_spam_status(ch['id']) == 1]
    if not enabled:
        db.setSpam(0)
        await bot.send_message(config.ADMINS[0],
                               'Нет включённых чатов для рассылки. Включи их в «💬 Чаты».',
                               reply_markup=welcome_keyboard())
        return 0

    spam_task = asyncio.create_task(user.spamming(enabled, db.settings(), db))
    return len(enabled)


async def main():
    global _wd_started
    logger.info(f"Autoposter {get_version()} стартует. Админы: {config.ADMINS}")
    try:
        st = db.settings()
        logger.info(f"Настройки: спам={'ВКЛ' if st[4] == 1 else 'ВЫКЛ'}, "
                    f"интервал={st[5]} мин.")
    except Exception as e:
        logger.error(f"Не смог прочитать настройки: {e}")
    # Запускаем Pyrogram клиент при старте
    connected = await user.start_client()
    startup_info['pyro_ok'] = connected
    if not connected:
        logger.warning("Pyrogram не подключен. Используй /login для входа.")
    # Если спам был включён до перезапуска — возобновляем
    try:
        if db.settings()[4] == 1:
            startup_info['spam_was'] = True
            startup_info['resumed'] = await start_spam_loop()
    except Exception as e:
        logger.error(f"Ошибка автовозобновления спама: {e}")
    if not _wd_started:
        _wd_started = True
        asyncio.create_task(_spam_watchdog(), name='spam-watchdog')
    await _run_polling()


@dp.error()
async def error_handler(event: ErrorEvent):
    if _is_quiet_error(event.exception):
        logger.warning(f'Переходная сетевая ошибка (самовоcстановление): {event.exception!r}')
        return
    logger.exception(f"Ошибка хендлера: {event.exception!r}")
    chat_id = None
    try:
        upd = event.update
        msg = getattr(upd, 'message', None) or getattr(upd, 'edited_message', None)
        if msg is not None:
            chat_id = msg.chat.id
        elif getattr(upd, 'callback_query', None) is not None:
            chat_id = upd.callback_query.message.chat.id
    except Exception:
        pass
    try:
        text = f'⚠️ Ошибка: {type(event.exception).__name__}: {event.exception}'.strip()
        if chat_id in config.ADMINS:
            await bot.send_message(chat_id, _short(text, 300))
    except Exception:
        pass


if __name__ == '__main__':
    if not acquire_lock():
        raise SystemExit(1)
    asyncio.run(main())
