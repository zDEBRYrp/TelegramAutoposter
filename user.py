import asyncio as _asyncio
try:
    _asyncio.get_event_loop()
except RuntimeError:
    # Python 3.12+: у pyrogram 2.x в sync.py вызывается get_event_loop()
    # на импорте — без активного loop импорт падает. Создаём заранее.
    _asyncio.set_event_loop(_asyncio.new_event_loop())
del _asyncio

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
login_error: Optional[str] = None
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
            if dialog.chat.type in (enums.ChatType.SUPERGROUP, enums.ChatType.CHANNEL,
                                    enums.ChatType.GROUP):
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
    global phone_code_hash, client, login_error
    login_error = None
    await _delete_session()
    client = make_client()
    try:
        await client.connect()
    except Exception as e:
        logger.error(f"Ошибка подключения для отправки кода: {e}")
        login_error = str(e)
        return None
    try:
        sent = await client.send_code(phone)
        phone_code_hash = sent.phone_code_hash
        return sent.phone_code_hash
    except FloodWait as e:
        wait = int(getattr(e, 'value', 0) or 0)
        logger.error(f"FloodWait при отправке кода: {wait}s")
        login_error = f"Telegram просит подождать {wait} сек. Попробуйте позже."
        try:
            await client.disconnect()
        except Exception:
            pass
        return None
    except Exception as e:
        logger.error(f"Ошибка отправки кода: {e}")
        login_error = str(e)
        try:
            await client.disconnect()
        except Exception:
            pass
        return None


async def do_sign_in(phone: str, code: str) -> dict:
    global client, phone_code_hash
    if client is None:
        return {"ok": False, "error": "Нет активной сессии входа. Отправьте /login заново."}
    try:
        await client.sign_in(phone, phone_code_hash, code)
        try:
            await client.disconnect()
        except Exception:
            pass
        ok = await start_client()
        if not ok:
            return {"ok": False, "error": "Код принят, но не удалось запустить клиент"}
        return {"ok": True}
    except SessionPasswordNeeded:
        try:
            hint = await client.get_password_hint()
        except Exception:
            hint = ""
        return {"ok": False, "need_password": True, "hint": hint}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def do_check_password(password: str) -> dict:
    global client
    if client is None:
        return {"ok": False, "error": "Нет активной сессии входа. Отправьте /login заново."}
    try:
        await client.check_password(password)
        try:
            await client.disconnect()
        except Exception:
            pass
        # Сбрасываем сохранённый пароль из памяти
        globals()["login_password"] = None
        ok = await start_client()
        if not ok:
            return {"ok": False, "error": "Пароль принят, но не удалось запустить клиент"}
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _resolve_media(path_base: str):
    """Найти файл медиа: точное имя, затем подбор расширения, затем photos/."""
    import os as _os
    if not path_base:
        return None
    try:
        import config as _cfg
        media_dir = getattr(_cfg, 'DIR', '') or ''
    except Exception:
        media_dir = ''
    dirs = ([media_dir] if media_dir else []) + ['photos', '']
    for d in dirs:
        p = _os.path.join(d, path_base) if d else path_base
        if _os.path.isfile(p):
            return p
    for d in dirs:
        base = _os.path.join(d, path_base) if d else path_base
        for ext in ['.jpg', '.jpeg', '.png', '.webp', '.mp4', '.mov', '.webm']:
            if _os.path.isfile(base + ext):
                return base + ext
    return None


def _to_html(text: str) -> str:
    """Markdown -> HTML для отправки через Pyrogram."""
    if not text:
        return ''
    try:
        from sqliter import markdown_to_html as _m2h
        return _m2h(text)
    except Exception:
        return text


