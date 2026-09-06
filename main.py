from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram import Router, F
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramConflictError
from aiogram.types import (
    Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, ErrorEvent
)

import html
import asyncio
import logging
import os
import re

import config
import user
import updater

from sqliter import DBConnection, markdown_to_html

router = Router()
from aiogram.client.default import DefaultBotProperties
from aiogram.types import FSInputFile

CODE_PROMPT = ('Введите код из Telegram:\nКод: {code}\n\n'
               '⚠️ Код вводите <b>только кнопками ниже</b> — '
               'не отправляйте его сообщением в чат, такой код не приму.') 

def get_version():
    try:
        with open("version.txt", "r") as f:
            return f.read().strip()
    except:
        return "unknown"

bot = Bot(token=config.TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
dp.include_router(router)
db = DBConnection()

user.init_bot(bot)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Активная задача спам-цикла (чтобы не плодить дубликаты)
spam_task: asyncio.Task | None = None

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
    filename = f"{prefix}_{int(time.time())}_{file_id[-8:]}{ext}"
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


# --- Подписи reply-кнопок (единый источник правды: меню и хендлеры используют их) ---
BTN_START = '▶️ Запустить рассылку'
BTN_STOP = '⏹ Остановить рассылку'
BTN_POST = '📝 Общий пост'
BTN_CHATS = '💬 Чаты'
BTN_CPOSTS = '📢 Посты чатов'
BTN_INFO = 'ℹ️ Инфо'
BTN_UPDATE = '🔄 Обновление'
BTN_HOME = '🏠 Главное меню'
BTN_CLIST = '📋 Список постов'
BTN_CADD = '➕ Новый пост'


def welcome_keyboard():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text=BTN_START)],
        [KeyboardButton(text=BTN_POST), KeyboardButton(text=BTN_CHATS)],
        [KeyboardButton(text=BTN_CPOSTS), KeyboardButton(text=BTN_INFO)],
        [KeyboardButton(text=BTN_UPDATE)]
    ], resize_keyboard=True)


