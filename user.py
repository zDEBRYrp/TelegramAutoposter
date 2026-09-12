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
import html as _html
import logging
import os
import re as _re
import time as _time
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

# Последний результат отправки: chat_id -> {'ok': bool, 'error': str, 'at': float}
send_status: dict = {}

# Кэш диалогов для меню
_chats_cache: dict = {'at': 0.0, 'data': []}
CHATS_CACHE_TTL = 60


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
        # Мёртвая/просроченная сессия может висеть в переподключениях вечно —
        # ограничиваем время старта, иначе весь бот не запустится
        await asyncio.wait_for(client.start(), timeout=30)
        me = await client.get_me()
        logger.info(f"Pyrogram запущен как {me.first_name} ({me.phone_number})")
        return True
    except asyncio.TimeoutError:
        logger.error("Таймаут запуска Pyrogram (30с). Сессия битая? Удали session.session и войди через /login.")
        try:
            await client.stop()
        except Exception:
            pass
        client = None
        return False
    except AuthKeyUnregistered:
        logger.error("AuthKeyUnregistered. Удаляю сессию...")
        await _delete_session()
        await notify_admin("Сессия недействительна. Отправь /login чтобы войти заново.")
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


async def notify_admin(text: str) -> None:
    """Сообщение всем админам через бот (best effort, тихо)."""
    try:
        if bot_instance is None:
            return
        for admin in config.ADMINS:
            try:
                await bot_instance.send_message(admin, text)
            except Exception:
                pass
    except Exception:
        pass


async def _delete_session():
    await stop_client()
    _chats_cache['at'] = 0.0
    _chats_cache['data'] = []
    for f in ["session.session", "session.session-journal"]:
        if os.path.exists(f):
            os.remove(f)
            logger.info(f"Удалён файл: {f}")


async def ensure_connected() -> bool:
    global client
    if client and client.is_connected:
        return True
    return await start_client()


async def get_chats(force: bool = False) -> List[Dict[str, Any]]:
    """Диалоги-чаты. Кэшируем на минуту — меню не должно дёргать сеть
    на каждый тап (отсюда были апдейты по 20+ секунд)."""
    now = _time.time()
    if not force and _chats_cache['data'] and now - _chats_cache['at'] < CHATS_CACHE_TTL:
        return [dict(c) for c in _chats_cache['data']]

    if not await ensure_connected():
        return [dict(c) for c in _chats_cache['data']] if _chats_cache['data'] else []

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
        await notify_admin("Сессия недействительна. Отправь /login для входа.")
    except Exception as e:
        logger.error(f"Ошибка получения чатов: {e}")
        if _chats_cache['data']:
            return [dict(c) for c in _chats_cache['data']]
        return []
    _chats_cache['at'] = _time.time()
    _chats_cache['data'] = chat_list
    return [dict(c) for c in chat_list]


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
        html_text = _m2h(text)
        # Pyrogram понимает только <spoiler>, а <tg-spoiler> молча выкидывает
        return html_text.replace('<tg-spoiler>', '<spoiler>').replace('</tg-spoiler>', '</spoiler>')
    except Exception:
        return text


