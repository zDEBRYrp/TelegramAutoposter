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
import config, user
from sqliter import DBConnection, markdown_to_html

router = Router()

from aiogram.client.default import DefaultBotProperties
bot = Bot(token=config.TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
dp.include_router(router)
db = DBConnection()

user.init_bot(bot)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def welcome_keyboard():
    keyboard = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text='📤 Запустить спам'), KeyboardButton(text='📝 Пост')],
        [KeyboardButton(text='💬 Доступные чаты'), KeyboardButton(text='🕒 Интервал')],
        [KeyboardButton(text='💡 Информация')]
    ], resize_keyboard=True)
    return keyboard


class login_phone(StatesGroup):
    phone = State()


class login_code(StatesGroup):
    code = State()


class login_password(StatesGroup):
    password = State()


@router.message(Command("start"))
async def process_start_command(m: Message):
    if m.chat.id in config.ADMINS:
        await bot.send_message(
            m.chat.id,
            "<b>👋 Добро пожаловать!</b>\n\n"
            "• Версия скрипта: 4.0.0 build\n\n"
            "Воспользуйтесь клавиатурой ниже для управления",
            reply_markup=welcome_keyboard()
        )
    else:
        await bot.send_message(m.chat.id, "❌")


@router.message(Command("login"))
async def login_command(m: Message):
    """Команда входа"""
    if m.chat.id in config.ADMINS:
        await do_login(m.chat.id)


@router.message(F.text == '💡 Информация')
async def send_info(message: Message):
    await message.answer(
        "💾 <b>Version:</b> 4.0.0 build\n"
        "💿 <b>Last Update:</b> 2026\n\n"
        "☎️ <b>Техническая поддержка:</b> @support",
        parse_mode=ParseMode.HTML
    )


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


@router.message(addition.id)
async def input_report(m: Message, state: FSMContext):
    data = await state.get_data()
    channel_id = data.get('channel_id')
    try:
        if channel_id:
            db.add_additional_text(channel_id, m.text)
            await bot.send_message(m.chat.id, f'☑️ Текст для данного чата был успешно обновлен!')
        else:
            await bot.send_message(m.chat.id, f'❌ Не найден ID чата для обновления!')
    except Exception as e:
        logger.error(f"Ошибка добавления текста: {e}")
        await bot.send_message(m.chat.id, f'❌ Текст для данного чата не был обновлен!')
    await state.clear()


@router.message(post.text)
async def input_post_text(m: Message, state: FSMContext):
    db.change_text(markdown_to_html(m.text))
    await bot.send_message(m.chat.id, f'☑️ Текст для поста был обновлен.')
    await state.clear()


@router.message(channel_post_text.text)
async def input_channel_post_text(m: Message, state: FSMContext):
    data = await state.get_data()
    channel_id = data.get('channel_id')
    try:
        if channel_id:
            db.set_channel_post(channel_id, text=markdown_to_html(m.text))
            await bot.send_message(m.chat.id, f'☑️ Текст для канального поста был обновлен!')
        else:
            await bot.send_message(m.chat.id, f'❌ Не найден ID чата для обновления!')
    except Exception as e:
        logger.error(f"Ошибка добавления канального текста: {e}")
        await bot.send_message(m.chat.id, f'❌ Ошибка при обновлении текста.')
    await state.clear()


@router.message(channel_post_photo.photo)
async def input_channel_post_photo(m: Message, state: FSMContext):
    data = await state.get_data()
    channel_id = data.get('channel_id')
    try:
        if channel_id:
            result = await m.photo[-1].download()
            db.set_channel_post(channel_id, photo=os.path.basename(result.name))
            await bot.send_message(m.chat.id, f'☑️ Фото для канального поста было обновлено!')
        else:
            await bot.send_message(m.chat.id, f'❌ Не найден ID чата для обновления!')
    except Exception as e:
        logger.error(f"Ошибка добавления канального фото: {e}")
        await bot.send_message(m.chat.id, f'❌ Ошибка при обновлении фото.')
    await state.clear()