def spam_running_keyboard():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text=BTN_STOP)],
        [KeyboardButton(text=BTN_POST), KeyboardButton(text=BTN_CHATS)],
        [KeyboardButton(text=BTN_CPOSTS), KeyboardButton(text=BTN_INFO)]
    ], resize_keyboard=True)


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
    rows = [
        [InlineKeyboardButton(text=spam_text, callback_data=f'TOGGLE_SPAM_SETTINGS:{chat_id}')],
        [InlineKeyboardButton(text='📝 Пост чата', callback_data=f'EDIT_CHANNEL_POST:{chat_id}')],
        [InlineKeyboardButton(text=f'⏱ Интервал: {timeout_val} мин.', callback_data=f'CHANGE_TIMEOUT:{chat_id}')],
        [InlineKeyboardButton(text='💬 Доп. текст', callback_data=f'ADD_ADDITIONAL:{chat_id}')],
    ]
    try:
        addit = db.get_additional_text(chat_id)
        if addit and addit[0]:
            rows.append([InlineKeyboardButton(text='🗑 Убрать доп. текст', callback_data=f'CLEAR_ADDITIONAL:{chat_id}')])
    except Exception:
        pass
    rows.append([InlineKeyboardButton(text='⬅️ К чатам', callback_data='BACK_TO_CHATS')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def format_chat_info(chat_id: int) -> str:
    """Красивая карточка чата для EDIT_CHAT / TOGGLE_SPAM_SETTINGS."""
    spam_status = db.get_channel_spam_status(chat_id)
    try:
        addit = db.get_additional_text(chat_id)
        addit_val = _short(addit[0], 80) if addit and addit[0] else '—'
    except Exception:
        addit_val = '—'
    post_data = db.get_channel_post(chat_id)
    if post_data and (post_data[0] or post_data[1] or post_data[2]):
        parts = []
        if post_data[0]:
            parts.append('📷 фото')
        if post_data[1]:
            parts.append('📹 видео')
        if post_data[2]:
            parts.append(f'«{html.escape(_short(post_data[2], 50))}»')
        post_desc = ' + '.join(parts)
    else:
        post_desc = '—'
    timeout_val = db.get_channel_timeout(chat_id)
    return (f'💬 <b>Чат {chat_id}</b>\n'
            f'{"✅ Рассылка включена" if spam_status == 1 else "⬜ Рассылка выключена"}\n'
            f'⏱ Интервал: {timeout_val} мин.\n'
            f'💬 Доп. текст: {html.escape(addit_val)}\n'
            f'📝 Пост: {post_desc}')


async def get_chats_keyboard(page=0):
    chats = await user.get_chats()
    per_page = 10

    # Убедимся что все чаты есть в БД (новые добавляем как выключенные)
    for chat in chats:
        db.add_channel(chat['id'])

    # Сортируем: включённые сверху
    def sort_key(chat):
        return 0 if db.get_channel_spam_status(chat['id']) == 1 else 1

    chats.sort(key=sort_key)

    start = page * per_page
    end = start + per_page
    page_chats = chats[start:end]

    keyboard = []
    for chat in page_chats:
        spam_status = db.get_channel_spam_status(chat['id'])
        icon = '✅' if spam_status == 1 else '⬜'
        keyboard.append([
            InlineKeyboardButton(
                text=f'{icon} {chat["title"]}',
                callback_data=f'TOGGLE_SPAM:{chat["id"]}:{page}'
            ),
            InlineKeyboardButton(text='⚙️', callback_data=f'EDIT_CHAT:{chat["id"]}')
        ])

    if len(chats) > per_page:
        pagination = []
        total_pages = (len(chats) + per_page - 1) // per_page
        if page > 0:
            pagination.append(InlineKeyboardButton(text='⬅️', callback_data=f'CHATS_PAGE:{page-1}'))
        pagination.append(InlineKeyboardButton(text=f'{page+1}/{total_pages}', callback_data='PAGINATION'))
        if page < total_pages - 1:
            pagination.append(InlineKeyboardButton(text='➡️', callback_data=f'CHATS_PAGE:{page+1}'))
        keyboard.append(pagination)

    keyboard.append([InlineKeyboardButton(text='➕ Добавить чат', callback_data='ADD_CHAT')])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


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

class channel_post_text(StatesGroup):
    text = State()

class channel_post_photo(StatesGroup):
    photo = State()

class channel_post_video(StatesGroup):
    video = State()

class time(StatesGroup):
    timeout = State()

class channel_time(StatesGroup):
    timeout = State()

class add_chat_state(StatesGroup):
    id = State()
    username = State()
    link = State()


@router.message(Command("start"))
async def process_start_command(m: Message):
    if m.chat.type != 'private':
        return  # в группах бот-панель молчит
    if m.chat.id in config.ADMINS:
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
    "• 🔄 Обновление — новая версия с GitHub\n\n"
    "<b>Команды:</b> /start /help /login /update /cancel"
)

@router.message(Command("help"))
async def help_command(m: Message):
    if m.chat.type != 'private':
        return
    if await _deny_if_not_admin(m):
        return
    await m.answer(HELP_TEXT, reply_markup=welcome_keyboard())

@router.message(Command("cancel"))
async def cancel_command(m: Message, state: FSMContext):
    if await _deny_if_not_admin(m):
        return
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
    await do_login(m.chat.id)
    await state.set_state(login_phone.phone)

@router.message(Command("update"))
async def update_command(m: Message):
    if await _deny_if_not_admin(m):
        return
    await do_update_menu(m.chat.id)

@router.message(F.text == BTN_INFO)
async def send_info(message: Message):
    if await _deny_if_not_admin(message):
        return
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
    await message.answer(
        f'ℹ️ <b>Autoposter {version}</b>\n'
        f'Обновление: {upd}\n'
        f'📝 Рассылка: {"✅ запущена" if spam == 1 else "⬜ остановлена"}\n'
        f'💬 Чаты: {active} активно из {total}\n\nSupport: @support')

@router.message(F.text == BTN_UPDATE)
async def update_btn(message: Message):
    if await _deny_if_not_admin(message):
        return
    await do_update_menu(message.chat.id)

@router.message(F.text == BTN_HOME)
async def return_menu(message: Message):
    if await _deny_if_not_admin(message):
        return
    await message.answer('🏠 Главное меню:', reply_markup=welcome_keyboard())

@router.message(F.text == BTN_POST)
async def post_settings(message: Message):
    if await _deny_if_not_admin(message):
        return
    settings = db.settings()
    # settings: [0]=ID, [1]=PHOTO, [2]=VIDEO, [3]=TEXT, [4]=SPAM, [5]=TIMEOUT
    photo = settings[1]
    video = settings[2]
    text = settings[3]
    spam = settings[4]
    timeout = settings[5]
    has_photo = bool(photo)
    has_video = bool(video)

    lines = ['<b>📝 Глобальный пост</b>']
    if has_photo:
        lines.append(f'📷 Фото: {html.escape(photo)}')
    if has_video:
        lines.append(f'📹 Видео: {html.escape(video)}')
    if text:
        lines.append(f'💬 Текст: «{html.escape(_short(text, 120))}»')
    if not (has_photo or has_video or text):
        lines.append('❌ Пост пуст')
    lines.append(f'⏱ Интервал по умолчанию: {timeout} мин.')
    lines.append(f'{"✅ Рассылка запущена" if spam == 1 else "⬜ Рассылка остановлена"}')

    info = '\n'.join(lines)

    keyboard_rows = []
    if has_photo or has_video or text:
        keyboard_rows.append([InlineKeyboardButton(text='👁 Просмотреть пост', callback_data='VIEW_GLOBAL_POST')])
    keyboard_rows.append([InlineKeyboardButton(
        text=f'📝 Текст {"✅" if text else ""}', callback_data='EDIT_TEXT')])
    keyboard_rows.append([
        InlineKeyboardButton(text=f'📷 Фото {"✅" if has_photo else ""}', callback_data='EDIT_PHOTO'),
        InlineKeyboardButton(text=f'📹 Видео {"✅" if has_video else ""}', callback_data='EDIT_VIDEO')
    ])
    if has_photo or has_video:
        keyboard_rows.append([InlineKeyboardButton(text='🗑 Убрать фото/видео', callback_data='DEL_MEDIA')])
    keyboard_rows.append([InlineKeyboardButton(text=f'⏱ Интервал: {timeout} мин.', callback_data='INTERVAL')])

    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
    await message.answer(info, reply_markup=keyboard)

@router.message(F.text == BTN_START)
async def start_spam_cmd(message: Message):
    if await _deny_if_not_admin(message):
        return
    db.setSpam(1)
    enabled = await start_spam_loop()
    if enabled:
        await message.answer(f'✅ Рассылка запущена! Активных чатов: {enabled}.',
                             reply_markup=spam_running_keyboard())
    # если чатов нет — start_spam_loop уже сообщил причину и выключил спам

@router.message(F.text == BTN_STOP)
async def stop_spam_cmd(message: Message):
    if await _deny_if_not_admin(message):
        return
    global spam_task
    db.setSpam(0)
    if spam_task and not spam_task.done():
        spam_task.cancel()
    await message.answer('⏹ Рассылка остановлена.', reply_markup=welcome_keyboard())

@router.message(F.text == BTN_CHATS)
async def chat_settings_menu(message: Message):
    if await _deny_if_not_admin(message):
        return
    try:
        all_chats = db.c.execute('SELECT COUNT(*), COALESCE(SUM(SPAM_ENABLED), 0) FROM CHANNELS').fetchone()
        total, active = (all_chats[0] or 0), (all_chats[1] or 0)
    except Exception:
        total, active = 0, 0
    keyboard = await get_chats_keyboard(0)
    await message.answer(f'💬 <b>Чаты</b> — активно {active} из {total}\n'
                         f'Нажми на чат чтобы вкл/выкл, ⚙️ — настройки',
                         reply_markup=keyboard)

@router.message(F.text == BTN_CPOSTS)
async def channel_posts_menu(message: Message):
    if await _deny_if_not_admin(message):
        return
    await message.answer('📢 <b>Посты чатов</b> — свой текст/медиа для отдельных чатов.\n'
                         'Либо: 💬 Чаты → ⚙️ → 📝 Пост чата.',
                         reply_markup=channel_post_keyboard())

@router.message(F.text == BTN_CADD)
async def add_channel_post_hint(message: Message):
    if await _deny_if_not_admin(message):
        return
    await message.answer(
        'Индивидуальный пост задаётся для конкретного чата:\n'
        '💬 Чаты → ⚙️ → 📝 Пост чата → Текст/Фото/Видео.')

@router.message(F.text == BTN_CLIST)
async def list_channel_posts(message: Message):
    if await _deny_if_not_admin(message):
        return
    try:
        db.c.execute('SELECT CHANNEL, POST_PHOTO, POST_VIDEO, POST_TEXT FROM CHANNELS WHERE COALESCE(POST_PHOTO, "") != "" OR COALESCE(POST_VIDEO, "") != "" OR COALESCE(POST_TEXT, "") != ""')
        posts = db.c.fetchall()
        if posts:
            text = '📋 <b>Посты чатов:</b>\n' + '\n'.join(
                f'• Чат {p[0]}: {"📷" if p[1] else ""}{"📹" if p[2] else ""}{"📝" if p[3] else ""}' for p in posts)
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
async def handle_password_confirm(c: CallbackQuery):
    if await _deny_callback_if_not_admin(c):
        return
    if user.login_password:
        result = await user.do_check_password(user.login_password)
        if result.get("ok"):
            await c.message.edit_text("Вход выполнен успешно!")
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
    password = m.text.strip()
    try:
        await m.delete()
    except Exception:
        pass
    if not password:
        await m.answer('Пароль пустой. Введите пароль 2FA:')
        return
    user.login_password = password
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='✅ Подтвердить', callback_data='password_enter')],
        [InlineKeyboardButton(text='❌ Отмена', callback_data='password_cancel')],
    ])
    await m.answer('Пароль сохранён. Нажмите «Подтвердить» для входа:', reply_markup=keyboard)