async def _send_with_fallback(chat_id: int, text: str, photo_path: str = None,
                              video_path: str = None, topic: int = 0, mention: str = ''):
    """Отправить медиа с fallback на текст если чат не поддерживает.
    topic = id темы форума (0 = General): шлём ответом в топик.
    mention = готовый HTML-хвост со скрытыми отметками (уже после конвертации)."""
    from pyrogram.enums import ParseMode as PyroParseMode

    html_text = ((_to_html(text) if text else '') + (mention or ''))
    reply_to = int(topic) if topic else None

    if photo_path:
        found = _resolve_media(photo_path) or (photo_path if os.path.exists(photo_path) else None)
        if found:
            try:
                await client.send_photo(chat_id, found, caption=html_text or None,
                                        parse_mode=PyroParseMode.HTML,
                                        reply_to_message_id=reply_to)
                return
            except Exception as e:
                err = str(e).lower()
                if any(x in err for x in ['chat_send_photos_forbidden', 'chat_send_media_forbidden', 'media', 'forbidden']):
                    logger.warning(f"Фото запрещено в {chat_id}, отправляю текст")
                    if html_text:
                        await client.send_message(chat_id, html_text, parse_mode=PyroParseMode.HTML,
                                                  reply_to_message_id=reply_to)
                    return
                raise
        if html_text:
            await client.send_message(chat_id, html_text, parse_mode=PyroParseMode.HTML,
                                      reply_to_message_id=reply_to)
        return

    if video_path:
        found = _resolve_media(video_path) or (video_path if os.path.exists(video_path) else None)
        if found:
            try:
                await client.send_video(chat_id, found, caption=html_text or None,
                                        parse_mode=PyroParseMode.HTML,
                                        reply_to_message_id=reply_to)
                return
            except Exception as e:
                err = str(e).lower()
                if any(x in err for x in ['chat_send_videos_forbidden', 'chat_send_media_forbidden', 'media', 'forbidden']):
                    logger.warning(f"Видео запрещено в {chat_id}, отправляю текст")
                    if html_text:
                        await client.send_message(chat_id, html_text, parse_mode=PyroParseMode.HTML,
                                                  reply_to_message_id=reply_to)
                    return
                raise
        if html_text:
            await client.send_message(chat_id, html_text, parse_mode=PyroParseMode.HTML,
                                      reply_to_message_id=reply_to)
        return

    if html_text:
        await client.send_message(chat_id, html_text, parse_mode=PyroParseMode.HTML,
                                  reply_to_message_id=reply_to)


FATAL_SEND_ERRORS = (
    'peer_id_invalid', 'user_banned_in_channel', 'chat_write_forbidden',
    'channel_private', 'chat_admin_required', 'user_is_blocked',
    'input_user_deactivated', 'user_deactivated', 'auth_key_unregistered',
    'session_revoked', 'user_restricted',
)


def _is_parse_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(s in msg for s in (
        "can't parse entities", 'message_empty', 'message text is empty',
        'message is empty', 'entities', 'caption_too_long', 'message_too_long'))


def _is_fatal_error_msg(msg: str) -> bool:
    m = (msg or '').lower().replace(' ', '_')
    return any(s in m for s in FATAL_SEND_ERRORS)


def strip_html_tags(s: str) -> str:
    """Убираем теги — plain-версия для фолбэка, если HTML не парсится."""
    if not s:
        return ''
    s = _re.sub(r'<[^>]+>', '', s)
    return _html.unescape(s)


# Кэш участников для скрытых отметок: chat_id -> (at, [user_id])
_tag_cache: dict = {}
TAG_LIMIT = 5  # максимум скрытых отметок на пост: больше 5 Telegram обычно не уведомляет
TAG_MEMBERS_TTL = 3600  # кэш списка участников (сек)
TAG_ANCHOR = chr(0x2060)  # U+2060 WORD JOINER — невидимый якорь для отметок
SLOWMODE_TTL = 3600  # как часто перепроверяем слоумод канала (сек)


async def get_tag_members(chat_id: int, limit: int = TAG_LIMIT):
    """ID участников чата для скрытых отметок (боты и удалённые — мимо).
    Кэшируется на TAG_MEMBERS_TTL."""
    now = _time.time()
    entry = _tag_cache.get(chat_id)
    if entry and now - entry[0] < TAG_MEMBERS_TTL:
        return list(entry[1])
    if not await ensure_connected():
        return list(entry[1]) if entry else []
    ids = []
    try:
        async for m in client.get_chat_members(chat_id, limit=limit * 2):
            try:
                u = m.user
                if getattr(u, 'is_bot', False) or getattr(u, 'is_deleted', False):
                    continue
                ids.append(int(u.id))
            except Exception:
                continue
            if len(ids) >= limit:
                break
    except Exception as e:
        logger.error(f"Не смог прочитать участников {chat_id}: {e}")
        return list(entry[1]) if entry else []
    _tag_cache[chat_id] = (now, ids)
    return list(ids)


