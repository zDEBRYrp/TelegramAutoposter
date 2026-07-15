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
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text='Запустить спам'), KeyboardButton(text='Пост')],
        [KeyboardButton(text='Настройки чатов'), KeyboardButton(text='Информация')],
        [KeyboardButton(text='Обновление')]
    ], resize_keyboard=True)


def post_settings_keyboard():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text='Изменить текст'), KeyboardButton(text='Изменить фото')],
        [KeyboardButton(text='Изменить видео'), KeyboardButton(text='Удалить медиа')],
        [KeyboardButton(text='Канальные посты'), KeyboardButton(text='Вернуться')]
    ], resize_keyboard=True)


def channel_post_keyboard():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text='Список канальных постов'), KeyboardButton(text='Добавить канальный пост')],
        [KeyboardButton(text='Вернуться')]
    ], resize_keyboard=True)


def get_chat_settings_keyboard(chat_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Изменить задержку', callback_data=f'CHANGE_TIMEOUT:{chat_id}')],
        [InlineKeyboardButton(text='Изменить пост', callback_data=f'EDIT_CHANNEL_POST:{chat_id}')],
        [InlineKeyboardButton(text='Доп. текст', callback_data=f'ADD_ADDITIONAL:{chat_id}')],
        [InlineKeyboardButton(text='❌ Удалить чат', callback_data=f'LFC:{chat_id}')]
    ])


async def get_chats_keyboard(page=0):
    chats = await user.get_chats() if user else []
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
        await bot.send_message(m.chat.id,
            f"<b>Добро пожаловать!</b>\n\nВерсия скрипта: {get_version()}\n\nВоспользуйтесь клавиатурой ниже для управления",
            reply_markup=welcome_keyboard())
    else:
        await bot.send_message(m.chat.id, "Нет доступа")

@router.message(Command("login"))
async def login_command(m: Message, state: FSMContext):
    if config and m.chat.id in config.ADMINS:
        await do_login(m.chat.id)
        await state.set_state(login_phone.phone)

@router.message(Command("update"))
async def update_command(m: Message):
    if config and m.chat.id in config.ADMINS:
        await do_update_menu(m.chat.id)

@router.message(F.text == 'Информация')
async def send_info(message: Message):
    version = get_version()
    latest = updater.get_latest_version() if updater else None
    status = f"Доступна версия {latest}" if latest and version != latest else "Актуально"
    await message.answer(f"Version: {version}\nStatus: {status}\n\nSupport: @support")

@router.message(F.text == 'Обновление')
async def update_btn(message: Message):
    await do_update_menu(message.chat.id)

@router.message(F.text == 'Вернуться')
async def return_menu(message: Message):
    await message.answer('Главное меню:', reply_markup=welcome_keyboard())

@router.message(F.text == 'Пост')
async def post_settings(message: Message):
    settings = db.settings()
    text_html = markdown_to_html(settings[2]) if settings[2] else ''
    try:
        photo_path = f'{config.DIR}{settings[1]}' if config and config.DIR else settings[1]
        video_path = f'{config.DIR}{settings[3]}' if config and config.DIR else settings[3]
        if settings[1] and os.path.exists(photo_path):
            await bot.send_photo(message.chat.id, photo_path, caption=text_html or ' ')
        elif settings[3] and os.path.exists(video_path):
            await bot.send_video(message.chat.id, video_path, caption=text_html or ' ')
        elif text_html:
            await bot.send_message(message.chat.id, text_html)
        else:
            await bot.send_message(message.chat.id, 'Пост пуст')
    except Exception as e:
        logger.error(f"Ошибка отправки поста: {e}")
        await bot.send_message(message.chat.id, 'Ошибка отправки поста')

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Изменить текст', callback_data='EDIT_TEXT')],
        [InlineKeyboardButton(text='Изменить фото', callback_data='EDIT_PHOTO'),
         InlineKeyboardButton(text='Изменить видео', callback_data='EDIT_VIDEO')],
        [InlineKeyboardButton(text='Удалить медиа', callback_data='DEL_MEDIA')],
        [InlineKeyboardButton(text='Интервал по умолчанию', callback_data='INTERVAL')]
    ])
    await message.answer('Настройки глобального поста:', reply_markup=keyboard)

