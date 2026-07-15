from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram import Router, F
from aiogram.enums import ParseMode
from aiogram.types import (
    Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, PhotoSize, Video
)

import html
import asyncio
import logging
import os
import re

try:
    import config, user, updater
except Exception as e:
    logger = logging.getLogger(__name__)
    logging.basicConfig(level=logging.INFO)
    logger.error(f"Ошибка импорта модулей: {e}")
    config = None
    user = None
    updater = None

from sqliter import DBConnection, markdown_to_html

router = Router()

from aiogram.client.default import DefaultBotProperties

def get_version():
    try:
        with open("version.txt", "r") as f:
            return f.read().strip()
    except:
        return "unknown"

bot = Bot(token=config.TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML)) if config else None
dp = Dispatcher(storage=MemoryStorage())
dp.include_router(router)
db = DBConnection()

if user:
    user.init_bot(bot)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def welcome_keyboard():
    keyboard = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text='Запустить спам'), KeyboardButton(text='Пост')],
        [KeyboardButton(text='Настройки чатов'), KeyboardButton(text='Информация')],
        [KeyboardButton(text='Обновление')]
    ], resize_keyboard=True)
    return keyboard


def post_settings_keyboard():
    keyboard = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text='Изменить текст'), KeyboardButton(text='Изменить фото')],
        [KeyboardButton(text='Изменить видео'), KeyboardButton(text='Удалить медиа')],
        [KeyboardButton(text='Канальные посты'), KeyboardButton(text='Вернуться')]
    ], resize_keyboard=True)
    return keyboard


def get_chats_keyboard(page=0):
    chats = asyncio.get_event_loop().run_until_complete(user.get_chats()) if user else []
    per_page = 10
    start = page * per_page
    end = start + per_page
    page_chats = chats[start:end]
    
    keyboard = []
    for chat in page_chats:
        keyboard.append([
            InlineKeyboardButton(text=f'❌ {chat["title"]}', callback_data=f'DELETE_CHAT:{chat["id"]}'),
            InlineKeyboardButton(text=f'⚙️ {chat["title"]}', callback_data=f'EDIT_CHAT:{chat["id"]}')
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


def get_chat_settings_keyboard(chat_id):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='Изменить задержку', callback_data=f'CHANGE_TIMEOUT:{chat_id}')],
            [InlineKeyboardButton(text='Изменить пост', callback_data=f'EDIT_CHANNEL_POST:{chat_id}')],
            [InlineKeyboardButton(text='Доп. текст', callback_data=f'ADD_ADDITIONAL:{chat_id}')],
            [InlineKeyboardButton(text='❌ Удалить чат', callback_data=f'LFC:{chat_id}')]
        ]
    )
    return keyboard


def channel_post_keyboard():
    keyboard = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text='Список канальных постов'), KeyboardButton(text='Добавить канальный пост')],
        [KeyboardButton(text='Вернуться')]
    ], resize_keyboard=True)
    return keyboard


class login_phone(StatesGroup):
    phone = State()


class login_code(StatesGroup):
    code = State()


class login_password(StatesGroup):
    password = State()


class update_state(StatesGroup):
    confirm = State()


class addition(StatesGroup):
    id = State()


class post(StatesGroup):
    text = State()


class channel_post_text(StatesGroup):
    text = State()


class channel_post_photo(StatesGroup):
    photo = State()


class channel_post_video(StatesGroup):
    video = State()


class time(StatesGroup):
    timeout = State()


class channel_time(StatesGroup):
    id = State()
    timeout = State()


class add_chat_state(StatesGroup):
    id = State()


@router.message(Command("start"))
async def process_start_command(m: Message):
    if config and m.chat.id in config.ADMINS:
        version = get_version()
        await bot.send_message(
            m.chat.id,
            f"<b>Добро пожаловать!</b>\n\n"
            f"Версия скрипта: {version}\n\n"
            "Воспользуйтесь клавиатурой ниже для управления",
            reply_markup=welcome_keyboard()
        )
    else:
        await bot.send_message(m.chat.id, "Нет доступа")


@router.message(Command("login"))
async def login_command(m: Message):
    if config and m.chat.id in config.ADMINS:
        await do_login(m.chat.id)


@router.message(Command("update"))
async def update_command(m: Message):
    if config and m.chat.id in config.ADMINS:
        await update_menu(m.chat.id)


