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
                   photo_path: str = None, video_path: str = None):
    """Отправка без исключений наружу (кроме FloodWait/Cancelled).
    Возвращает (ok, error). При битой HTML-разметке — повтор plain-текстом."""
    if not text and not photo_path and not video_path:
        return True, ''
    try:
        await _send_with_fallback(chat_id, text, photo_path=photo_path, video_path=video_path)
        return True, ''
    except (FloodWait, asyncio.CancelledError):
        raise
    except Exception as e:
        if _is_parse_error(e):
            logger.warning(f"HTML не парсится для {chat_id} ({e}), шлю plain-текстом")
            try:
                plain = strip_html_tags(text)
                if photo_path:
                    await _send_with_fallback(chat_id, plain, photo_path=photo_path)
                elif video_path:
                    await _send_with_fallback(chat_id, plain, video_path=video_path)
                elif plain:
                    await client.send_message(chat_id, plain)
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
                             attempts: int = SEND_ATTEMPTS):
    """Пробуем отправить несколько раз подряд (FloodWait/отмена — наружу).
    Фатальные ошибки не ретраим (бессмысленно). Возвращает (ok, err, tries)."""
    last_err = ''
    tries = 0
    for n in range(max(1, attempts)):
        tries += 1
        try:
            ok, err = await _deliver(chat_id, text, photo_path, video_path)
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
                    photo_path: str = None, video_path: str = None) -> None:
    """Компактная копия отправленного поста в лог-чат (если включён):
    шапка с #log + сам пост цитатой (длинное сворачивается клиентом).
    Никогда не роняет цикл рассылки."""
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
        if media_path:
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

                try:
                    channel_post = db.get_channel_post(chat['id'])
                    photo_path = video_path = None
                    if channel_post and (channel_post[0] or channel_post[1] or channel_post[2]):
                        text = channel_post[2] or ''
                        if chat.get('text'):
                            text = f"{text}\n\n{chat['text']}" if text else chat['text']
                        if channel_post[0]:
                            photo_path = f"{config.DIR}{channel_post[0]}" if config.DIR else channel_post[0]
                        elif channel_post[1]:
                            video_path = f"{config.DIR}{channel_post[1]}" if config.DIR else channel_post[1]
                    else:
                        # settings: [0]=ID, [1]=PHOTO, [2]=VIDEO, [3]=TEXT, [4]=SPAM, [5]=TIMEOUT
                        text = settings[3] or ''
                        if chat.get('text'):
                            text = f"{text}\n\n{chat['text']}" if text else chat['text']
                        if settings[1]:
                            photo_path = f"{config.DIR}{settings[1]}" if config.DIR else settings[1]
                        elif settings[2]:
                            video_path = f"{config.DIR}{settings[2]}" if config.DIR else settings[2]

                    # Индивидуальный таймаут чата, иначе глобальный
                    try:
                        per_chat = db.get_channel_timeout(chat['id'])
                    except Exception:
                        per_chat = None
                    timeout = per_chat if per_chat and per_chat >= 1 else default_timeout

                    try:
                        ok, err, tries = await _send_with_retries(
                            chat['id'], text, photo_path, video_path)
                    except FloodWait as e:
                        wait = int(getattr(e, 'value', 30) or 30)
                        logger.warning(f"FloodWait {wait}s для {chat['id']}, повтор позже")
                        register_send_result(db, chat['id'], False,
                                             f'FloodWait {wait}s, повтор позже')
                        next_at[chat['id']] = _time.time() + wait
                        save_schedule(db, chat['id'], next_at[chat['id']])
                        continue
                    next_at[chat['id']] = _time.time() + timeout * 60
                    save_schedule(db, chat['id'], next_at[chat['id']])
                    disabled = register_send_result(db, chat['id'], ok, err)
                    if not ok:
                        logger.error(f"Ошибка отправки в {chat['id']} после {tries} попыток: {err}")
                        if disabled:
                            await notify_admin(
                                f'⛔ Чат {chat["id"]} выключен из рассылки: {err}')
                    else:
                        logger.info(f"Отправлено в {chat['id']} с {tries} попытки, следующее через {timeout} мин.")
                        try:
                            await _log_send(db, chat, text, photo_path, video_path)
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
