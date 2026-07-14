"""Модуль авторизации через Telegram"""
import os
import logging
import asyncio
from pyrogram import Client
from pyrogram.errors import (
    PhoneMigrated, NetworkMigrate, SessionPasswordNeeded,
    AuthKeyUnregistered, PhoneCodeInvalid, PhoneNumberInvalid,
    PhoneCodeExpired
)
import config

logger = logging.getLogger(__name__)

# Глобальные переменные для FSM
pending_login_chat = None
login_phone = None
login_code = None
login_client = None


def init_login_bot(bot):
    """Инициализация бота для логина"""
    global login_bot
    login_bot = bot


async def login_phone_step(chat_id):
    """Этап ввода телефона"""
    global pending_login_chat
    
    pending_login_chat = chat_id
    
    # Удаляем старые сообщения
    try:
        import aiogram
        from aiogram import types
        
        # Просим ввести телефон
        keyboard = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text='❌ Отмена', callback_data='login_cancel')]
            ]
        )
        
        msg = await login_bot.send_message(
            chat_id,
            '📱 Введите номер телефона (в формате +1234567890):',
            reply_markup=keyboard
        )
        
        # Удаляем сообщение бота через 60 сек
        await asyncio.sleep(60)
        try:
            await msg.delete()
        except:
            pass
            
    except Exception as e:
        logger.error(f"Ошибка отправки запроса телефона: {e}")


async def login_code_step(phone):
    """Этап ввода кода"""
    global login_code
    
    login_phone = phone
    login_code = None
    
    # Генерируем inline кнопки с цифрами
    keyboard = [
        [
            types.InlineKeyboardButton(text='1', callback_data='code_1'),
            types.InlineKeyboardButton(text='2', callback_data='code_2'),
            types.InlineKeyboardButton(text='3', callback_data='code_3')
        ],
        [
            types.InlineKeyboardButton(text='4', callback_data='code_4'),
            types.InlineKeyboardButton(text='5', callback_data='code_5'),
            types.InlineKeyboardButton(text='6', callback_data='code_6')
        ],
        [
            types.InlineKeyboardButton(text='7', callback_data='code_7'),
            types.InlineKeyboardButton(text='8', callback_data='code_8'),
            types.InlineKeyboardButton(text='9', callback_data='code_9')
        ],
        [
            types.InlineKeyboardButton(text='⌫', callback_data='code_back'),
            types.InlineKeyboardButton(text='0', callback_data='code_0'),
            types.InlineKeyboardButton(text='✓', callback_data='code_enter')
        ]
    ]
    
    # Сохраняем текущий код для каждого чата
    login_code[config.ADMINS[0]] = ""
    
    msg = await login_bot.send_message(
        config.ADMINS[0],
        f'🔑 Введите код из Telegram для {phone}:\n\n'
        f'Введите код используя кнопки ниже или просто текстом',
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )


async def complete_login(phone, code):
    """Завершить вход"""
    global login_client
    
    try:
        # Создаем клиента
        login_client = Client(
            "session",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            workdir=".",
            parse_mode=enums.ParseMode.DEFAULT
        )
        
        async with login_client:
            await login_client.sign_in(phone, code)
        
        logger.info(f"Успешный вход: {phone}")
        await login_bot.send_message(config.ADMINS[0], "✅ Успешный вход!")
        
        # Очищаем
        login_client = None
        return True
        
    except PhoneCodeInvalid:
        await login_bot.send_message(config.ADMINS[0], "❌ Неверный код. Попробуйте снова.")
        return False
    except PhoneCodeExpired:
        await login_bot.send_message(config.ADMINS[0], "❌ Код истек. Попробуйте снова.")
        return False
    except Exception as e:
        logger.error(f"Ошибка входа: {e}")
        await login_bot.send_message(config.ADMINS[0], f"❌ Ошибка входа: {e}")
        return False