@router.message(F.text == 'Запустить спам')
async def start_spam_cmd(message: Message):
    db.setSpam(1)
    await message.answer('Спам успешно запущен!', reply_markup=ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text='Остановить спам')]], resize_keyboard=True))
    await start_spam_loop()

@router.message(F.text == 'Остановить спам')
async def stop_spam_cmd(message: Message):
    db.setSpam(0)
    await message.answer('Останавливаю...', reply_markup=welcome_keyboard())

@router.message(F.text == 'Настройки чатов')
async def chat_settings_menu(message: Message):
    keyboard = await get_chats_keyboard(0)
    await message.answer('Доступные чаты:', reply_markup=keyboard)

@router.message(F.text == 'Канальные посты')
async def channel_posts_menu(message: Message):
    await message.answer('Управление канальными постами:', reply_markup=channel_post_keyboard())

@router.message(F.text == 'Список канальных постов')
async def list_channel_posts(message: Message):
    try:
        db.c.execute('SELECT CHANNEL, POST_PHOTO, POST_VIDEO, POST_TEXT FROM CHANNELS WHERE POST_PHOTO != "" OR POST_VIDEO != "" OR POST_TEXT != ""')
        posts = db.c.fetchall()
        if posts:
            text = 'Канальные посты:\n' + '\n'.join(
                f'• Чат {p[0]}: {"📷" if p[1] else ""} {"📹" if p[2] else ""} {"📝" if p[3] else ""}' for p in posts)
            await message.answer(text)
        else:
            await message.answer('Канальных постов нет.')
    except Exception as e:
        await message.answer(f'Ошибка: {e}')