async def build_mentions(chat_id: int, db) -> str:
    """HTML-хвост со скрытыми отметками ('' если выключено/некого отмечать).
    Якоря — невидимые U+2060, сущности — text_mention через tg://user ссылки."""
    try:
        get_tag = getattr(db, 'get_tag_all', None)
        if get_tag is None or get_tag(chat_id) != 1:
            return ''
    except Exception:
        return ''
    ids = await get_tag_members(chat_id)
    if not ids:
        return ''
    # Прогреваем пиров, иначе парсер выкинет неизвестных молча
    try:
        await client.get_users(ids)
    except Exception as e:
        logger.error(f"Прогрев пиров для отметок {chat_id}: {e}")
    return ''.join(f'<a href="tg://user?id={i}">{TAG_ANCHOR}</a>' for i in ids)


def register_send_result(db, chat_id: int, ok: bool, err: str) -> bool:
    """Пишем итог в send_status. Фатальные ошибки выключают чат.
    Возвращает True если чат выключен."""
    send_status[chat_id] = {'ok': ok, 'error': err or '', 'at': _time.time()}
    if not ok and _is_fatal_error_msg(err):
        try:
            db.stop_spam_for_channel(chat_id)
        except Exception:
            pass
        send_status[chat_id]['disabled'] = True
        logger.warning(f"Чат {chat_id} выключен: фатальная ошибка отправки: {err}")
        return True
    return False


async def _deliver(chat_id: int, text: str,
                   photo_path: str = None, video_path: str = None, fwd=None, topic: int = 0,
                   mention: str = ''):
    """Отправка без исключений наружу (кроме FloodWait/Cancelled).
    Возвращает (ok, error). При битой HTML-разметке — повтор plain-текстом.
    fwd = (from_chat_id, message_id): переслать как есть (премиум-эмодзи целы),
    текст следом отдельным сообщением. topic = тема форума (0 = General).
    mention = готовый HTML-хвост скрытых отметок (уже после конвертации)."""
    if not text and not photo_path and not video_path and not fwd and not mention:
        return True, ''
    if fwd is not None:
        try:
            fwd_ids = fwd[1] if isinstance(fwd[1], list) else [fwd[1]]
            if topic:
                await _forward_to_topic(chat_id, fwd[0], fwd_ids, int(topic))
            else:
                await client.forward_messages(chat_id, fwd[0], fwd_ids)
        except (FloodWait, asyncio.CancelledError):
            raise
        except Exception as e:
            return False, str(e)
        if not text and not mention:
            return True, ''
        photo_path = video_path = None
    try:
        await _send_with_fallback(chat_id, text, photo_path=photo_path, video_path=video_path,
                                  topic=topic, mention=mention)
        return True, ''
    except (FloodWait, asyncio.CancelledError):
        raise
    except Exception as e:
        if _is_parse_error(e):
            logger.warning(f"HTML не парсится для {chat_id} ({e}), шлю plain-текстом")
            try:
                plain = strip_html_tags(text) + strip_html_tags(mention)
                if photo_path:
                    await _send_with_fallback(chat_id, plain, photo_path=photo_path, topic=topic)
                elif video_path:
                    await _send_with_fallback(chat_id, plain, video_path=video_path, topic=topic)
                elif plain:
                    await client.send_message(chat_id, plain,
                                              reply_to_message_id=int(topic) if topic else None)
                return True, ''
            except (FloodWait, asyncio.CancelledError):
                raise
            except Exception as e2:
                return False, str(e2)
        return False, str(e)


SEND_STAGGER_SEC = 5  # пауза между отправками в РАЗНЫЕ чаты (антифлуд)
POLL_STEP_SEC = 15  # гранулярность проверки флага/расписания
SEND_ATTEMPTS = 3  # попыток отправки в чат подряд
RETRY_DELAY_SEC = 10  # пауза между попытками в один чат


