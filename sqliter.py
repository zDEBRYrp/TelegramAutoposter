import sqlite3
import config
import logging
import os
from typing import Optional, Tuple, Any

logger = logging.getLogger(__name__)

# Маркер "текст уже готовый HTML из entities сообщения".
# Такие тексты markdown_to_html возвращает как есть, без повторной конвертации.
HTML_READY_MARK = '\u200b'


def entities_to_html(text: str, entities) -> str:
    """Форматирование Telegram-клиента (entities) -> HTML.

    Смещения entities в UTF-16 кодовых единицах — маппим через байты.
    Неизвестные/служебные entities (mention, hashtag, команды...) игнорируем,
    их текст остаётся как есть.
    """
    import html as _h
    if not text:
        return ''
    if not entities:
        return _h.escape(text)

    raw = text.encode('utf-16-le')
    total_units = len(raw) // 2

    def tags_for(ent):
        t = ent.type
        if t == 'bold':
            return '<b>', '</b>'
        if t == 'italic':
            return '<i>', '</i>'
        if t == 'underline':
            return '<u>', '</u>'
        if t == 'strikethrough':
            return '<s>', '</s>'
        if t == 'spoiler':
            return '<tg-spoiler>', '</tg-spoiler>'
        if t == 'code':
            return '<code>', '</code>'
        if t == 'pre':
            return '<pre>', '</pre>'
        if t == 'text_link' and getattr(ent, 'url', None):
            return f'<a href="{_h.escape(ent.url, quote=True)}">', '</a>'
        if t == 'text_mention' and getattr(ent, 'user', None):
            uid = getattr(ent.user, 'id', '')
            return f'<a href="tg://user?id={uid}">', '</a>'
        if t == 'blockquote':
            return '<blockquote>', '</blockquote>'
        if t == 'expandable_blockquote':
            return '<blockquote expandable>', '</blockquote>'
        return None, None  # custom_emoji и прочие: текст без тегов

    bounds = {0, total_units}
    spans = []
    for ent in entities:
        try:
            start = int(ent.offset)
            end = start + int(ent.length)
        except (TypeError, ValueError):
            continue
        if start < 0 or end <= start:
            continue
        start = min(start, total_units)
        end = min(end, total_units)
        bounds.add(start)
        bounds.add(end)
        spans.append((start, end, ent))
    # внешние — раньше: сначала меньший offset, потом большая длина
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))

    points = sorted(bounds)
    starts: dict[int, list[tuple[int, str, str]]] = {}
    for (s, e, ent) in spans:
        o, cl = tags_for(ent)
        if o:
            starts.setdefault(s, []).append((e, o, cl))
    for s in starts:
        starts[s].sort(key=lambda t: -t[0])  # внешние (длинные) открываем первыми

    out: list[str] = []
    stack: list[tuple[int, str, str]] = []  # (end, open, close), верх — самый внутренний

    def _open_at(p: int):
        for (e, o, cl) in starts.get(p, []):
            out.append(o)
            stack.append((e, o, cl))

    def _close_at(p: int):
        while True:
            idx = next((i for i in range(len(stack) - 1, -1, -1) if stack[i][0] == p), None)
            if idx is None:
                break
            while len(stack) > idx:
                out.append(stack.pop()[2])

    prev = points[0]
    _open_at(prev)
    for p in points[1:]:
        chunk = raw[prev * 2:p * 2].decode('utf-16-le')
        if chunk:
            out.append(_h.escape(chunk))
        _close_at(p)
        _open_at(p)
        prev = p
    while stack:  # на всякий случай (точки покрывают все концы, но мало ли)
        out.append(stack.pop()[2])
    return ''.join(out)


def message_to_html(text: str | None, entities) -> str:
    """Текст входящего сообщения -> то, что кладём в БД.

    Есть entities (форматирование кнопками клиента) — возвращаем готовый HTML
    с маркером; нет — сырой текст, markdown доконвертится при отправке.
    """
    if entities:
        return HTML_READY_MARK + entities_to_html(text or '', entities)
    return text or ''


