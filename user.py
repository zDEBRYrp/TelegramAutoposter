from pyrogram import Client, enums
from pyrogram.errors import FloodWait, AuthKeyUnregistered, SessionPasswordNeeded
import config
import asyncio
import logging
import os
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

client: Optional[Client] = None
bot_instance = None

login_phone = None
login_password = None
current_code = {}
code_messages = {}
phone_code_hash = None


def init_bot(bot):
    global bot_instance
    bot_instance = bot


def make_client() -> Client:
    return Client(
        "session",
        api_id=config.API_ID,
        api_hash=config.API_HASH,
        workdir=".",
    )


async def start_client() -> bool:
    global client
    if client and client.is_connected:
        return True

    # Не запускаем если нет файла сессии — иначе Pyrogram запросит телефон в консоли
    if not os.path.exists("session.session"):
        logger.info("Файл сессии не найден. Используй /login для входа.")
        return False

    client = make_client()
    try:
        await client.start()
        me = await client.get_me()
        logger.info(f"Pyrogram запущен как {me.first_name} ({me.phone_number})")
        return True
    except AuthKeyUnregistered:
        logger.error("AuthKeyUnregistered. Удаляю сессию...")
        await _delete_session()
        if bot_instance:
            for admin in config.ADMINS:
                await bot_instance.send_message(
                    admin,
                    "Сессия недействительна. Отправь /login чтобы войти заново."
                )
        return False
    except Exception as e:
        logger.error(f"Ошибка запуска клиента: {e}")
        return False


async def stop_client():
    global client
    if client and client.is_connected:
        try:
            await client.stop()
        except Exception as e:
            logger.error(f"Ошибка остановки клиента: {e}")
    client = None


async def _delete_session():
    await stop_client()
    for f in ["session.session", "session.session-journal"]:
        if os.path.exists(f):
            os.remove(f)
            logger.info(f"Удалён файл: {f}")


async def ensure_connected() -> bool:
    global client
    if client and client.is_connected:
        return True
    return await start_client()


async def get_chats() -> List[Dict[str, Any]]:
    if not await ensure_connected():
        return []

    chat_list = []
    try:
        async for dialog in client.get_dialogs():
            if dialog.chat.type in (enums.ChatType.SUPERGROUP, enums.ChatType.CHANNEL):
                chat_list.append({
                    'title': dialog.chat.title,
                    'id': dialog.chat.id
                })
    except AuthKeyUnregistered:
        await _delete_session()
        if bot_instance:
            for admin in config.ADMINS:
                await bot_instance.send_message(admin, "Сессия недействительна. Отправь /login для входа.")
    except Exception as e:
        logger.error(f"Ошибка получения чатов: {e}")
    return chat_list


async def leave_from_channel(channel_id: int) -> bool:
    if not await ensure_connected():
        return False

    try:
        await client.leave_chat(channel_id)
        return True
    except Exception as e:
        logger.error(f"Ошибка выхода из чата {channel_id}: {e}")
        return False


async def do_login(phone: str) -> Optional[str]:
    global phone_code_hash, client
    await _delete_session()
    client = make_client()
    await client.connect()
    try:
        sent = await client.send_code(phone)
        phone_code_hash = sent.phone_code_hash
        return sent.phone_code_hash
    except Exception as e:
        logger.error(f"Ошибка отправки кода: {e}")
        return None