@router.callback_query(F.data)
async def callback_handler(c: CallbackQuery, state: FSMContext):
    if await _deny_callback_if_not_admin(c):
        return
    data = c.data

    if data.startswith('CHATS_PAGE:'):
        page = int(data.split(':')[1])
        keyboard = await get_chats_keyboard(page)
        try:
            await c.message.edit_reply_markup(reply_markup=keyboard)
        except Exception:
            pass
        await c.answer()

    elif data.startswith('TOGGLE_SPAM:'):
        parts = data.split(':')
        chat_id = int(parts[1])
        page = int(parts[2]) if len(parts) > 2 else 0
        current = db.get_channel_spam_status(chat_id)
        if current == 1:
            db.stop_spam_for_channel(chat_id)
            toast = '⬜ Рассылка выключена'
        else:
            db.c.execute('UPDATE CHANNELS SET SPAM_ENABLED = 1 WHERE CHANNEL = ?', [str(chat_id)])
            db.conn.commit()
            toast = '✅ Рассылка включена'
        keyboard = await get_chats_keyboard(page)
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
        await c.message.edit_text('Отправьте ID чата (например: -1001234567890):')
        await state.set_state(add_chat_state.id)
        await c.answer()

    elif data == 'INPUT_CHAT_USERNAME':
        await c.message.edit_text('Отправьте username чата (например: @mychannel):')
        await state.set_state(add_chat_state.username)
        await c.answer()

    elif data == 'INPUT_CHAT_LINK':
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
                callback_data=f'ADD_CHAT_FROM_DIALOG:{ch["id"]}'
            )])
        if total_pages > 1:
            nav = []
            if page > 0:
                nav.append(InlineKeyboardButton(text='⬅️', callback_data=f'DIALOGS_PAGE:{page-1}'))
            nav.append(InlineKeyboardButton(text=f'{page+1}/{total_pages}', callback_data='PAGINATION'))
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
        keyboard = await get_chats_keyboard(0)
        try:
            all_chats = db.c.execute('SELECT COUNT(*), COALESCE(SUM(SPAM_ENABLED), 0) FROM CHANNELS').fetchone()
            total, active = (all_chats[0] or 0), (all_chats[1] or 0)
        except Exception:
            total, active = 0, 0
        header = (f'💬 <b>Чаты</b> — активно {active} из {total}\n'
                  f'Нажми на чат чтобы вкл/выкл, ⚙️ — настройки')
        try:
            await c.message.edit_text(header, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        except Exception:
            await c.message.edit_reply_markup(reply_markup=keyboard)
        await c.answer()

    elif data.startswith('CHANGE_TIMEOUT:'):
        chat_id = int(data.split(':')[1])
        await state.set_data({'chat_id': chat_id})
        cur = db.get_channel_timeout(chat_id)
        await c.message.edit_text(f'Текущий интервал чата: {cur} мин.\nВведите новый интервал (в минутах):')
        await state.set_state(channel_time.timeout)
        await c.answer()

    elif data.startswith('ADD_ADDITIONAL:'):
        chat_id = int(data.split(':')[1])
        await state.set_data({'chat_id': chat_id})
        await c.message.edit_text('Введите дополнительный текст для чата:')
        await state.set_state(addition.id)
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
        has_post = has_photo or has_video or has_text
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='👁 Посмотреть пост', callback_data=f'VIEW_CHANNEL_POST:{chat_id}')] if has_post else [],
            [InlineKeyboardButton(text=f'📝 Текст {"✅" if has_text else ""}', callback_data=f'CHANNEL_EDIT_TEXT:{chat_id}')],
            [InlineKeyboardButton(text=f'📷 Фото {"✅" if has_photo else ""}', callback_data=f'CHANNEL_EDIT_PHOTO:{chat_id}'),
             InlineKeyboardButton(text=f'📹 Видео {"✅" if has_video else ""}', callback_data=f'CHANNEL_EDIT_VIDEO:{chat_id}')],
            [InlineKeyboardButton(text='🗑 Очистить пост', callback_data=f'CHANNEL_CLEAR:{chat_id}')] if has_post else [],
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
        if not post_data or not (post_data[0] or post_data[1] or post_data[2]):
            await c.answer('Пост не установлен', show_alert=True)
            return
        photo, video, text = post_data
        text_html = markdown_to_html(text) if text else ''
        try:
            if photo:
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
        await state.set_data({'chat_id': chat_id})
        await c.message.edit_text('Введите текст канального поста (Markdown поддерживается):')
        await state.set_state(channel_post_text.text)
        await c.answer()

    elif data.startswith('CHANNEL_EDIT_PHOTO:'):
        chat_id = int(data.split(':')[1])
        await state.set_data({'chat_id': chat_id})
        await c.message.edit_text('Отправь фото для канального поста:')
        await state.set_state(channel_post_photo.photo)
        await c.answer()

    elif data.startswith('CHANNEL_EDIT_VIDEO:'):
        chat_id = int(data.split(':')[1])
        await state.set_data({'chat_id': chat_id})
        await c.message.edit_text('Отправь видео для канального поста:')
        await state.set_state(channel_post_video.video)
        await c.answer()

    elif data.startswith('CHANNEL_CLEAR:'):
        chat_id = int(data.split(':')[1])
        db.clear_channel_post(chat_id)
        await c.message.edit_text(f'Канальный пост для чата {chat_id} очищен.')
        await c.answer()

    elif data == 'EDIT_TEXT':
        await c.message.edit_text('Введите текст глобального поста:')
        await state.set_state(post.text)
        await c.answer()

    elif data == 'VIEW_GLOBAL_POST':
        settings = db.settings()
        # settings: [0]=ID, [1]=PHOTO, [2]=VIDEO, [3]=TEXT, [4]=SPAM, [5]=TIMEOUT
        photo = settings[1]
        video = settings[2]
        text = settings[3]
        text_html = markdown_to_html(text) if text else ''
        try:
            if photo:
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
        await c.message.edit_text('Отправь фото для глобального поста:')
        await state.set_state(global_post_photo.photo)
        await c.answer()

    elif data == 'EDIT_VIDEO':
        await c.message.edit_text('Отправь видео для глобального поста:')
        await state.set_state(global_post_video.video)
        await c.answer()

    elif data == 'DEL_MEDIA':
        db.change_photo('')
        db.change_video('')
        await c.message.edit_text('🗑 Фото/видео убрано.')
        await c.answer()

    elif data == 'INTERVAL':
        settings = db.settings()
        await c.message.edit_text(f'Текущий интервал: {settings[5]} мин.\nВведите новый интервал:')
        await state.set_state(time.timeout)
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
    try:
        chat_id = int(m.text.strip())
    except (ValueError, AttributeError):
        await bot.send_message(m.chat.id, 'ID должен быть числом. Попробуйте снова:')
        return
    try:
        await m.delete()
    except Exception:
        pass
    try:
        db.add_channel(chat_id)
        await bot.send_message(m.chat.id, f'Чат {chat_id} успешно добавлен!')
    except Exception as e:
        await bot.send_message(m.chat.id, f'Ошибка: {e}')
    finally:
        await state.clear()