@router.message(channel_post_video.video)
async def input_channel_post_video(m: Message, state: FSMContext):
    data = await state.get_data()
    channel_id = data.get('channel_id')
    try:
        if channel_id:
            result = await m.video.download()
            db.set_channel_post(channel_id, video=os.path.basename(result.name))
            await bot.send_message(m.chat.id, f'☑️ Видео для канального поста было обновлено!')
        else:
            await bot.send_message(m.chat.id, f'❌ Не найден ID чата для обновления!')
    except Exception as e:
        logger.error(f"Ошибка добавления канального видео: {e}")
        await bot.send_message(m.chat.id, f'❌ Ошибка при обновлении видео.')
    await state.clear()


@router.message(time.timeout)
async def input_timeout(m: Message, state: FSMContext):
    try:
        timeout = int(m.text)
        if timeout > 1:
            db.setTimeOut(timeout)
            await bot.send_message(m.chat.id, f'🕒 Интервал рассылки был успешно обновлен.')
        else:
            await bot.send_message(m.chat.id, f'❌ Введите число больше 1.')
    except ValueError:
        await bot.send_message(m.chat.id, f'❌ Введите число.')
    except Exception as e:
        logger.error(f"Ошибка установки таймаута: {e}")
        await bot.send_message(m.chat.id, f'❌ Ошибка при обновлении таймаута.')
    await state.clear()


@router.message(channel_time.timeout)
async def input_channel_timeout(m: Message, state: FSMContext):
    data = await state.get_data()
    channel_id = data.get('channel_id')
    try:
        timeout = int(m.text)
        if timeout > 1:
            if channel_id:
                db.set_channel_timeout(channel_id, timeout)
                await bot.send_message(m.chat.id, f'🕒️ Интервал рассылки для этого чата был успешно обновлен.')
            else:
                await bot.send_message(m.chat.id, f'❌ Не найден ID чата для обновления!')
        else:
            await bot.send_message(m.chat.id, f'❌ Введите число больше 1.')
    except ValueError:
        await bot.send_message(m.chat.id, f'❌ Введите число.')
    except Exception as e:
        logger.error(f"Ошибка установки таймаута канала: {e}")
        await bot.send_message(m.chat.id, f'❌ Ошибка при обновлении таймаута.')
    await state.clear()


@router.message(F.text)
async def echo_message(m: Message):
    if m.text == '💬 Доступные чаты':
        chats = await user.get_chats()
        if not chats:
            await bot.send_message(m.chat.id, '💬 Нет доступных чатов.')
            return
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=f'{_["title"]}', callback_data=f'EDIT_ID:{_["id"]}')]
                for _ in chats
            ]
        )
        await bot.send_message(m.chat.id, '💬 Все доступные чаты:', reply_markup=keyboard)
        
    elif m.text == '📤 Запустить спам':
        db.setSpam(1)
        keyboard = ReplyKeyboardMarkup(keyboard=[
            [KeyboardButton(text='🛑 Остановить спам')]
        ], resize_keyboard=True)
        await bot.send_message(m.chat.id, '🕹 Спам успешно запущен!', reply_markup=keyboard)
        await start_spam()
        
    elif m.text == '🛑 Остановить спам':
        db.setSpam(0)
        await bot.send_message(m.chat.id, '🗳 Отправляю последние сообщения и закругляюсь', reply_markup=welcome_keyboard())
        
    elif m.text == '🕒 Интервал':
        settings = db.settings()
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text='🕘 Изменить интервал', callback_data='INTERVAL')]
            ]
        )
        await bot.send_message(m.chat.id, f'🔃 <b>Текущий интервал:</b> {settings[5]} минут(а)', reply_markup=keyboard, parse_mode=ParseMode.HTML)

    elif m.text == '📝 Пост':
        settings = db.settings()
        text_html = markdown_to_html(settings[2]) if settings[2] else ''
        try:
            photo_path = f'{config.DIR}{settings[1]}' if config.DIR else settings[1]
            video_path = f'{config.DIR}{settings[3]}' if config.DIR else settings[3]
            if os.path.exists(photo_path):
                await bot.send_photo(m.chat.id, photo_path, caption=text_html, parse_mode=ParseMode.HTML)
            elif os.path.exists(video_path):
                await bot.send_video(m.chat.id, video_path, caption=text_html, parse_mode=ParseMode.HTML)
            else:
                await bot.send_message(m.chat.id, text_html, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error(f"Ошибка отправки медиа: {e}")
            await bot.send_message(m.chat.id, text_html, parse_mode=ParseMode.HTML)

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text='📜 Изменить глобальный текст', callback_data='EDIT_TEXT')],
                [InlineKeyboardButton(text='🏙 Изменить глобальное фото', callback_data='EDIT_PHOTO')],
                [InlineKeyboardButton(text='🎥 Изменить глобальное видео', callback_data='EDIT_VIDEO')],
                [InlineKeyboardButton(text='❌ Удалить медиа', callback_data='DEL_MEDIA')],
                [InlineKeyboardButton(text='📊 Управление канальными постами', callback_data='CHANNEL_POSTS')]
            ]
        )
        await bot.send_message(m.chat.id, '<b>🔝 Ваш глобальный пост выглядит вот так 🔝</b>', reply_markup=keyboard, parse_mode=ParseMode.HTML)