async def _send_with_retries(chat_id: int, text: str,
                             photo_path: str = None, video_path: str = None,
                             fwd=None, topic: int = 0, mention: str = '',
                             attempts: int = SEND_ATTEMPTS):
    """Пробуем отправить несколько раз подряд (FloodWait/отмена — наружу).
    Фатальные ошибки не ретраим (бессмысленно). Возвращает (ok, err, tries)."""
    last_err = ''
    tries = 0
    for n in range(max(1, attempts)):
        tries += 1
        try:
            ok, err = await _deliver(chat_id, text, photo_path, video_path, fwd, topic, mention)
        except (FloodWait, asyncio.CancelledError):
            raise
        if ok:
            return True, '', tries
        last_err = err
        if _is_fatal_error_msg(err):
            return False, err, tries
        if n < max(1, attempts) - 1:
            logger.warning(f"Попытка {tries} в {chat_id} не удалась ({err}), повтор через {RETRY_DELAY_SEC}с")
            await asyncio.sleep(RETRY_DELAY_SEC)
    return False, last_err, tries


async def _log_send(db, chat: Dict[str, Any], text: str,
                    photo_path: str = None, video_path: str = None, fwd=None) -> None:
    """Компактная копия отправленного поста в лог-чат (если включён):
    шапка с #log + сам пост цитатой (длинное сворачивается клиентом).
    Форвард логируется форвардом. Никогда не роняет цикл рассылки."""
    try:
        enabled, target = db.get_log_config()
    except Exception:
        return
    if not enabled or not target:
        return
    try:
        dest = int(str(target).strip())
    except (ValueError, TypeError):
        dest = str(target).strip()
    if not dest:
        return
    from pyrogram.enums import ParseMode as PyroParseMode
    import html as _hmod
    title = chat.get('title') or str(chat.get('id'))
    header = (f'📤 <code>{chat.get("id")}</code> {_hmod.escape(str(title))} · '
              f'{_time.strftime("%H:%M", _time.localtime(_time.time()))}\n#log')
    body = _to_html(text) if text else ''

    async def _safe_send_text(html_msg: str):
        try:
            await client.send_message(dest, html_msg, parse_mode=PyroParseMode.HTML)
        except (FloodWait, asyncio.CancelledError):
            raise
        except Exception as e:
            if _is_parse_error(e):
                try:
                    await client.send_message(dest, strip_html_tags(html_msg))
                    return
                except (FloodWait, asyncio.CancelledError):
                    raise
                except Exception as e2:
                    logger.warning(f"Лог plain тоже не ушёл в {dest}: {e2}")
                    return
            logger.warning(f"Не смог записать в лог-чат {dest}: {e}")

    media_path = _resolve_media(photo_path) if photo_path else None
    is_video = False
    if not media_path and video_path:
        media_path = _resolve_media(video_path)
        is_video = bool(media_path)
    try:
        if fwd is not None:
            await _safe_send_text(header)
            try:
                fwd_ids = fwd[1] if isinstance(fwd[1], list) else [fwd[1]]
                await client.forward_messages(dest, fwd[0], fwd_ids)
            except (FloodWait, asyncio.CancelledError):
                raise
            except Exception as e:
                logger.warning(f"Лог-форвард не ушёл в {dest}: {e}")
                return
            if body:
                await _safe_send_text(f'<blockquote>{body}</blockquote>')
        elif media_path:
            try:
                if is_video:
                    await client.send_video(dest, media_path, caption=header,
                                            parse_mode=PyroParseMode.HTML)
                else:
                    await client.send_photo(dest, media_path, caption=header,
                                            parse_mode=PyroParseMode.HTML)
            except (FloodWait, asyncio.CancelledError):
                raise
            except Exception as e:
                if _is_parse_error(e):
                    plain_cap = strip_html_tags(header) or None
                    try:
                        if is_video:
                            await client.send_video(dest, media_path, caption=plain_cap)
                        else:
                            await client.send_photo(dest, media_path, caption=plain_cap)
                    except (FloodWait, asyncio.CancelledError):
                        raise
                    except Exception as e2:
                        logger.warning(f"Лог-медиа не ушло в {dest}: {e2}")
                        return
                else:
                    logger.warning(f"Лог-медиа не ушло в {dest}: {e}")
                    return
            if body:
                await _safe_send_text(f'<blockquote>{body}</blockquote>')
        elif body:
            await _safe_send_text(f'{header}\n<blockquote>{body}</blockquote>')
        else:
            await _safe_send_text(header)
    except (FloodWait, asyncio.CancelledError):
        raise
    except Exception as e:
        logger.warning(f"Не смог записать в лог-чат {dest}: {e}")