@router.message(F.text == 'Информация')
async def send_info(message: Message):
    version = get_version()
    latest = updater.get_latest_version() if updater else None
    
    if latest and version != latest:
        status = f"Доступна версия {latest}"
    else:
        status = "Актуально"
    
    await message.answer(
        f"Version: {version}\n"
        f"Status: {status}\n\n"
        f"Support: @support",
        parse_mode=ParseMode.HTML
    )


@router.message(F.text == 'Пост')
async def post_settings(message: Message):
    settings = db.settings()
    text_html = markdown_to_html(settings[2]) if settings[2] else ''
    try:
        photo_path = f'{config.DIR}{settings[1]}' if config else settings[1]
        video_path = f'{config.DIR}{settings[3]}' if config else settings[3]
        if os.path.exists(photo_path):
            await bot.send_photo(message.chat.id, photo_path, caption=text_html or ' ', parse_mode=ParseMode.HTML)
        elif os.path.exists(video_path):
            await bot.send_video(message.chat.id, video_path, caption=text_html or ' ', parse_mode=ParseMode.HTML)
        elif text_html:
            await bot.send_message(message.chat.id, text_html, parse_mode=ParseMode.HTML)
        else:
            await bot.send_message(message.chat.id, 'Нет поста', parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Ошибка отправки медиа: {e}")
        await bot.send_message(message.chat.id, 'Ошибка отправки поста')
    
    await message.answer('Настройки поста:', reply_markup=post_settings_keyboard())


@router.message(F.text == 'Запустить спам')
async def start_spam_cmd(message: Message):
    db.setSpam(1)
    keyboard = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text='Остановить спам')]
    ], resize_keyboard=True)
    await message.answer('Спам успешно запущен!', reply_markup=keyboard)
    await start_spam_loop()


@router.message(F.text == 'Остановить спам')
async def stop_spam_cmd(message: Message):
    db.setSpam(0)
    await message.answer('Отправляю последние сообщения и закругляюсь', reply_markup=welcome_keyboard())


@router.message(F.text == 'Интервал')
async def interval_settings(message: Message):
    settings = db.settings()
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='Изменить интервал', callback_data='INTERVAL')]
        ]
    )
    await message.answer(f'Текущий интервал: {settings[5]} минут(а)', reply_markup=keyboard, parse_mode=ParseMode.HTML)


@router.message(F.text == 'Настройки чатов')
async def chat_settings_menu(message: Message):
    chats = await user.get_chats() if user else []
    keyboard = get_chats_keyboard(0)
    await message.answer('Все доступные чаты:', reply_markup=keyboard)


@router.message(F.text == 'Канальные посты')
async def channel_posts_menu(message: Message):
    await message.answer('Управление канальными постами:', reply_markup=channel_post_keyboard())


@router.message(F.text == 'Список канальных постов')
async def list_channel_posts(message: Message):
    try:
        db.c.execute('SELECT CHANNEL, POST_PHOTO, POST_VIDEO, POST_TEXT FROM CHANNELS WHERE POST_PHOTO != "" OR POST_VIDEO != "" OR POST_TEXT != ""')
        posts = db.c.fetchall()
        if posts:
            text = 'Канальные посты:\n'
            for post in posts:
                text += f'Чат {post[0]}: {"Photo" if post[1] else ""} {"Video" if post[2] else ""} {"Text" if post[3] else ""}\n'
            await message.answer(text)
        else:
            await message.answer('Канальных постов нет.')
    except Exception as e:
        logger.error(f"Ошибка получения списка постов: {e}")
        await message.answer(f'Ошибка: {e}')


@router.message(F.text == 'Добавить канальный пост')
async def add_channel_post(message: Message):
    await message.answer('Введите ID канала:')


@router.message(F.text == 'Вернуться')
async def return_menu(message: Message):
    await message.answer('Возвращаю в главное меню:', reply_markup=welcome_keyboard())