@router.callback_query(F.data)
async def callback_handler(c: CallbackQuery, state: FSMContext):
    data = c.data

    if data.startswith('CHATS_PAGE:'):
        page = int(data.split(':')[1])
        keyboard = await get_chats_keyboard(page)
        await c.message.edit_reply_markup(reply_markup=keyboard)

    elif data.startswith('DELETE_CHAT:'):
        chat_id = int(data.split(':')[1])
        log = await user.leave_from_channel(chat_id) if user else False
        if log:
            keyboard = await get_chats_keyboard(0)
            await c.message.edit_text('Вы успешно покинули чат.', reply_markup=keyboard)
        else:
            await c.answer('Не удалось покинуть чат', show_alert=True)

    elif data.startswith('EDIT_CHAT:'):
        chat_id = int(data.split(':')[1])
        addit = db.get_additional_text(chat_id)
        addit_val = addit[0] if addit and addit[0] else 'не задан'
        post_data = db.get_channel_post(chat_id)
        has_post = post_data and (post_data[0] or post_data[1] or post_data[2])
        info = f'Чат: {chat_id}\nДоп. текст: {addit_val}\nИндив. пост: {"есть" if has_post else "нет"}'
        await c.message.edit_text(info, reply_markup=get_chat_settings_keyboard(chat_id))

    elif data == 'ADD_CHAT':
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='Ввести ID вручную', callback_data='INPUT_CHAT_ID')],
            [InlineKeyboardButton(text='Назад', callback_data='BACK_TO_CHATS')]
        ])
        await c.message.edit_text('Выберите способ добавления чата:', reply_markup=keyboard)

    elif data == 'INPUT_CHAT_ID':
        await c.message.edit_text('Отправьте ID чата:')
        await state.set_state(add_chat_state.id)

    elif data == 'BACK_TO_CHATS':
        keyboard = await get_chats_keyboard(0)
        await c.message.edit_text('Доступные чаты:', reply_markup=keyboard)

    elif data.startswith('LFC:'):
        chat_id = int(data.split(':')[1])
        log = await user.leave_from_channel(chat_id) if user else False
        if log:
            await c.message.edit_text(f'Вы покинули чат {chat_id}.')
        else:
            await c.answer('Не удалось покинуть чат', show_alert=True)

    elif data.startswith('CHANGE_TIMEOUT:'):
        chat_id = int(data.split(':')[1])
        await state.set_data({'chat_id': chat_id})
        await c.message.edit_text('Введите интервал для этого чата (в минутах):')
        await state.set_state(channel_time.timeout)

    elif data.startswith('ADD_ADDITIONAL:'):
        chat_id = int(data.split(':')[1])
        await state.set_data({'chat_id': chat_id})
        await c.message.edit_text('Введите дополнительный текст для чата:')
        await state.set_state(addition.id)

    elif data.startswith('EDIT_CHANNEL_POST:'):
        chat_id = int(data.split(':')[1])
        await state.set_data({'chat_id': chat_id})
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='Текст', callback_data=f'CHANNEL_EDIT_TEXT:{chat_id}')],
            [InlineKeyboardButton(text='Фото', callback_data=f'CHANNEL_EDIT_PHOTO:{chat_id}'),
             InlineKeyboardButton(text='Видео', callback_data=f'CHANNEL_EDIT_VIDEO:{chat_id}')],
            [InlineKeyboardButton(text='Очистить пост', callback_data=f'CHANNEL_CLEAR:{chat_id}')],
            [InlineKeyboardButton(text='Назад', callback_data=f'EDIT_CHAT:{chat_id}')]
        ])
        await c.message.edit_text(f'Редактирование поста чата {chat_id}:', reply_markup=keyboard)

    elif data.startswith('CHANNEL_EDIT_TEXT:'):
        chat_id = int(data.split(':')[1])
        await state.set_data({'chat_id': chat_id})
        await c.message.edit_text('Введите текст канального поста (Markdown поддерживается):')
        await state.set_state(channel_post_text.text)

    elif data.startswith('CHANNEL_EDIT_PHOTO:'):
        chat_id = int(data.split(':')[1])
        await state.set_data({'chat_id': chat_id})
        await c.message.edit_text('Отправь фото для канального поста:')
        await state.set_state(channel_post_photo.photo)

    elif data.startswith('CHANNEL_EDIT_VIDEO:'):
        chat_id = int(data.split(':')[1])
        await state.set_data({'chat_id': chat_id})
        await c.message.edit_text('Отправь видео для канального поста:')
        await state.set_state(channel_post_video.video)

    elif data.startswith('CHANNEL_CLEAR:'):
        chat_id = int(data.split(':')[1])
        db.clear_channel_post(chat_id)
        await c.message.edit_text(f'Канальный пост для чата {chat_id} очищен.')

    elif data == 'EDIT_TEXT':
        await c.message.edit_text('Введите текст глобального поста:')
        await state.set_state(post.text)

    elif data == 'EDIT_PHOTO':
        await c.message.edit_text('Отправь фото для глобального поста:')

    elif data == 'EDIT_VIDEO':
        await c.message.edit_text('Отправь видео для глобального поста:')

    elif data == 'DEL_MEDIA':
        db.change_photo('')
        db.change_video('')
        await c.message.edit_text('Медиа удалено.')

    elif data == 'INTERVAL':
        settings = db.settings()
        await c.message.edit_text(f'Текущий интервал: {settings[5]} мин.\nВведите новый интервал:')
        await state.set_state(time.timeout)

    elif data == 'PAGINATION':
        await c.answer()

    elif data == 'update_confirm':
        await c.message.edit_text('Запускаю обновление...')
        if updater:
            result = updater.run_update()
            msg = result.get("error") or result.get("message", "Готово")
            await c.message.edit_text(f'{msg}\n\nПерезапустите скрипт для применения.')
        else:
            await c.message.edit_text('Модуль обновления недоступен')
        await state.clear()

    elif data == 'update_cancel':
        await c.message.edit_text('Обновление отменено')
        await state.clear()