def _effective_delay(user_minutes, slow_sec: int, sync_on: bool) -> int:
    """Итоговая пауза в секундах: КД юзера, но не короче слоумода канала
    (если включён SYNC). «Впритык» — ровно max из двух."""
    try:
        base = max(1, int(user_minutes or 5)) * 60
    except (TypeError, ValueError):
        base = 5 * 60
    try:
        slow = int(slow_sec or 0)
    except (TypeError, ValueError):
        slow = 0
    if sync_on and slow > base:
        return slow
    return base


def _chat_sync_on(db, chat_id: int) -> bool:
    """Синх КД со слоумодом для чата: глобальный тумблер И per-chat флаг.
    Интервал юзера при этом всегда остаётся минимумом (см. _effective_delay)."""
    glob = True
    try:
        get_g = getattr(db, 'get_sync_slowmode', None)
        if get_g is not None:
            glob = get_g() == 1
    except Exception:
        pass
    per = True
    try:
        get_p = getattr(db, 'get_sync_slow', None)
        if get_p is not None:
            per = int(get_p(chat_id) or 0) == 1
    except Exception:
        pass
    return bool(glob and per)


async def get_channel_slowmode(chat_id: int, db) -> int:
    """Слоумод канала в секундах (0 = нет). Кэшируем в БД на SLOWMODE_TTL.
    Определяем через GetFullChannel — код сам видит КД канала."""
    cached, at = 0, 0.0
    try:
        cached, at = db.get_slowmode(chat_id)
    except Exception:
        pass
    if at and _time.time() - at < SLOWMODE_TTL:
        return cached
    if not await ensure_connected():
        return cached
    try:
        from pyrogram import raw
        peer = await client.resolve_peer(chat_id)
        full = await client.invoke(raw.functions.channels.GetFullChannel(channel=peer))
        sec = int(getattr(full.full_chat, 'slowmode_seconds', 0) or 0)
    except Exception as e:
        logger.error(f"Не смог прочитать слоумод {chat_id}: {e}")
        return cached
    try:
        db.set_slowmode(chat_id, sec)
    except Exception:
        pass
    if sec != cached:
        logger.info(f"Слоумод {chat_id}: {sec}с")
    return sec


FORUM_TTL = 24 * 3600  # как часто перепроверяем флаг форума (сек)


async def detect_forum(chat_id: int):
    """Форум ли чат: True/False/None (неизвестно — сеть упала и т.п.).
    Смотрим флаг forum у Channel; обычные группы — сразу False без запросов."""
    if not await ensure_connected():
        return None
    try:
        from pyrogram import raw
        peer = await client.resolve_peer(chat_id)
    except (FloodWait, asyncio.CancelledError):
        raise
    except Exception as e:
        logger.error(f"Не смог резолвить пир {chat_id}: {e}")
        return None
    if not isinstance(peer, raw.types.InputPeerChannel):
        return False
    try:
        res = await client.invoke(raw.functions.channels.GetChannels(id=[peer]))
    except (FloodWait, asyncio.CancelledError):
        raise
    except Exception as e:
        logger.error(f"Не смог прочитать флаг форума {chat_id}: {e}")
        return None
    try:
        chats = getattr(res, 'chats', []) or []
        if not chats:
            return None
        return bool(getattr(chats[0], 'forum', False))
    except Exception:
        return None