@router.callback_query(F.data)
async def callback_handler(c: CallbackQuery, state: FSMContext):
    if 'CHATS_PAGE:' in c.data:
        page = int(c.data.split(':')[1])
        keyboard = get_chats_keyboard(page)
        await c.message.edit_reply_markup(reply_markup=keyboard)
        
    elif 'DELETE_CHAT:' in c.data:
        chat_id = int(c.data.split(':')[1])
        log = await user.leave_from_channel(chat_id) if user else False
        if log:
            await c.message.edit_text(f'Вы успешно покинули чат {chat_id}.')
            chats = await user.get_chats() if user else []
            keyboard = get_chats_keyboard(0)
            await c.message.edit_reply_markup(reply_markup=keyboard)
        else:
            await c.answer('Не удалось покинуть чат')
            
    elif 'EDIT_CHAT:' in c.data:
        chat_id = int(c.data.split(':')[1])
        keyboard = get_chat_settings_keyboard(chat_id)
        await c.message.edit_text(f'Настройки чата {chat_id}:', reply_markup=keyboard)
        
    elif 'ADD_CHAT' == c.data:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text='Ввести ID вручную', callback_data='INPUT_CHAT_ID')],
                [InlineKeyboardButton(text='Назад', callback_data='BACK_TO_CHATS')]
            ]
        )
        await c.message.edit_text('Выберите способ добавления чата:', reply_markup=keyboard)
        
    elif 'INPUT_CHAT_ID' == c.data:
        await c.message.edit_text('Отправьте ID чата для добавления:')
        await state.set_state(add_chat_state.id)
        
    elif 'BACK_TO_CHATS' == c.data:
        chats = await user.get_chats() if user else []
        keyboard = get_chats_keyboard(0)
        await c.message.edit_reply_markup(reply_markup=keyboard)
        
    elif 'LFC:' in c.data:
        chat_id = int(c.data.split(':')[1])
        log = await user.leave_from_channel(chat_id) if user else False
        if log:
            await c.message.edit_text(f'Вы успешно покинули чат {chat_id}.')
        else:
            await c.answer('Не удалось покинуть чат')
        await c.message.delete()
        
    elif 'CHANGE_TIMEOUT:' in c.data:
        chat_id = int(c.data.split(':')[1])
        await c.message.edit_text('Отправь мне интервал рассылки для этого чата (в минутах):')
        await state.set_data({'chat_id': chat_id})
        await state.set_state(channel_time.timeout)
        
    elif 'EDIT_CHANNEL_POST:' in c.data:
        chat_id = int(c.data.split(':')[1])
        await state.set_data({'chat_id': chat_id})
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text='Изменить текст', callback_data=f'CHANNEL_EDIT_TEXT:{chat_id}')],
                [InlineKeyboardButton(text='Изменить фото', callback_data=f'CHANNEL_EDIT_PHOTO:{chat_id}')],
                [InlineKeyboardButton(text='Изменить видео', callback_data=f'CHANNEL_EDIT_VIDEO:{chat_id}')],
                [InlineKeyboardButton(text='Очистить пост', callback_data=f'CHANNEL_CLEAR:{chat_id}')]
            ]
        )
        await c.message.edit_text(f'Редактирование канального поста для чата {chat_id}:', reply_markup=keyboard)
        
    elif 'CHANNEL_EDIT_TEXT:' in c.data:
        chat_id = int(c.data.split(':')[1])
        await state.set_data({'chat_id': chat_id})
        await c.message.edit_text('Введите текст канального поста (Markdown поддерживается):')
        await state.set_state(channel_post_text.text)
        
    elif 'CHANNEL_EDIT_PHOTO:' in c.data:
        chat_id = int(c.data.split(':')[1])
        await state.set_data({'chat_id': chat_id})
        await c.message.edit_text('Отправь фото для канального поста:')
        await state.set_state(channel_post_photo.photo)
        
    elif 'CHANNEL_EDIT_VIDEO:' in c.data:
        chat_id = int(c.data.split(':')[1])
        await state.set_data({'chat_id': chat_id})
        await c.message.edit_text('Отправь видео для канального поста:')
        await state.set_state(channel_post_video.video)
        
    elif 'CHANNEL_CLEAR:' in c.data:
        chat_id = int(c.data.split(':')[1])
        db.clear_channel_post(chat_id)
        await c.message.edit_text(f'Канальный пост для чата {chat_id} был очищен.')
        
    elif 'ADD_ADDITIONAL:' in c.data:
        chat_id = int(c.data.split(':')[1])
        await state.set_data({'chat_id': chat_id})
        await c.message.edit_text(f'Введите дополнительный текст для данного чата:')
        await state.set_state(addition.id)
        
    elif 'INTERVAL' == c.data:
        await c.message.edit_text('Отправь мне интервал рассылки по умолчанию (в минутах):')
        await state.set_state(time.timeout)
        
    elif 'update_confirm' == c.data:
        await state.set_state(update_state.confirm)
        await c.message.edit_text('Запускаю обновление...')
        if updater:
            result = updater.run_update()
            if result.get("error"):
                await c.message.edit_text(f'Ошибка: {result["error"]}')
            else:
                await c.message.edit_text(result["message"])
                await c.message.edit_text('Перезапустите скрипт для применения изменений.')
        else:
            await c.message.edit_text('Модуль обновления недоступен')
        await state.clear()
        
    elif 'update_cancel' == c.data:
        await c.message.edit_text('Обновление отменено')
        await state.clear()