@router.message(add_chat_state.username)
async def input_chat_username(m: Message, state: FSMContext):
    if await _deny_if_not_admin(m):
        await state.clear()
        return
    username = (m.text or '').strip()
    try:
        await m.delete()
    except Exception:
        pass
    try:
        if not await user.ensure_connected():
            await bot.send_message(m.chat.id, 'Pyrogram не подключен. Используй /login')
            await state.clear()
            return
        username = username.lstrip('@')
        chat = await user.client.get_chat(username)
        db.add_channel(chat.id)
        await bot.send_message(m.chat.id, f'Чат @{username} (ID: {chat.id}) успешно добавлен!')
    except Exception as e:
        await bot.send_message(m.chat.id, f'Ошибка: {e}')
    finally:
        await state.clear()


@router.message(add_chat_state.link)
async def input_chat_link(m: Message, state: FSMContext):
    if await _deny_if_not_admin(m):
        await state.clear()
        return
    link = (m.text or '').strip()
    try:
        await m.delete()
    except Exception:
        pass
    try:
        if not await user.ensure_connected():
            await bot.send_message(m.chat.id, 'Pyrogram не подключен. Используй /login')
            await state.clear()
            return
        # Извлекаем username из ссылки
        import re as _re
        match = _re.search(r't\.me/(?:joinchat/|[+])?([a-zA-Z0-9_]+)', link)
        if not match:
            await bot.send_message(m.chat.id, 'Неверный формат ссылки. Пример: https://t.me/mychannel')
            await state.clear()
            return
        username = match.group(1)
        chat = await user.client.get_chat(username)
        db.add_channel(chat.id)
        await bot.send_message(m.chat.id, f'Чат {chat.title} (ID: {chat.id}) успешно добавлен!')
    except Exception as e:
        await bot.send_message(m.chat.id, f'Ошибка: {e}')
    finally:
        await state.clear()