def markdown_to_html(text: str) -> str:
    """Конвертирует Markdown в HTML для Telegram.

    Код (```блоки``` и `инлайн`) выносится в плейсхолдеры до
    форматирования, чтобы **жирный** внутри кода не ломал разметку.
    Текст с маркером HTML_READY_MARK (уже готовый HTML из entities)
    возвращается как есть.
    """
    if not text:
        return text
    if text.startswith(HTML_READY_MARK):
        return text[len(HTML_READY_MARK):]
    import re

    # Экранируем HTML спецсимволы сначала
    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    placeholders: dict[str, str] = {}

    def _store(html: str) -> str:
        key = f"\x00CODE{len(placeholders)}\x00"
        placeholders[key] = html
        return key

    # Блок кода ```...``` — первым
    text = re.sub(r'```(?:\w+\n)?(.*?)```',
                  lambda m: _store(f'<pre>{m.group(1)}</pre>'),
                  text, flags=re.DOTALL)
    # Инлайн-код `...`
    text = re.sub(r'`([^`\n]+?)`',
                  lambda m: _store(f'<code>{m.group(1)}</code>'),
                  text)

    # Цитаты "> строка" (после экранирования это "&gt;") — группируем подряд
    lines = text.split('\n')
    out_lines: list[str] = []
    quote_buf: list[str] = []
    for line in lines:
        if line.startswith('&gt; ') or line == '&gt;':
            quote_buf.append(line[5:] if line.startswith('&gt; ') else '')
        else:
            if quote_buf:
                out_lines.append('<blockquote>' + '\n'.join(quote_buf) + '</blockquote>')
                quote_buf = []
            out_lines.append(line)
    if quote_buf:
        out_lines.append('<blockquote>' + '\n'.join(quote_buf) + '</blockquote>')
    text = '\n'.join(out_lines)

    # Жирный **text** или __text__
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text, flags=re.DOTALL)
    text = re.sub(r'__(.+?)__', r'<b>\1</b>', text, flags=re.DOTALL)

    # Курсив *text* или _text_
    text = re.sub(r'\*([^*\n]+?)\*', r'<i>\1</i>', text)
    text = re.sub(r'(?<!\w)_([^_\n]+?)_(?!\w)', r'<i>\1</i>', text)

    # Зачёркнутый ~~text~~
    text = re.sub(r'~~(.+?)~~', r'<s>\1</s>', text, flags=re.DOTALL)

    # Спойлер ||text||
    text = re.sub(r'\|\|(.+?)\|\|', r'<tg-spoiler>\1</tg-spoiler>', text, flags=re.DOTALL)

    # Ссылки [text](url): разрешаем https://, tg://, t.me/..., @user
    def _link(m: 're.Match') -> str:
        title, url = m.group(1), m.group(2).strip()
        if url.startswith('@'):
            url = 'https://t.me/' + url[1:]
        elif url.startswith(('t.me/', 'telegram.me/')):
            url = 'https://' + url
        if not url.startswith(('http://', 'https://', 'tg://')):
            return m.group(0)  # не ссылка — оставляем как есть
        return f'<a href="{url}">{title}</a>'

    text = re.sub(r'\[(.+?)\]\(([^)\s]+)\)', _link, text)

    for key, html in placeholders.items():
        text = text.replace(key, html)

    return text