@router.message(add_chat_state.id)
async def input_chat_id(m: Message, state: FSMContext):
    try:
        chat_id = int(m.text.strip())
        await m.delete()
        db.add_channel(chat_id)
        await bot.send_message(m.chat.id, f'Чат {chat_id} успешно добавлен!')
    except ValueError:
        await bot.send_message(m.chat.id, 'ID должен быть числом.')
    except Exception as e:
        await bot.send_message(m.chat.id, f'Ошибка: {e}')
    finally:
        await state.clear()


@router.message(addition.id)
async def input_additional_text(m: Message, state: FSMContext):
    data = await state.get_data()
    chat_id = data.get('chat_id')
    try:
        await m.delete()
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
    try:
        await m.delete()
        db.change_text(markdown_to_html(m.text))
        await bot.send_message(m.chat.id, 'Текст глобального поста обновлен!')
    except Exception as e:
        await bot.send_message(m.chat.id, f'Ошибка: {e}')
    finally:
        await state.clear()


@router.message(channel_post_text.text)
async def input_channel_post_text(m: Message, state: FSMContext):
    data = await state.get_data()
    chat_id = data.get('chat_id')
    try:
        await m.delete()
        if chat_id:
            db.set_channel_post(chat_id, text=markdown_to_html(m.text))
            await bot.send_message(m.chat.id, 'Текст канального поста обновлен!')
        else:
            await bot.send_message(m.chat.id, 'Не найден ID чата.')
    except Exception as e:
        await bot.send_message(m.chat.id, f'Ошибка: {e}')
    finally:
        await state.clear()


@router.message(channel_post_photo.photo)
async def input_channel_post_photo(m: Message, state: FSMContext):
    data = await state.get_data()
    chat_id = data.get('chat_id')
    try:
        if chat_id:
            result = await m.photo[-1].download()
            db.set_channel_post(chat_id, photo=os.path.basename(result.name))
            await bot.send_message(m.chat.id, 'Фото канального поста обновлено!')
        else:
            await bot.send_message(m.chat.id, 'Не найден ID чата.')
    except Exception as e:
        await bot.send_message(m.chat.id, f'Ошибка: {e}')
    finally:
        await state.clear()


@router.message(channel_post_video.video)
async def input_channel_post_video(m: Message, state: FSMContext):
    data = await state.get_data()
    chat_id = data.get('chat_id')
    try:
        if chat_id:
            result = await m.video.download()
            db.set_channel_post(chat_id, video=os.path.basename(result.name))
            await bot.send_message(m.chat.id, 'Видео канального поста обновлено!')
        else:
            await bot.send_message(m.chat.id, 'Не найден ID чата.')
    except Exception as e:
        await bot.send_message(m.chat.id, f'Ошибка: {e}')
    finally:
        await state.clear()


@router.message(time.timeout)
async def input_timeout(m: Message, state: FSMContext):
    try:
        await m.delete()
        timeout = int(m.text.strip())
        if timeout > 1:
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
    data = await state.get_data()
    chat_id = data.get('chat_id')
    try:
        await m.delete()
        timeout = int(m.text.strip())
        if timeout > 1:
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


@router.message(F.photo)
async def download_photo(m: Message):
    result = await m.photo[-1].download()
    db.change_photo(os.path.basename(result.name))
    await bot.send_message(m.chat.id, 'Фото глобального поста обновлено.')


@router.message(F.video)
async def download_video(m: Message):
    result = await m.video.download()
    db.change_video(os.path.basename(result.name))
    await bot.send_message(m.chat.id, 'Видео глобального поста обновлено.')


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

        await m.answer('Отправляю код...')
        result = await user.do_login(phone)
        if result is None:
            await m.answer('Ошибка отправки кода. Проверьте номер телефона.')
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
        msg = await m.answer('Введите код из Telegram:\nКод: ', reply_markup=keyboard)
        user.code_messages[m.chat.id] = msg.message_id
        await state.set_state(login_code.code)
    else:
        await m.answer('Неверный формат. Пример: +79001234567')