@router.message(addition.id)
async def input_additional_text(m: Message, state: FSMContext):
    if await _deny_if_not_admin(m):
        await state.clear()
        return
    data = await state.get_data()
    chat_id = data.get('chat_id')
    try:
        await m.delete()
    except Exception:
        pass
    try:
        if chat_id:
            db.add_additional_text(chat_id, m.text)
            await bot.send_message(m.chat.id, f'Доп. текст для чата {chat_id} обновлен!')
        else:
            await bot.send_message(m.chat.id, 'Не найден ID чата.')
    except Exception as e:
        await bot.send_message(m.chat.id, f'Ошибка: {e}')
    finally:
        await state.clear()


@router.message(post.text)
async def input_post_text(m: Message, state: FSMContext):
    if await _deny_if_not_admin(m):
        await state.clear()
        return
    try:
        await m.delete()
    except Exception:
        pass
    try:
        db.change_text(m.text)
        await bot.send_message(m.chat.id, 'Текст глобального поста обновлен!')
    except Exception as e:
        await bot.send_message(m.chat.id, f'Ошибка: {e}')
    finally:
        await state.clear()


@router.message(channel_post_text.text)
async def input_channel_post_text(m: Message, state: FSMContext):
    if await _deny_if_not_admin(m):
        await state.clear()
        return
    data = await state.get_data()
    chat_id = data.get('chat_id')
    try:
        await m.delete()
    except Exception:
        pass
    try:
        if chat_id:
            db.set_channel_post(chat_id, text=m.text)
            await bot.send_message(m.chat.id, 'Текст канального поста обновлен!')
        else:
            await bot.send_message(m.chat.id, 'Не найден ID чата.')
    except Exception as e:
        await bot.send_message(m.chat.id, f'Ошибка: {e}')
    finally:
        await state.clear()