@router.callback_query(F.data)
async def poc_callback_but(c: CallbackQuery, state: FSMContext):
    if 'EDIT_ID:' in c.data:
        channel_id = int(c.data.split(':')[1])
        try:
            addit_text = db.get_additional_text(channel_id)
            addit_text_value = addit_text[0] if addit_text else None
            post_data = db.get_channel_post(channel_id)
        except Exception as e:
            logger.error(f"Ошибка получения данных: {e}")
            addit_text_value = None
            post_data = None
            
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text='❌ Покинуть чат', callback_data=f'LFC:{channel_id}')],
                [InlineKeyboardButton(text='🛑 Остановить спам', callback_data=f'STOP_SPAM:{channel_id}')],
                [InlineKeyboardButton(text='🔄 Изменить задержку', callback_data=f'CHANGE_TIMEOUT:{channel_id}')]
            ]
        )
        
        if post_data and (post_data[0] or post_data[1] or post_data[2]):
            keyboard.inline_keyboard.insert(0, [InlineKeyboardButton(text='📊 Изменить канальный пост', callback_data=f'EDIT_CHANNEL_POST:{channel_id}')])
        
        if addit_text_value:
            keyboard.inline_keyboard.insert(0, [InlineKeyboardButton(text='🗃 Изменить дополнительный текст', callback_data=f'ADD_ADDITIONAL:{channel_id}')])
            await bot.send_message(c.message.chat.id, f'🗃 Текущий дополнительный текст: {addit_text_value}', reply_markup=keyboard, parse_mode=ParseMode.HTML)
        else:
            keyboard.inline_keyboard.insert(0, [InlineKeyboardButton(text='🗃 Добавить дополнительный текст', callback_data=f'ADD_ADDITIONAL:{channel_id}')])
            await bot.send_message(c.message.chat.id, f'🗃 Дополнительного текста не найдено.', reply_markup=keyboard)
            
    elif 'ADD_ADDITIONAL:' in c.data:
        channel_id = int(c.data.split(':')[1])
        await state.set_data({'channel_id': channel_id})
        await bot.send_message(c.message.chat.id, f'💬 Введите дополнительный текст для данного чата:')
        await state.set_state(addition.id)
        
    elif 'LFC:' in c.data:
        channel_id = int(c.data.split(':')[1])
        log = await user.leave_from_channel(channel_id)
        if log:
            text = f'☑️ Вы успешно покинули данный чат.'
        else:
            text = '❌ Возникли некие трудности при выходе.'
        await bot.send_message(c.message.chat.id, text)
        
    elif 'STOP_SPAM:' in c.data:
        channel_id = int(c.data.split(':')[1])
        db.stop_spam_for_channel(channel_id)
        await bot.send_message(c.message.chat.id, f'🛑 Спам в данном чате был остановлен.')
        
    elif 'CHANGE_TIMEOUT:' in c.data:
        channel_id = int(c.data.split(':')[1])
        await state.set_data({'channel_id': channel_id})
        await bot.send_message(c.message.chat.id, '🕒 Отправь мне интервал рассылки для этого чата (в минутах):')
        await state.set_state(channel_time.timeout)
        
    elif 'EDIT_TEXT' == c.data:
        await bot.send_message(c.message.chat.id, '📄 Введите текст глобального поста (Markdown поддерживается):')
        await state.set_state(post.text)
        
    elif 'EDIT_PHOTO' == c.data:
        await bot.send_message(c.message.chat.id, '🏙 Отправь мне новое фото для глобального поста:')
        
    elif 'EDIT_VIDEO' == c.data:
        await bot.send_message(c.message.chat.id, '🎥 Отправь мне новое видео для глобального поста:')
        
    elif 'DEL_MEDIA' == c.data:
        db.change_photo('')
        db.change_video('')
        await bot.send_message(c.message.chat.id, '❌ Медиа было успешно удалено.')
        
    elif 'CHANNEL_POSTS' == c.data:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text='📋 Список канальных постов', callback_data='LIST_CHANNEL_POSTS')],
                [InlineKeyboardButton(text='➕ Добавить канальный пост', callback_data='ADD_CHANNEL_POST')]
            ]
        )
        await bot.send_message(c.message.chat.id, '📊 Управление канальными постами:', reply_markup=keyboard)
        
    elif 'ADD_CHANNEL_POST' == c.data:
        await bot.send_message(c.message.chat.id, '💬 Введите ID канала:')
        await state.set_state(channel_post_text.text)
        
    elif 'EDIT_CHANNEL_POST:' in c.data:
        channel_id = int(c.data.split(':')[1])
        await state.set_data({'channel_id': channel_id})
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text='📝 Изменить текст', callback_data=f'CHANNEL_EDIT_TEXT:{channel_id}')],
                [InlineKeyboardButton(text='🏙 Изменить фото', callback_data=f'CHANNEL_EDIT_PHOTO:{channel_id}')],
                [InlineKeyboardButton(text='🎥 Изменить видео', callback_data=f'CHANNEL_EDIT_VIDEO:{channel_id}')],
                [InlineKeyboardButton(text='❌ Очистить пост', callback_data=f'CHANNEL_CLEAR:{channel_id}')]
            ]
        )
        await bot.send_message(c.message.chat.id, f'📊 Редактирование канального поста для чата {channel_id}:', reply_markup=keyboard)
        
    elif 'CHANNEL_EDIT_TEXT:' in c.data:
        channel_id = int(c.data.split(':')[1])
        await state.set_data({'channel_id': channel_id})
        await bot.send_message(c.message.chat.id, '📄 Введите текст канального поста (Markdown поддерживается):')
        await state.set_state(channel_post_text.text)
        
    elif 'CHANNEL_EDIT_PHOTO:' in c.data:
        channel_id = int(c.data.split(':')[1])
        await state.set_data({'channel_id': channel_id})
        await bot.send_message(c.message.chat.id, '🏙 Отправь фото для канального поста:')
        await state.set_state(channel_post_photo.photo)
        
    elif 'CHANNEL_EDIT_VIDEO:' in c.data:
        channel_id = int(c.data.split(':')[1])
        await state.set_data({'channel_id': channel_id})
        await bot.send_message(c.message.chat.id, '🎥 Отправь видео для канального поста:')
        await state.set_state(channel_post_video.video)
        
    elif 'CHANNEL_CLEAR:' in c.data:
        channel_id = int(c.data.split(':')[1])
        db.clear_channel_post(channel_id)
        await bot.send_message(c.message.chat.id, f'🗑 Канальный пост для чата {channel_id} был очищен.')
        
    elif 'LIST_CHANNEL_POSTS' == c.data:
        try:
            db.c.execute('SELECT CHANNEL, POST_PHOTO, POST_VIDEO, POST_TEXT FROM CHANNELS WHERE POST_PHOTO != "" OR POST_VIDEO != "" OR POST_TEXT != ""')
            posts = db.c.fetchall()
            if posts:
                text = '📋 Канальные посты:\n'
                for post in posts:
                    text += f'• Чат {post[0]}: {"Photo" if post[1] else ""} {"Video" if post[2] else ""} {"Text" if post[3] else ""}\n'
                await bot.send_message(c.message.chat.id, text)
            else:
                await bot.send_message(c.message.chat.id, '📋 Канальных постов нет.')
        except Exception as e:
            logger.error(f"Ошибка получения списка постов: {e}")
            await bot.send_message(c.message.chat.id, f'❌ Ошибка: {e}')