@router.callback_query(F.data.startswith('code_'))
async def handle_code_button(c: CallbackQuery, state: FSMContext):
    code_part = c.data.split('_')[1]
    current_code_val = user.current_code.get(c.message.chat.id, "")

    if code_part == 'back':
        if current_code_val:
            user.current_code[c.message.chat.id] = current_code_val[:-1]
            await c.message.edit_text(f'Код: {user.current_code[c.message.chat.id]}',
                                       reply_markup=c.message.reply_markup)
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
            elif result.get("need_password"):
                hint = result.get("hint", "")
                hint_text = f"\nПодсказка: {hint}" if hint else ""
                await c.message.edit_text(
                    f"Требуется пароль двухфакторной аутентификации.{hint_text}\n\nВведите пароль:"
                )
                await state.set_state(login_password.password)
            else:
                await c.answer(f"Ошибка: {result.get('error')}", show_alert=True)
                await c.message.edit_text('Код: ', reply_markup=c.message.reply_markup)
        else:
            await c.answer("Введите код", show_alert=True)
    else:
        if len(current_code_val) < 6:
            user.current_code[c.message.chat.id] = current_code_val + code_part
            await c.message.edit_text(f'Код: {user.current_code[c.message.chat.id]}',
                                       reply_markup=c.message.reply_markup)
        else:
            await c.answer("Максимум 6 цифр")


@router.message(login_code.code)
async def handle_code_text(m: Message, state: FSMContext):
    await m.delete()
    await m.answer('Используйте кнопки для ввода кода.')


@router.message(login_password.password)
async def handle_password_input(m: Message, state: FSMContext):
    try:
        await m.delete()
    except:
        pass
    user.login_password = m.text
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Подтвердить', callback_data='password_enter')],
        [InlineKeyboardButton(text='Отмена', callback_data='password_cancel')]
    ])
    await m.answer('Пароль получен. Подтвердить?', reply_markup=keyboard)


@router.callback_query(F.data == 'password_enter')
async def handle_password_confirm(c: CallbackQuery):
    if user and user.login_password:
        result = await user.do_check_password(user.login_password)
        if result.get("ok"):
            await c.message.edit_text("Вход выполнен успешно!")
        else:
            await c.answer(f"Ошибка: {result.get('error')}", show_alert=True)
    else:
        await c.answer("Сначала введите пароль", show_alert=True)


@router.callback_query(F.data == 'password_cancel')
async def handle_password_cancel(c: CallbackQuery):
    await c.message.edit_text("Вход отменен.")


@router.message(F.text)
async def echo_message(m: Message):
    pass


async def do_login(chat_id):
    await user._delete_session()
    await bot.send_message(chat_id, 'Введите номер телефона (в формате +79001234567):')
    await bot.get_updates


async def do_update_menu(chat_id):
    if not updater:
        await bot.send_message(chat_id, "Модуль обновления недоступен")
        return
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


async def start_spam_loop():
    settings = db.settings()
    if settings[4] != 1:
        return
    spam_list = []
    for i in await user.get_chats():
        try:
            addit_text = db.get_additional_text(i['id'])
            i['text'] = addit_text[0] if addit_text and addit_text[0] else ''
        except Exception as e:
            logger.error(f"Ошибка получения доп. текста {i['id']}: {e}")
            i['text'] = ''
        spam_list.append(i)

    if not spam_list:
        if config:
            await bot.send_message(config.ADMINS[0], 'Нет доступных каналов для рассылки')
        return

    asyncio.create_task(user.spamming(spam_list, db.settings(), db))


async def main():
    # Запускаем Pyrogram клиент при старте
    if user:
        connected = await user.start_client()
        if not connected:
            logger.warning("Pyrogram не подключен. Используй /login для входа.")
    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())