@router.message(channel_post_photo.photo, F.photo)
async def input_channel_post_photo(m: Message, state: FSMContext):
    if await _deny_if_not_admin(m):
        await state.clear()
        return
    data = await state.get_data()
    chat_id = data.get('chat_id')
    try:
        if chat_id:
            file_id = m.photo[-1].file_id
            filename = await save_telegram_file(file_id, f'channel_{chat_id}', '.jpg')
            db.set_channel_post(chat_id, photo=filename)
            await bot.send_message(m.chat.id, 'Фото канального поста обновлено!')
        else:
            await bot.send_message(m.chat.id, 'Не найден ID чата.')
    except Exception as e:
        await bot.send_message(m.chat.id, f'Ошибка: {e}')
    finally:
        await state.clear()


@router.message(channel_post_video.video, F.video)
async def input_channel_post_video(m: Message, state: FSMContext):
    if await _deny_if_not_admin(m):
        await state.clear()
        return
    data = await state.get_data()
    chat_id = data.get('chat_id')
    try:
        if chat_id:
            file_id = m.video.file_id
            filename = await save_telegram_file(file_id, f'channel_{chat_id}', '.mp4')
            db.set_channel_post(chat_id, video=filename)
            await bot.send_message(m.chat.id, 'Видео канального поста обновлено!')
        else:
            await bot.send_message(m.chat.id, 'Не найден ID чата.')
    except Exception as e:
        await bot.send_message(m.chat.id, f'Ошибка: {e}')
    finally:
        await state.clear()


