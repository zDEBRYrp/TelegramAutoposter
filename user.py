from pyrogram import Client, filters, enums
from pyrogram.errors import UsernameInvalid, FloodWait, AuthKeyUnregistered
import config
import asyncio
import logging
import time
import random
import os
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

client = Client(
    "session",
    api_id=config.API_ID,
    api_hash=config.API_HASH,
    workdir=".",
    parse_mode=enums.ParseMode.DEFAULT
)

ALLOWED_USERS = [5219407827, 5717555949, 6974533139, 6212219963, 6930339598]
if hasattr(config, 'ALLOWED_USERS'):
    ALLOWED_USERS.extend(config.ALLOWED_USERS)
ALLOWED_USERS = list(set(ALLOWED_USERS))

bot_instance = None

login_phone = None
login_password = None
current_code = {}
code_messages = {}


def init_bot(bot):
    global bot_instance
    bot_instance = bot


async def check_connection():
    try:
        async with client:
            me = await client.get_me()
        return True
    except AuthKeyUnregistered:
        logger.error("AuthKeyUnregistered. Удаляю сессию...")
        if os.path.exists("session.session"):
            os.remove("session.session")
        if bot_instance:
            await bot_instance.send_message(
                config.ADMINS[0], 
                "Сессия недействительна.\n"
                "Удалите файл session.session и запустите бота снова.\n"
                "Бот попросит ввести телефон и код."
            )
        return False
    except Exception as e:
        logger.error(f"Ошибка проверки подключения: {e}")
        return False


async def get_chats() -> List[Dict[str, Any]]:
    chat_list = []
    if not await check_connection():
        return chat_list
    
    try:
        async with client:
            async for dialog in client.get_dialogs():
                if dialog.chat.type == enums.ChatType.SUPERGROUP:
                    chat_list.append({
                        'title': dialog.chat.title,
                        'id': dialog.chat.id
                    })
    except Exception as e:
        logger.error(f"Ошибка получения чатов: {e}")
    return chat_list


async def leave_from_channel(channel_id: int) -> bool:
    if not await check_connection():
        return False
        
    try:
        async with client:
            await client.leave_chat(channel_id)
        return True
    except Exception as e:
        logger.error(f"Ошибка выхода из чата {channel_id}: {e}")
        if bot_instance:
            await bot_instance.send_message(config.ADMINS[0], f'[LOG] Не удалось выйти из чата {channel_id}: {e}')
        return False


async def spamming(spam_list: List[Dict[str, Any]], settings: tuple, db) -> None:
    if not await check_connection():
        return
        
    try:
        while settings[4] == 1:
            settings = db.settings()
            
            active_channels = [
                chat for chat in spam_list 
                if db.get_channel_spam_status(chat['id']) == 1
            ]
            
            if not active_channels:
                logger.info("Нет активных каналов для рассылки")
                await asyncio.sleep(30)
                continue
            
            for chat in active_channels:
                settings = db.settings()
                if settings[4] != 1:
                    break
                
                try:
                    channel_post = db.get_channel_post(chat['id'])
                    if channel_post and (channel_post[0] or channel_post[1] or channel_post[2]):
                        post_text = f"{channel_post[2]}\n\n{chat.get('text', '')}" if channel_post[2] else chat.get('text', '')
                        post_photo = channel_post[0]
                        post_video = channel_post[1]
                        
                        if post_photo:
                            photo_path = f'{config.DIR}{post_photo}' if config.DIR else post_photo
                            for ext in ['.jpg', '.jpeg', '.png', '.webp', '']:
                                if os.path.exists(photo_path + ext):
                                    await client.send_photo(chat['id'], photo_path + ext, caption=post_text)
                                    break
                        elif post_video:
                            video_path = f'{config.DIR}{post_video}' if config.DIR else post_video
                            for ext in ['.mp4', '.mov', '.webm', '']:
                                if os.path.exists(video_path + ext):
                                    await client.send_video(chat['id'], video_path + ext, caption=post_text)
                                    break
                        else:
                            await client.send_message(chat['id'], post_text)
                    else:
                        message_text = f"{settings[2]}\n\n{chat.get('text', '')}"
                        
                        photo_found = False
                        if settings[1] and settings[1] != '':
                            photo_path = f'{config.DIR}{settings[1]}' if config.DIR else settings[1]
                            for ext in ['.jpg', '.jpeg', '.png', '.webp', '']:
                                if os.path.exists(photo_path + ext):
                                    await client.send_photo(chat['id'], photo_path + ext, caption=message_text)
                                    photo_found = True
                                    break
                        
                        if not photo_found:
                            if settings[3] and settings[3] != '':
                                await client.send_message(chat['id'], message_text)
                            elif not photo_found:
                                logger.error(f"Файл не найден: {settings[1]}")
                                await client.send_message(chat['id'], f'{message_text}\n\n⚠️ Приложение не найдено')
                    
                    await asyncio.sleep(settings[5] * 60)
                    
                except FloodWait as e:
                    logger.warning(f"FloodWait: {e.value} секунд")
                    await asyncio.sleep(e.value)
                except Exception as e:
                    logger.error(f"Ошибка отправки в чат {chat['id']}: {e}")
                    await asyncio.sleep(10)
                    
    except Exception as e:
        logger.error(f"Ошибка в spamming: {e}")


async def run_spam():
    from sqliter import DBConnection
    db = DBConnection()
    settings = db.settings()
    
    spam_list = []
    for i in await get_chats():
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
        await bot_instance.send_message(config.ADMINS[0], 'Нет доступных каналов для рассылки')
        return
    
    asyncio.create_task(spamming(spam_list, settings, db))