@router.message(add_chat_state.id)
async def input_chat_id(m: Message, state: FSMContext):
    try:
        chat_id = int(m.text)
        db.add_channel(chat_id)
        await bot.send_message(m.chat.id, f'Чат {chat_id} успешно добавлен!')
        await state.clear()
    except Exception as e:
        logger.error(f"Ошибка добавления чата: {e}")
        await bot.send_message(m.chat.id, f'Ошибка: {e}')
        await state.clear()


@router.message(addition.id)
async def input_additional_text(m: Message, state: FSMContext):
    data = await state.get_data()
    chat_id = data.get('chat_id')
    try:
        if chat_id:
            db.add_additional_text(chat_id, m.text)
            await bot.send_message(m.chat.id, f'Дополнительный текст для чата {chat_id} обновлен!')
        else:
            await bot.send_message(m.chat.id, 'Не найден ID чата для обновления!')
    except Exception as e:
        logger.error(f"Ошибка добавления текста: {e}")
        await bot.send_message(m.chat.id, 'Ошибка при обновлении текста.')
    await state.clear()


@router.message(channel_post_text.text)
async def input_channel_post_text(m: Message, state: FSMContext):
    data = await state.get_data()
    chat_id = data.get('chat_id')
    try:
        if chat_id:
            db.set_channel_post(chat_id, text=markdown_to_html(m.text))
            await bot.send_message(m.chat.id, f'Текст канального поста обновлен!')
        else:
            await bot.send_message(m.chat.id, 'Не найден ID чата для обновления!')
    except Exception as e:
        logger.error(f"Ошибка добавления канального текста: {e}")
        await bot.send_message(m.chat.id, 'Ошибка при обновлении текста.')
    await state.clear()


@router.message(channel_post_photo.photo)
async def input_channel_post_photo(m: Message, state: FSMContext):
    data = await state.get_data()
    chat_id = data.get('chat_id')
    try:
        if chat_id:
            result = await m.photo[-1].download()
            db.set_channel_post(chat_id, photo=os.path.basename(result.name))
            await bot.send_message(m.chat.id, f'Фото канального поста обновлено!')
        else:
            await bot.send_message(m.chat.id, 'Не найден ID чата для обновления!')
    except Exception as e:
        logger.error(f"Ошибка добавления канального фото: {e}")
        await bot.send_message(m.chat.id, 'Ошибка при обновлении фото.')
    await state.clear()


@router.message(channel_post_video.video)
async def input_channel_post_video(m: Message, state: FSMContext):
    data = await state.get_data()
    chat_id = data.get('chat_id')
    try:
        if chat_id:
            result = await m.video.download()
            db.set_channel_post(chat_id, video=os.path.basename(result.name))
            await bot.send_message(m.chat.id, f'Видео канального поста обновлено!')
        else:
            await bot.send_message(m.chat.id, 'Не найден ID чата для обновления!')
    except Exception as e:
        logger.error(f"Ошибка добавления канального видео: {e}")
        await bot.send_message(m.chat.id, 'Ошибка при обновлении видео.')
    await state_clear()


@router.message(time.timeout)
async def input_timeout(m: Message, state: FSMContext):
    try:
        timeout = int(m.text)
        if timeout > 1:
            db.setTimeOut(timeout)
            await bot.send_message(m.chat.id, f'Интервал рассылки обновлен: {timeout} минут')
        else:
            await bot.send_message(m.chat.id, 'Введите число больше 1.')
    except ValueError:
        await bot.send_message(m.chat.id, 'Введите число.')
    except Exception as e:
        logger.error(f"Ошибка установки таймаута: {e}")
        await bot.send_message(m.chat.id, 'Ошибка при обновлении таймаута.')
    await state.clear()