@router.message(global_post_photo.photo, F.photo)
async def download_global_photo(m: Message, state: FSMContext):
    if await _deny_if_not_admin(m):
        await state.clear()
        return
    try:
        file_id = m.photo[-1].file_id
        filename = await save_telegram_file(file_id, 'global', '.jpg')
        db.change_photo(filename)
        await bot.send_message(m.chat.id, 'Фото глобального поста обновлено.')
    except Exception as e:
        await bot.send_message(m.chat.id, f'Ошибка сохранения фото: {e}')
    finally:
        await state.clear()


@router.message(global_post_video.video, F.video)
async def download_global_video(m: Message, state: FSMContext):
    if await _deny_if_not_admin(m):
        await state.clear()
        return
    try:
        file_id = m.video.file_id
        filename = await save_telegram_file(file_id, 'global', '.mp4')
        db.change_video(filename)
        await bot.send_message(m.chat.id, 'Видео глобального поста обновлено.')
    except Exception as e:
        await bot.send_message(m.chat.id, f'Ошибка сохранения видео: {e}')
    finally:
        await state.clear()


@router.message(time.timeout)
async def input_timeout(m: Message, state: FSMContext):
    if await _deny_if_not_admin(m):
        await state.clear()
        return
    try:
        await m.delete()
    except Exception:
        pass
    try:
        timeout = int(m.text.strip())
        if timeout >= 1:
            db.setTimeOut(timeout)
            await bot.send_message(m.chat.id, f'Интервал обновлен: {timeout} минут')
        else:
            await bot.send_message(m.chat.id, 'Введите число больше 1.')
    except ValueError:
        await bot.send_message(m.chat.id, 'Введите число.')
    except Exception as e:
        await bot.send_message(m.chat.id, f'Ошибка: {e}')
    finally:
        await state.clear()