@router.message(F.photo)
async def download_photo(m: Message):
    result = await m.photo[-1].download()
    db.change_photo(os.path.basename(result.name))
    await bot.send_message(m.chat.id, '🏙 Фото было успешно обновлено.')


@router.message(F.video)
async def download_video(m: Message):
    result = await m.video.download()
    db.change_video(os.path.basename(result.name))
    await bot.send_message(m.chat.id, '🎥 Видео было успешно обновлено.')


@router.message(login_phone.phone)
async def handle_phone_input(m: Message, state: FSMContext):
    """Обработка ввода телефона"""
    phone = m.text.strip()
    if re.match(r'^\+?[0-9]+$', phone):
        user.login_phone = phone
        user.current_code[m.chat.id] = ""
        
        # Удаляем сообщение пользователя
        try:
            await m.delete()
        except:
            pass
        
        # Создаем клавиатуру с цифрами
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
                    InlineKeyboardButton(text='✓ Войти', callback_data='code_enter')
                ]
            ]
        )
        
        # Просим ввести код
        await m.answer(f'📱 Телефон: {phone}')
        msg = await m.answer(
            'Код: ',
            reply_markup=keyboard
        )
        user.code_messages[m.chat.id] = msg.message_id
        await state.set_state(login_code.code)
    else:
        await m.answer('❌ Неверный формат телефона. Попробуйте снова:')