async def _send_with_fallback(chat_id: int, text: str, photo_path: str = None, video_path: str = None):
    """Отправить медиа с fallback на текст если чат не поддерживает."""
    from pyrogram.enums import ParseMode as PyroParseMode

    html_text = _to_html(text) if text else None

    if photo_path:
        found = _resolve_media(photo_path) or (photo_path if os.path.exists(photo_path) else None)
        if found:
            try:
                await client.send_photo(chat_id, found, caption=html_text, parse_mode=PyroParseMode.HTML)
                return
            except Exception as e:
                err = str(e).lower()
                if any(x in err for x in ['chat_send_photos_forbidden', 'chat_send_media_forbidden', 'media', 'forbidden']):
                    logger.warning(f"Фото запрещено в {chat_id}, отправляю текст")
                    if html_text:
                        await client.send_message(chat_id, html_text, parse_mode=PyroParseMode.HTML)
                    return
                raise
        if html_text:
            await client.send_message(chat_id, html_text, parse_mode=PyroParseMode.HTML)
        return

    if video_path:
        found = _resolve_media(video_path) or (video_path if os.path.exists(video_path) else None)
        if found:
            try:
                await client.send_video(chat_id, found, caption=html_text, parse_mode=PyroParseMode.HTML)
                return
            except Exception as e:
                err = str(e).lower()
                if any(x in err for x in ['chat_send_videos_forbidden', 'chat_send_media_forbidden', 'media', 'forbidden']):
                    logger.warning(f"Видео запрещено в {chat_id}, отправляю текст")
                    if html_text:
                        await client.send_message(chat_id, html_text, parse_mode=PyroParseMode.HTML)
                    return
                raise
        if html_text:
            await client.send_message(chat_id, html_text, parse_mode=PyroParseMode.HTML)
        return

    if html_text:
        await client.send_message(chat_id, html_text, parse_mode=PyroParseMode.HTML)


async def spamming(spam_list: List[Dict[str, Any]], settings: tuple, db) -> None:
    if not await ensure_connected():
        return

    try:
        while True:
            try:
                settings = db.settings()
            except Exception as e:
                logger.error(f"Ошибка чтения настроек: {e}")
                await asyncio.sleep(30)
                continue
            if not settings or settings[4] != 1:
                break

            default_timeout = settings[5] if len(settings) > 5 else 5

            # Обновляем доп. текст и отсев выключенных на каждой итерации
            active_channels = []
            for chat in spam_list:
                try:
                    if db.get_channel_spam_status(chat['id']) != 1:
                        continue
                    addit = db.get_additional_text(chat['id'])
                    chat['text'] = addit[0] if addit and addit[0] else ''
                    active_channels.append(chat)
                except Exception as e:
                    logger.error(f"Ошибка подготовки чата {chat.get('id')}: {e}")

            if not active_channels:
                await asyncio.sleep(30)
                continue

            for chat in active_channels:
                try:
                    settings = db.settings()
                except Exception:
                    settings = None
                if not settings or settings[4] != 1:
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
                                from pyrogram.enums import ParseMode as PyroParseMode
                                await client.send_message(chat['id'], _to_html(text),
                                                          parse_mode=PyroParseMode.HTML)
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
                                from pyrogram.enums import ParseMode as PyroParseMode
                                await client.send_message(chat['id'], _to_html(text),
                                                          parse_mode=PyroParseMode.HTML)

                    # Индивидуальный таймаут чата, иначе глобальный
                    try:
                        per_chat = db.get_channel_timeout(chat['id'])
                    except Exception:
                        per_chat = None
                    timeout = per_chat if per_chat and per_chat >= 1 else default_timeout
                    await asyncio.sleep(timeout * 60)

                except FloodWait as e:
                    wait = int(getattr(e, 'value', 30) or 30)
                    logger.warning(f"FloodWait {wait}s")
                    await asyncio.sleep(wait)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"Ошибка отправки в {chat['id']}: {e}")
                    await asyncio.sleep(10)

    except asyncio.CancelledError:
        logger.info("Спам-цикл остановлен")
        raise
    except Exception as e:
        logger.error(f"Ошибка в spamming: {e}")