async def refresh_forum_flag(chat_id: int, db, force: bool = False):
    """Обновить флаг форума в БД (пишем только точный ответ, не 'не знаю')."""
    try:
        _is_forum, _at = db.get_forum(chat_id)
    except Exception:
        _is_forum, _at = 1, 0.0
    if not force and _at and _time.time() - _at < FORUM_TTL:
        return _is_forum == 1
    try:
        res = await detect_forum(chat_id)
    except (FloodWait, asyncio.CancelledError):
        raise
    except Exception:
        return None
    if res is None:
        return None
    try:
        db.set_forum(chat_id, res)
    except Exception:
        pass
    return res


async def get_forum_topics(chat_id: int):
    """Темы форума-группы: [{'id': top_msg_id, 'title': ...}].
    Пустой список = не форум или только General. Бросает исключение
    с понятным текстом, если прочитать нельзя."""
    if not await ensure_connected():
        raise RuntimeError('Pyrogram не подключён')
    try:
        from pyrogram import raw
        peer = await client.resolve_peer(chat_id)
        res = await client.invoke(raw.functions.channels.GetForumTopics(
            channel=peer, offset_date=0, offset_id=0, offset_topic=0,
            limit=100))
    except Exception as e:
        raise RuntimeError(f'Темы не читаются: {e}')
    topics = []
    try:
        raw_topics = getattr(res, 'topics', []) or []
    except Exception:
        raw_topics = []
    for t in raw_topics:
        try:
            tid = int(getattr(t, 'top_message', 0) or getattr(t, 'id', 0) or 0)
            title = str(getattr(t, 'title', '') or '')
        except Exception:
            continue
        if tid > 0:
            topics.append({'id': tid, 'title': title})
    return topics


async def _forward_to_topic(chat_id: int, from_chat_id: int, message_ids, topic_id: int):
    """Форвард в конкретную тему форума (raw: top_msg_id)."""
    from pyrogram import raw
    import random as _random
    mids = list(message_ids) if isinstance(message_ids, list) else [message_ids]
    try:
        random_ids = [client.rnd_id() for _ in mids]
    except Exception:
        random_ids = [_random.getrandbits(63) for _ in mids]
    return await client.invoke(
        raw.functions.messages.ForwardMessages(
            to_peer=await client.resolve_peer(chat_id),
            from_peer=await client.resolve_peer(from_chat_id),
            id=mids,
            top_msg_id=int(topic_id),
            random_id=random_ids,
        ))


def load_schedule(db, ids) -> Dict[int, float]:
    """Расписание из БД (переживает перезапуск). Нет данных = слать сразу."""
    ids = set(ids)
    try:
        rows = db.c.execute('SELECT CHANNEL, SEND_NEXT FROM CHANNELS').fetchall()
        return {int(r[0]): float(r[1] or 0) for r in rows if int(r[0]) in ids}
    except Exception:
        pass
    sched: Dict[int, float] = {}
    get = getattr(db, 'get_send_next', None)
    if get:
        for cid in ids:
            try:
                sched[cid] = float(get(cid) or 0)
            except Exception:
                pass
    return sched


def save_schedule(db, chat_id: int, ts: float) -> None:
    """Сохранить следующий дедлайн чата (best effort)."""
    try:
        setter = getattr(db, 'set_send_next', None)
        if setter:
            setter(chat_id, ts)
    except Exception as e:
        logger.error(f"Не сохранил расписание {chat_id}: {e}")


def _plan_sends(roster, statuses, next_at, now):
    """Чистая функция планировщика.

    Каждый чат живёт по своему таймеру: due — кому пора слать прямо сейчас,
    wait — через сколько проснуться (до ближайшего дедлайна).
    """
    due = [c for c in roster
           if statuses.get(c['id']) == 1 and next_at.get(c['id'], 0) <= now]
    future = [t for cid, t in next_at.items()
              if t > now and statuses.get(cid) == 1]
    wait = (min(future) - now) if future else POLL_STEP_SEC
    return due, max(0, wait)