@router.message(channel_time.timeout)
async def input_channel_timeout(m: Message, state: FSMContext):
    data = await state.get_data()
    chat_id = data.get('chat_id')
    try:
        timeout = int(m.text)
        if timeout > 1:
            if chat_id:
                db.set_channel_timeout(chat_id, timeout)
                await bot.send_message(m.chat.id, f'Интервал для чата {chat_id} обновлен: {timeout} минут')
            else:
                await bot.send_message(m.chat.id, 'Не найден ID чата для обновления!')
        else:
            await bot.send_message(m.chat.id, 'Введите число больше 1.')
    except ValueError:
        await bot.send_message(m.chat.id, 'Введите число.')
    except Exception as e:
        logger.error(f"Ошибка установки таймаута канала: {e}")
        await bot.send_message(m.chat.id, 'Ошибка при обновлении таймаута.')
    await state_clear()


@router.message(F.text)
async def echo_message(m: Message):
    pass


@router.message(F.photo)
async def download_photo(m: Message):
    result = await m.photo[-1].download()
    db.change_photo(os.path.basename(result.name))
    await bot.send_message(m.chat.id, 'Фото было успешно обновлено.')


@router.message(F.video)
async def download_video(m: Message):
    result = await m.video.download()
    db.change_video(os.path.basename(result.name))
    await bot.send_message(m.chat.id, 'Видео было успешно обновлено.')


@router.message(login_phone.phone)
async def handle_phone_input(m: Message, state: FSMContext):
    phone = m.text.strip()
    if re.match(r'^\+?[0-9]+$', phone):
        user.login_phone = phone
        user.current_code[m.chat.id] = ""
        
        try:
            await m.delete()
        except:
            pass
        
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text='1', callback_data='code_1'),
                    InlineKeyboardButton(text='2', callback_data='code_2'),
                    InlineKeyboardButton(text='3', callback_data='code_3')
                ],
                [
                    InlineKeyboardButton(text='4', callback_data='code_4'),
                    InlineKeyboardButton(text='5', callback_data='code_5'),
                    InlineKeyboardButton(text='6', callback_data='code_6')
                ],
                [
                    InlineKeyboardButton(text='7', callback_data='code_7'),
                    InlineKeyboardButton(text='8', callback_data='code_8'),
                    InlineKeyboardButton(text='9', callback_data='code_9')
                ],
                [
                    InlineKeyboardButton(text='⌫', callback_data='code_back'),
                    InlineKeyboardButton(text='0', callback_data='code_0'),
                    InlineKeyboardButton(text='Войти', callback_data='code_enter')
                ]
            ]
        )
        
        await m.answer(f'Телефон: {phone}')
        msg = await m.answer('Код: ', reply_markup=keyboard)
        user.code_messages[m.chat.id] = msg.message_id
        await state.set_state(login_code.code)
    else:
        await m.answer('Неверный формат телефона. Попробуйте снова:')


@router.message(login_code.code)
async def handle_code_input(m: Message, state: FSMContext):
    code = m.text.strip()
    if code.isdigit() and 4 <= len(code) <= 6:
        if user and user.login_phone:
            from pyrogram import Client
            client = Client("session", config.API_ID, config.API_HASH, workdir=".") if config else Client("session", 0, "", workdir=".")
            try:
                async with client:
                    await client.sign_in(user.login_phone, code)
                await bot.send_message(config.ADMINS[0], "Успешный вход!") if config else None
                user.current_code[m.chat.id] = ""
                await state.clear()
            except Exception as e:
                await m.answer(f'Ошибка: {e}')
        else:
            await m.answer('Сначала введите телефон.')
    else:
        await m.answer('Код должен содержать 4-6 цифр. Попробуйте снова:')


@router.message(login_password.password)
async def handle_password_input(m: Message, state: FSMContext):
    password = m.text
    user.login_password = password
    
    try:
        await m.delete()
    except:
        pass
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='Подтвердить', callback_data='password_enter')],
            [InlineKeyboardButton(text='Отмена', callback_data='password_cancel')]
        ]
    )
    
    await m.answer(f'Введенный пароль: {password}', reply_markup=keyboard)