@router.message(login_code.code)
async def handle_code_input(m: Message, state: FSMContext):
    """Обработка ввода кода текстом"""
    code = m.text.strip()
    if code.isdigit() and 4 <= len(code) <= 6:
        if user.login_phone:
            from pyrogram import Client
            client = Client("session", config.API_ID, config.API_HASH, workdir=".")
            try:
                async with client:
                    await client.sign_in(user.login_phone, code)
                await bot.send_message(config.ADMINS[0], "✅ Успешный вход!")
                user.current_code[m.chat.id] = ""
                await state.clear()
            except Exception as e:
                await m.answer(f'❌ Ошибка: {e}')
        else:
            await m.answer('❌ Сначала введите телефон.')
    else:
        await m.answer('❌ Код должен содержать 4-6 цифр. Попробуйте снова:')


@router.message(login_password.password)
async def handle_password_input(m: Message, state: FSMContext):
    """Обработка ввода 2FA пароля"""
    password = m.text
    user.login_password = password
    
    # Удаляем сообщение пользователя
    try:
        await m.delete()
    except:
        pass
    
    # Подтверждение
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='✓ Подтвердить', callback_data='password_enter')],
            [InlineKeyboardButton(text='❌ Отмена', callback_data='password_cancel')]
        ]
    )
    
    await m.answer(
        f'🔐 Введенный пароль: {password}',
        reply_markup=keyboard
    )


@router.callback_query(F.data.startswith('code_'))
async def handle_code_button(c: CallbackQuery, state: FSMContext):
    """Обработка кнопок ввода кода"""
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
            client = Client("session", config.API_ID, config.API_HASH, workdir=".")
            try:
                async with client:
                    await client.sign_in(user.login_phone, current_code_val)
                await bot.send_message(config.ADMINS[0], "✅ Успешный вход!")
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
    """Подтверждение пароля"""
    if user.login_password and user.login_phone:
        from pyrogram import Client
        client = Client("session", config.API_ID, config.API_HASH, workdir=".")
        try:
            async with client:
                await client.check_password(user.login_password)
            await bot.send_message(config.ADMINS[0], "✅ Успешный вход!")
            user.current_code[c.message.chat.id] = ""
            await c.message.delete()
        except Exception as e:
            await c.answer(f"Ошибка: {e}", show_alert=True)
    else:
        await c.answer("Сначала введите пароль", show_alert=True)


@router.callback_query(F.data == 'password_cancel')
async def handle_password_cancel(c: CallbackQuery):
    """Отмена пароля"""
    await c.message.delete()
    await c.answer("Вход отменен")


@router.callback_query(F.data == 'login_cancel')
async def cancel_login(c: CallbackQuery):
    """Отмена входа"""
    await c.message.delete()
    await c.answer("Вход отменен")


async def do_login(chat_id):
    """Выполнить вход через Telegram"""
    # Удаляем старую сессию
    if os.path.exists("session.session"):
        os.remove("session.session")
        logger.info("Удалена старая сессия")
    
    # Просим ввести телефон
    msg = await bot.send_message(
        chat_id,
        '📱 Введите номер телефона (в формате +1234567890):'
    )
    # Удаляем через 60 сек
    await asyncio.sleep(60)
    try:
        await msg.delete()
    except:
        pass


async def start_spam():
    """Запустить рассылку"""
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
            await bot.send_message(config.ADMINS[0], '⚠️ Нет доступных каналов для рассылки')
            return
        
        settings = db.settings()
        asyncio.create_task(user.spamming(spam_list, settings, db))


async def main():
    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())