async def spamming(spam_list: List[Dict[str, Any]], settings: tuple, db) -> None:
    """Рассылка с независимыми таймерами: каждый чат шлётся по своему
    интервалу, а не строго по очереди со слипом на весь таймаут."""
    if not await ensure_connected():
        has_session = os.path.exists("session.session")
        if not has_session:
            await notify_admin('⚠️ Рассылка не запустилась: Pyrogram не подключён. Используй /login.')
            try:
                db.setSpam(0)
            except Exception:
                pass
        else:
            # сеть упала, сессия есть — флаг не гасим, watchdog перезапустит
            await notify_admin('⚠️ Рассылка ждёт сеть: соединение потеряно, продолжу автоматически.')
        return

    conn_failures = 0
    roster = list(spam_list)
    next_at: Dict[int, float] = load_schedule(db, [c['id'] for c in roster])
    if next_at:
        logger.info(f"Расписание восстановлено из БД для {len(next_at)} чатов")
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
            try:
                _dt = int(default_timeout)
                default_timeout = _dt if _dt >= 1 else 5
            except (TypeError, ValueError):
                default_timeout = 5

            # Освежаем ростер (get_chats кэшируется на 60с — дёшево)
            try:
                fresh = await get_chats()
                if fresh:
                    roster = fresh
            except Exception as e:
                logger.error(f"Ошибка обновления ростера: {e}")
            roster_ids = {c['id'] for c in roster}
            for k in [k for k in next_at if k not in roster_ids]:
                del next_at[k]

            # Статусы одним запросом (fallback — поштучно для чужих реализаций db)
            try:
                rows = db.c.execute('SELECT CHANNEL, SPAM_ENABLED FROM CHANNELS').fetchall()
                statuses = {int(r[0]): (r[1] or 0) for r in rows}
            except Exception:
                statuses = {}
                for chat in roster:
                    try:
                        statuses[chat['id']] = db.get_channel_spam_status(chat['id'])
                    except Exception:
                        pass

            now = _time.time()
            due, wait = _plan_sends(roster, statuses, next_at, now)
            if not due:
                await asyncio.sleep(min(wait, POLL_STEP_SEC))
                continue

            for chat in due:
                try:
                    settings = db.settings()
                except Exception:
                    settings = None
                if not settings or settings[4] != 1:
                    break

                if not await ensure_connected():
                    conn_failures += 1
                    if conn_failures >= 3:
                        await notify_admin('⚠️ Рассылка остановлена: Pyrogram не подключён. Используй /login.')
                        try:
                            db.setSpam(0)
                        except Exception:
                            pass
                        return
                    await asyncio.sleep(30)
                    break
                conn_failures = 0

                # Свежий доп. текст на каждую отправку
                try:
                    addit = db.get_additional_text(chat['id'])
                    chat['text'] = addit[0] if addit and addit[0] else ''
                except Exception as e:
                    logger.error(f"Ошибка доп. текста {chat.get('id')}: {e}")
                    chat['text'] = ''

                # Флаг форума освежаем по TTL (не ломаем отправку при ошибке)
                try:
                    await refresh_forum_flag(chat['id'], db)
                except (FloodWait, asyncio.CancelledError):
                    raise
                except Exception:
                    pass

                try:
                    channel_post = db.get_channel_post(chat['id'])
                    photo_path = video_path = None
                    fwd = None
                    try:
                        _fc, _fm = db.get_channel_forward(chat['id'])
                        if _fc and _fm:
                            fwd = (int(_fc), int(_fm))
                    except Exception:
                        fwd = None
                    if channel_post and (channel_post[0] or channel_post[1] or channel_post[2]):
                        text = channel_post[2] or ''
                        if chat.get('text'):
                            text = f"{text}\n\n{chat['text']}" if text else chat['text']
                        if channel_post[0]:
                            photo_path = f"{config.DIR}{channel_post[0]}" if config.DIR else channel_post[0]
                        elif channel_post[1]:
                            video_path = f"{config.DIR}{channel_post[1]}" if config.DIR else channel_post[1]
                    else:
                        # settings: [0]=ID, [1]=PHOTO, [2]=VIDEO, [3]=TEXT, [4]=SPAM, [5]=TIMEOUT,
                        # [6]=FWD_CHAT, [7]=FWD_MSG
                        text = settings[3] or ''
                        if chat.get('text'):
                            text = f"{text}\n\n{chat['text']}" if text else chat['text']
                        if settings[1]:
                            photo_path = f"{config.DIR}{settings[1]}" if config.DIR else settings[1]
                        elif settings[2]:
                            video_path = f"{config.DIR}{settings[2]}" if config.DIR else settings[2]
                        if fwd is None and len(settings) > 7 and settings[6] and settings[7]:
                            try:
                                fwd = (int(settings[6]), int(settings[7]))
                            except (TypeError, ValueError):
                                fwd = None

                    # Индивидуальный таймаут чата, иначе глобальный
                    try:
                        per_chat = db.get_channel_timeout(chat['id'])
                    except Exception:
                        per_chat = None
                    timeout = per_chat if per_chat and per_chat >= 1 else default_timeout

                    # Тема форума (0 = General) и слоумод канала (КД впритык)
                    try:
                        topic_id, _topic_name = db.get_topic(chat['id'])
                    except Exception:
                        topic_id = 0
                    try:
                        topic_id = int(topic_id or 0)
                    except (TypeError, ValueError):
                        topic_id = 0
                    slow = await get_channel_slowmode(chat['id'], db)
                    sync_on = _chat_sync_on(db, chat['id'])
                    delay = _effective_delay(timeout, slow, sync_on)

                    try:
                        mention = await build_mentions(chat['id'], db)
                    except Exception as e:
                        logger.error(f"Отметки для {chat['id']}: {e}")
                        mention = ''

                    try:
                        ok, err, tries = await _send_with_retries(
                            chat['id'], text, photo_path, video_path,
                            fwd=fwd, topic=topic_id, mention=mention)
                    except FloodWait as e:
                        wait = int(getattr(e, 'value', 30) or 30)
                        logger.warning(f"FloodWait {wait}s для {chat['id']}, повтор позже")
                        register_send_result(db, chat['id'], False,
                                             f'FloodWait {wait}s, повтор позже')
                        next_at[chat['id']] = _time.time() + wait
                        save_schedule(db, chat['id'], next_at[chat['id']])
                        continue
                    next_at[chat['id']] = _time.time() + delay
                    save_schedule(db, chat['id'], next_at[chat['id']])
                    disabled = register_send_result(db, chat['id'], ok, err)
                    if not ok:
                        logger.error(f"Ошибка отправки в {chat['id']} после {tries} попыток: {err}")
                        if disabled:
                            await notify_admin(
                                f'⛔ Чат {chat["id"]} выключен из рассылки: {err}')
                    else:
                        logger.info(f"Отправлено в {chat['id']} с {tries} попытки, "
                                    f"следующее через {delay}с"
                                    + (f" (тема {topic_id})" if topic_id else "")
                                    + (f" [слоумод {slow}с]" if sync_on and slow * 1.0 > timeout * 60 else ""))
                        try:
                            await _log_send(db, chat, text, photo_path, video_path, fwd=fwd)
                        except asyncio.CancelledError:
                            raise
                        except Exception as e:
                            logger.warning(f"Лог не записался для {chat['id']}: {e}")

                    await asyncio.sleep(SEND_STAGGER_SEC)

                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"Ошибка отправки в {chat['id']}: {e}")
                    register_send_result(db, chat['id'], False, str(e))
                    next_at[chat['id']] = _time.time() + 60
                    save_schedule(db, chat['id'], next_at[chat['id']])
                    await asyncio.sleep(10)

    except asyncio.CancelledError:
        logger.info("Спам-цикл остановлен")
        raise
    except Exception as e:
        logger.error(f"Ошибка в spamming: {e}")