@router.message(channel_time.timeout)
async def input_channel_timeout(m: Message, state: FSMContext):
    if await _deny_if_not_admin(m):
        await state.clear()
        return
    data = await state.get_data()
    chat_id = data.get('chat_id')
    try:
        await m.delete()
    except Exception:
        pass
    try:
        timeout = int(m.text.strip())
        if timeout >= 1:
            if chat_id:
                db.set_channel_timeout(chat_id, timeout)
                await bot.send_message(m.chat.id, f'Интервал для чата {chat_id}: {timeout} минут')
            else:
                await bot.send_message(m.chat.id, 'Не найден ID чата.')
        else:
            await bot.send_message(m.chat.id, 'Введите число больше 1.')
    except ValueError:
        await bot.send_message(m.chat.id, 'Введите число.')
    except Exception as e:
        await bot.send_message(m.chat.id, f'Ошибка: {e}')
    finally:
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
        try:
            await m.delete()
        except Exception:
            pass

        await m.answer('Отправляю код...')
        result = await user.do_login(phone)
        if result is None:
            detail = getattr(user, 'login_error', None) or 'Проверьте номер телефона.'
            await m.answer(f'Ошибка отправки кода. {detail}')
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
             InlineKeyboardButton(text='Войти', callback_data='code_enter')]
        ])
        msg = await m.answer(CODE_PROMPT.format(code=''), reply_markup=keyboard)
        user.code_messages[m.chat.id] = msg.message_id
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


@router.message(F.text)
async def echo_message(m: Message, state: FSMContext):
    if m.chat.type != 'private':
        return
    uid = m.from_user.id if m.from_user else 0
    if m.chat.id not in config.ADMINS and uid not in config.ADMINS:
        return
    if await state.get_state() is not None:
        return  # идёт ввод — молчим, ждём данные
    await m.answer('Неизвестная команда. /help — список команд.', reply_markup=welcome_keyboard())


async def do_login(chat_id):
    user.login_phone = None
    user.login_password = None
    await user._delete_session()
    await bot.send_message(chat_id, 'Введите номер телефона (в формате +79001234567):')


async def do_update_menu(chat_id):
    check = updater.check_update()
    if check.get("error"):
        await bot.send_message(chat_id, f"Ошибка: {check['error']}")
        return
    if check["update_available"]:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='Обновить', callback_data='update_confirm')],
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
        await bot.send_message(config.ADMINS[0], 'Pyrogram не подключен. Используй /login')
        db.setSpam(0)
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
    logger.info(f"Autoposter {get_version()} стартует. Админы: {config.ADMINS}")
    try:
        st = db.settings()
        logger.info(f"Настройки: спам={'ВКЛ' if st[4] == 1 else 'ВЫКЛ'}, "
                    f"интервал={st[5]} мин.")
    except Exception as e:
        logger.error(f"Не смог прочитать настройки: {e}")
    # Запускаем Pyrogram клиент при старте
    connected = await user.start_client()
    if not connected:
        logger.warning("Pyrogram не подключен. Используй /login для входа.")
    # Если спам был включён до перезапуска — возобновляем
    try:
        if db.settings()[4] == 1:
            await start_spam_loop()
    except Exception as e:
        logger.error(f"Ошибка автовозобновления спама: {e}")
    try:
        await dp.start_polling(bot)
    except TelegramConflictError:
        logger.error("Telegram Conflict: этот же токен уже опрашивает ДРУГОЙ запущенный "
                     "экземпляр бота. Симптом — команды 'не работают'. Останови дубль.")
        raise


@dp.error()
async def error_handler(event: ErrorEvent):
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
    asyncio.run(main())