class DBConnection(object):
    def __init__(self, db_path: Optional[str] = None):
        if db_path:
            self.db_path = db_path
        else:
            self.db_path = f'{config.DIR}database.db' if getattr(config, 'DIR', '') else 'database.db'
        # timeout: второй процесс (дубль бота) может держать блокировку —
        # ждём, а не падаем сразу с "database is locked"
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30.0)
        self.c = self.conn.cursor()
        self.init_db()
    
    def init_db(self):
        try:
            self.c.execute('''
                CREATE TABLE IF NOT EXISTS SETTINGS (
                    ID INTEGER PRIMARY KEY,
                    PHOTO TEXT DEFAULT '',
                    VIDEO TEXT DEFAULT '',
                    TEXT TEXT DEFAULT 'Текст по умолчанию',
                    SPAM INTEGER DEFAULT 0,
                    TIMEOUT INTEGER DEFAULT 5
                )
            ''')
            
            self.c.execute('''
                CREATE TABLE IF NOT EXISTS CHANNELS (
                    CHANNEL INTEGER PRIMARY KEY,
                    ADDITIONAL TEXT DEFAULT '',
                    SPAM_ENABLED INTEGER DEFAULT 1,
                    TIMEOUT INTEGER DEFAULT 5,
                    POST_PHOTO TEXT DEFAULT '',
                    POST_VIDEO TEXT DEFAULT '',
                    POST_TEXT TEXT DEFAULT ''
                )
            ''')

            # Миграции старых баз: докладываем колонки, которых не было
            self._ensure_column('SETTINGS', 'LOG_ENABLED', 'INTEGER DEFAULT 0')
            self._ensure_column('SETTINGS', 'LOG_CHAT', "TEXT DEFAULT ''")
            self._ensure_column('SETTINGS', 'REPORT_ENABLED', 'INTEGER DEFAULT 1')
            self._ensure_column('CHANNELS', 'SEND_NEXT', 'REAL DEFAULT 0')
            
            self.c.execute('SELECT * FROM SETTINGS WHERE ID = 1')
            if self.c.fetchone() is None:
                self.c.execute('INSERT INTO SETTINGS (ID, PHOTO, VIDEO, TEXT, SPAM, TIMEOUT) VALUES (?, ?, ?, ?, ?, ?)',
                              [1, '', '', 'Текст по умолчанию', 0, 5])
            
            self.conn.commit()
            logger.info("База данных инициализирована успешно")
            
        except Exception as e:
            logger.error(f"Ошибка инициализации БД: {e}")
            raise

    def _ensure_column(self, table: str, column: str, ddl: str) -> None:
        try:
            cols = [r[1] for r in self.c.execute(f'PRAGMA table_info({table})').fetchall()]
            if column not in cols:
                self.c.execute(f'ALTER TABLE {table} ADD COLUMN {column} {ddl}')
                self.conn.commit()
                logger.info(f"Миграция БД: {table}.{column} добавлена")
        except Exception as e:
            logger.error(f"Ошибка миграции {table}.{column}: {e}")
    
    def add_additional_text(self, channel_id: int, text: str) -> bool:
        try:
            self.c.execute('SELECT ADDITIONAL FROM CHANNELS WHERE CHANNEL = ?', [str(channel_id)])
            table = self.c.fetchone()
            if table is None:
                self.c.execute('INSERT INTO CHANNELS(CHANNEL, ADDITIONAL) VALUES (?, ?)', [str(channel_id), str(text)])
            else:
                self.c.execute('UPDATE CHANNELS SET ADDITIONAL = ? WHERE CHANNEL = ?', [str(text), str(channel_id)])
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Ошибка добавления дополнительного текста: {e}")
            return False
    
    def get_additional_text(self, channel_id: int) -> Optional[Tuple[str]]:
        try:
            self.c.execute('SELECT ADDITIONAL FROM CHANNELS WHERE CHANNEL = ?', [str(channel_id)])
            return self.c.fetchone()
        except Exception as e:
            logger.error(f"Ошибка получения дополнительного текста: {e}")
            return None
    
    def get_channel_spam_status(self, channel_id: int) -> int:
        try:
            self.c.execute('SELECT SPAM_ENABLED FROM CHANNELS WHERE CHANNEL = ?', [str(channel_id)])
            result = self.c.fetchone()
            # Чатов нет в БД -> считаем выключенными (раньше возвращалась 1,
            # из-за чего неизвестные чаты спамились)
            return result[0] if result else 0
        except Exception as e:
            logger.error(f"Ошибка получения статуса спама канала: {e}")
            return 0
    
    def change_text(self, text: str) -> bool:
        try:
            self.c.execute('UPDATE SETTINGS SET TEXT = ? WHERE ID = ?', [text, 1])
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Ошибка изменения текста: {e}")
            return False
    
    def change_photo(self, name: str) -> bool:
        try:
            # Храним имя файла как есть (с расширением).
            # Для совместимости со старыми записями без расширения
            # чтение умеет подбирать расширение перебором.
            # Установка фото сбрасывает видео (в посте только одно медиа).
            base_name = os.path.basename(name) if name else ''
            if base_name:
                self.c.execute('UPDATE SETTINGS SET PHOTO = ?, VIDEO = ? WHERE ID = ?', [base_name, '', 1])
            else:
                self.c.execute('UPDATE SETTINGS SET PHOTO = ? WHERE ID = ?', [base_name, 1])
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Ошибка изменения фото: {e}")
            return False

    def change_video(self, name: str) -> bool:
        try:
            base_name = os.path.basename(name) if name else ''
            if base_name:
                self.c.execute('UPDATE SETTINGS SET VIDEO = ?, PHOTO = ? WHERE ID = ?', [base_name, '', 1])
            else:
                self.c.execute('UPDATE SETTINGS SET VIDEO = ? WHERE ID = ?', [base_name, 1])
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Ошибка изменения видео: {e}")
            return False
    
    def settings(self) -> Optional[Tuple]:
        try:
            self.c.execute('SELECT * FROM SETTINGS WHERE ID = ?', [1])
            return self.c.fetchone()
        except Exception as e:
            logger.error(f"Ошибка получения настроек: {e}")
            return (1, '', '', 'Текст по умолчанию', 0, 5)
    
    def setSpam(self, spam: int) -> bool:
        try:
            self.c.execute('UPDATE SETTINGS SET SPAM = ? WHERE ID = ?', [spam, 1])
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Ошибка переключения спама: {e}")
            return False
    
    def setTimeOut(self, time: int) -> bool:
        try:
            self.c.execute('UPDATE SETTINGS SET TIMEOUT = ? WHERE ID = ?', [time, 1])
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Ошибка изменения таймаута: {e}")
            return False

    def get_log_config(self) -> Tuple[int, str]:
        """(LOG_ENABLED 0/1, LOG_CHAT id-or-username)."""
        try:
            self.c.execute('SELECT LOG_ENABLED, LOG_CHAT FROM SETTINGS WHERE ID = ?', [1])
            row = self.c.fetchone()
            if not row:
                return 0, ''
            return (row[0] or 0), (row[1] or '')
        except Exception as e:
            logger.error(f"Ошибка чтения лог-конфига: {e}")
            return 0, ''

    def set_log_enabled(self, enabled: int) -> bool:
        try:
            self.c.execute('UPDATE SETTINGS SET LOG_ENABLED = ? WHERE ID = ?', [1 if enabled else 0, 1])
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Ошибка переключения лога: {e}")
            return False

    def set_log_chat(self, chat: str) -> bool:
        try:
            self.c.execute('UPDATE SETTINGS SET LOG_CHAT = ? WHERE ID = ?', [str(chat or ''), 1])
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Ошибка установки лог-чата: {e}")
            return False

    def get_report_enabled(self) -> int:
        try:
            self.c.execute('SELECT REPORT_ENABLED FROM SETTINGS WHERE ID = ?', [1])
            row = self.c.fetchone()
            return 1 if not row or row[0] is None else int(row[0])
        except Exception as e:
            logger.error(f"Ошибка чтения флага отчёта: {e}")
            return 1

    def set_report_enabled(self, enabled: int) -> bool:
        try:
            self.c.execute('UPDATE SETTINGS SET REPORT_ENABLED = ? WHERE ID = ?', [1 if enabled else 0, 1])
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Ошибка переключения отчёта: {e}")
            return False

    def set_all_channels(self, enabled: int) -> int:
        """Вкл/выкл все чаты разом. Возвращает число затронутых."""
        try:
            self.c.execute('UPDATE CHANNELS SET SPAM_ENABLED = ?', [1 if enabled else 0])
            n = self.c.rowcount if self.c.rowcount is not None and self.c.rowcount >= 0 else 0
            self.conn.commit()
            return n
        except Exception as e:
            logger.error(f"Ошибка массового переключения чатов: {e}")
            return 0

    def get_send_next(self, channel_id: int) -> float:
        """Когда чату пора слать следующий пост (unix time, 0 = сразу)."""
        try:
            self.c.execute('SELECT SEND_NEXT FROM CHANNELS WHERE CHANNEL = ?', [str(channel_id)])
            row = self.c.fetchone()
            if not row or row[0] is None:
                return 0.0
            return float(row[0])
        except Exception as e:
            logger.error(f"Ошибка чтения расписания: {e}")
            return 0.0

    def set_send_next(self, channel_id: int, ts: float) -> bool:
        try:
            self.c.execute('UPDATE CHANNELS SET SEND_NEXT = ? WHERE CHANNEL = ?',
                           [float(ts), str(channel_id)])
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Ошибка записи расписания: {e}")
            return False
    
    def stop_spam_for_channel(self, channel_id: int) -> bool:
        try:
            self.c.execute('UPDATE CHANNELS SET SPAM_ENABLED = 0 WHERE CHANNEL = ?', [str(channel_id)])
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Ошибка остановки спама для канала: {e}")
            return False
    
    def set_channel_timeout(self, channel_id: int, timeout: int) -> bool:
        try:
            self.c.execute('UPDATE CHANNELS SET TIMEOUT = ? WHERE CHANNEL = ?', [timeout, str(channel_id)])
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Ошибка установки таймаута канала: {e}")
            return False
    
    def add_channel(self, channel_id: int) -> bool:
        try:
            self.c.execute('INSERT OR IGNORE INTO CHANNELS (CHANNEL, ADDITIONAL, SPAM_ENABLED, TIMEOUT) VALUES (?, ?, ?, ?)',
                          [str(channel_id), '', 0, 5])
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Ошибка добавления канала: {e}")
            return False
    
    def get_channel_timeout(self, channel_id: int) -> Optional[int]:
        """Таймаут конкретного чата (минуты) или None если чат неизвестен."""
        try:
            self.c.execute('SELECT TIMEOUT FROM CHANNELS WHERE CHANNEL = ?', [str(channel_id)])
            row = self.c.fetchone()
            if row is None or row[0] is None:
                return None
            try:
                return int(row[0])
            except (TypeError, ValueError):
                return None
        except Exception as e:
            logger.error(f"Ошибка получения таймаута канала: {e}")
            return None

    def set_channel_post(self, channel_id: int, photo: Optional[str] = None,
                         video: Optional[str] = None, text: Optional[str] = None) -> bool:
        """Обновить только переданные поля канального поста.

        None = оставить как было (для обратной совместимости '' тоже
        трактуется как 'не передано'). Очистка всего поста — через
        clear_channel_post(). Установка фото сбрасывает видео и наоборот,
        т.к. пост может содержать только один тип медиа.
        """
        try:
            # Нормализуем: '' считаем отсутствием значения, чтобы старые
            # вызовы set_channel_post(chat_id, text=...) не затирали медиа.
            if photo == '':
                photo = None
            if video == '':
                video = None
            if text == '':
                # Пустой текст при установке только медиа — тоже preserve.
                # Явная очистка текста делается через clear_channel_post.
                text = None
            photo_base = os.path.basename(photo) if photo else None
            video_base = os.path.basename(video) if video else None

            existing = self.get_channel_post(channel_id)
            if existing is None:
                new_photo = photo_base or ''
                new_video = video_base or ''
                new_text = text if text is not None else ''
                self.c.execute(
                    'INSERT INTO CHANNELS (CHANNEL, ADDITIONAL, SPAM_ENABLED, TIMEOUT, POST_PHOTO, POST_VIDEO, POST_TEXT) VALUES (?, ?, ?, ?, ?, ?, ?)',
                    [str(channel_id), '', 0, 5, new_photo, new_video, new_text])
            else:
                updates = []
                params: list[Any] = []
                if photo_base is not None:
                    updates.append('POST_PHOTO = ?')
                    params.append(photo_base)
                    # только одно медиа в посте
                    updates.append('POST_VIDEO = ?')
                    params.append('')
                if video_base is not None:
                    updates.append('POST_VIDEO = ?')
                    params.append(video_base)
                    updates.append('POST_PHOTO = ?')
                    params.append('')
                if text is not None:
                    updates.append('POST_TEXT = ?')
                    params.append(text)
                if not updates:
                    return True
                params.append(str(channel_id))
                self.c.execute(
                    f'UPDATE CHANNELS SET {", ".join(updates)} WHERE CHANNEL = ?',
                    params)
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Ошибка установки канального поста: {e}")
            return False
    
    def get_channel_post(self, channel_id: int) -> Optional[Tuple[str, str, str]]:
        try:
            self.c.execute('SELECT POST_PHOTO, POST_VIDEO, POST_TEXT FROM CHANNELS WHERE CHANNEL = ?', [str(channel_id)])
            return self.c.fetchone()
        except Exception as e:
            logger.error(f"Ошибка получения канального поста: {e}")
            return None
    
    def clear_channel_post(self, channel_id: int) -> bool:
        try:
            self.c.execute('UPDATE CHANNELS SET POST_PHOTO = ?, POST_VIDEO = ?, POST_TEXT = ? WHERE CHANNEL = ?',
                          ['', '', '', str(channel_id)])
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Ошибка очистки канального поста: {e}")
            return False
    
    def __del__(self):
        try:
            self.c.close()
            self.conn.close()
        except:
            pass