async def do_sign_in(phone: str, code: str) -> dict:
    global client, phone_code_hash
    try:
        await client.sign_in(phone, phone_code_hash, code)
        await client.stop()
        await start_client()
        return {"ok": True}
    except SessionPasswordNeeded:
        hint = await client.get_password_hint()
        return {"ok": False, "need_password": True, "hint": hint}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def do_check_password(password: str) -> dict:
    global client
    try:
        await client.check_password(password)
        await client.stop()
        await start_client()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def _send_with_fallback(chat_id: int, text: str, photo_path: str = None, video_path: str = None):
    """Отправить медиа с fallback на текст если чат не поддерживает."""
    if photo_path:
        for ext in ['.jpg', '.jpeg', '.png', '.webp', '']:
            p = photo_path + ext if ext else photo_path
            if os.path.exists(p):
                try:
                    await client.send_photo(chat_id, p, caption=text or None)
                    return
                except Exception as e:
                    err = str(e).lower()
                    if any(x in err for x in ['chat_send_photos_forbidden', 'media', 'forbidden']):
                        logger.warning(f"Фото запрещено в {chat_id}, отправляю текст")
                        if text:
                            await client.send_message(chat_id, text)
                        return
                    raise
        if text:
            await client.send_message(chat_id, text)
        return

    if video_path:
        for ext in ['.mp4', '.mov', '.webm', '']:
            p = video_path + ext if ext else video_path
            if os.path.exists(p):
                try:
                    await client.send_video(chat_id, p, caption=text or None)
                    return
                except Exception as e:
                    err = str(e).lower()
                    if any(x in err for x in ['chat_send_videos_forbidden', 'media', 'forbidden']):
                        logger.warning(f"Видео запрещено в {chat_id}, отправляю текст")
                        if text:
                            await client.send_message(chat_id, text)
                        return
                    raise
        if text:
            await client.send_message(chat_id, text)
        return

    if text:
        await client.send_message(chat_id, text)


async def spamming(spam_list: List[Dict[str, Any]], settings: tuple, db) -> None:
    if not await ensure_connected():
        return

    try:
        while True:
            settings = db.settings()
            if settings[4] != 1:
                break

            active_channels = [
                chat for chat in spam_list
                if db.get_channel_spam_status(chat['id']) == 1
            ]

            if not active_channels:
                await asyncio.sleep(30)
                continue

            for chat in active_channels:
                settings = db.settings()
                if settings[4] != 1:
                    break

                if not await ensure_connected():
                    await asyncio.sleep(30)
                    break

                try:
                    channel_post = db.get_channel_post(chat['id'])
                    if channel_post and (channel_post[0] or channel_post[1] or channel_post[2]):
                        text = channel_post[2] or ''
                        if chat.get('text'):
                            text = f"{text}\n\n{chat['text']}" if text else chat['text']

                        if channel_post[0]:
                            photo_path = f"{config.DIR}{channel_post[0]}" if config.DIR else channel_post[0]
                            await _send_with_fallback(chat['id'], text, photo_path=photo_path)
                        elif channel_post[1]:
                            video_path = f"{config.DIR}{channel_post[1]}" if config.DIR else channel_post[1]
                            await _send_with_fallback(chat['id'], text, video_path=video_path)
                        else:
                            if text:
                                await client.send_message(chat['id'], text)
                    else:
                        # settings: [0]=ID, [1]=PHOTO, [2]=VIDEO, [3]=TEXT, [4]=SPAM, [5]=TIMEOUT
                        text = settings[3] or ''
                        if chat.get('text'):
                            text = f"{text}\n\n{chat['text']}" if text else chat['text']

                        if settings[1]:
                            photo_path = f"{config.DIR}{settings[1]}" if config.DIR else settings[1]
                            await _send_with_fallback(chat['id'], text, photo_path=photo_path)
                        elif settings[2]:
                            video_path = f"{config.DIR}{settings[2]}" if config.DIR else settings[2]
                            await _send_with_fallback(chat['id'], text, video_path=video_path)
                        else:
                            if text:
                                await client.send_message(chat['id'], text)

                    timeout = db.settings()[5]
                    await asyncio.sleep(timeout * 60)

                except FloodWait as e:
                    logger.warning(f"FloodWait {e.value}s")
                    await asyncio.sleep(e.value)
                except Exception as e:
                    logger.error(f"Ошибка отправки в {chat['id']}: {e}")
                    await asyncio.sleep(10)

    except Exception as e:
        logger.error(f"Ошибка в spamming: {e}")