@router.callback_query(F.data.startswith('code_'))
async def handle_code_button(c: CallbackQuery, state: FSMContext):
    code_part = c.data.split('_')[1]
    current_code_val = user.current_code.get(c.message.chat.id, "")
    
    if code_part == 'back':
        if current_code_val:
            user.current_code[c.message.chat.id] = current_code_val[:-1]
            new_code = user.current_code[c.message.chat.id]
            await c.message.edit_text(f'Код: {new_code}')
        else:
            await c.answer("Код пустой", show_alert=True)
    elif code_part == 'enter':
        if current_code_val and user.login_phone:
            from pyrogram import Client
            client = Client("session", config.API_ID, config.API_HASH, workdir=".") if config else Client("session", 0, "", workdir=".")
            try:
                async with client:
                    await client.sign_in(user.login_phone, current_code_val)
                await bot.send_message(config.ADMINS[0], "Успешный вход!") if config else None
                user.current_code[c.message.chat.id] = ""
                await state.clear()
            except Exception as e:
                await c.answer(f"Ошибка: {e}", show_alert=True)
        else:
            await c.answer("Введите код", show_alert=True)
    else:
        if len(current_code_val) < 6:
            user.current_code[c.message.chat.id] = current_code_val + code_part
            new_code = user.current_code[c.message.chat.id]
            await c.message.edit_text(f'Код: {new_code}')
        else:
            await c.answer("Код полный", show_alert=True)


@router.callback_query(F.data == 'password_enter')
async def handle_password_confirm(c: CallbackQuery):
    if user and user.login_password and user.login_phone:
        from pyrogram import Client
        client = Client("session", config.API_ID, config.API_HASH, workdir=".") if config else Client("session", 0, "", workdir=".")
        try:
            async with client:
                await client.check_password(user.login_password)
            await bot.send_message(config.ADMINS[0], "Успешный вход!") if config else None
            user.current_code[c.message.chat.id] = ""
            await c.message.delete()
        except Exception as e:
            await c.answer(f"Ошибка: {e}", show_alert=True)
    else:
        await c.answer("Сначала введите пароль", show_alert=True)


@router.callback_query(F.data == 'password_cancel')
async def handle_password_cancel(c: CallbackQuery):
    await c.message.delete()
    await c.answer("Вход отменен")


async def do_login(chat_id):
    if os.path.exists("session.session"):
        os.remove("session.session")
        logger.info("Удалена старая сессия")
    
    msg = await bot.send_message(chat_id, 'Введите номер телефона (в формате +1234567890):')
    await asyncio.sleep(60)
    try:
        await msg.delete()
    except:
        pass


async def update_menu(chat_id):
    if not updater:
        await bot.send_message(chat_id, "Модуль обновления недоступен")
        return
    
    check = updater.check_update()
    
    if check.get("error"):
        await bot.send_message(chat_id, f"Ошибка: {check['error']}")
        return
    
    if check["update_available"]:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text='Обновить', callback_data='update_confirm')],
                [InlineKeyboardButton(text='Отмена', callback_data='update_cancel')]
            ]
        )
        await bot.send_message(
            chat_id,
            f"Доступно обновление!\n"
            f"Текущая: {check['current']}\n"
            f"Последняя: {check['latest']}\n\n"
            "Хотите обновить?",
            reply_markup=keyboard
        )
    else:
        await bot.send_message(chat_id, f"Актуальная версия: {check['current']}")


async def start_spam_loop():
    settings = db.settings()
    if settings[4] == 1:
        spam_list = []
        for i in await user.get_chats():
            try:
                addit_text = db.get_additional_text(i['id'])
                if addit_text and addit_text[0]:
                    i['text'] = addit_text[0]
                else:
                    i['text'] = ''
            except Exception as e:
                logger.error(f"Ошибка получения дополнительного текста для {i['id']}: {e}")
                i['text'] = ''
            spam_list.append(i)
        
        if not spam_list:
            await bot.send_message(config.ADMINS[0], 'Нет доступных каналов для рассылки') if config else None
            return
        
        settings = db.settings()
        asyncio.create_task(user.spamming(spam_list, settings, db))


async def main():
    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())
