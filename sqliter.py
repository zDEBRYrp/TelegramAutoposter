import sqlite3
import config
import logging
import os
from typing import Optional, Tuple, Any

logger = logging.getLogger(__name__)

try:
    import markdown2
    HAS_MARKDOWN = True
except ImportError:
    HAS_MARKDOWN = False


def markdown_to_html(text: str) -> str:
    """Конвертирует Markdown в HTML для Telegram.

    Код (```блоки``` и `инлайн`) выносится в плейсхолдеры до
    форматирования, чтобы **жирный** внутри кода не ломал разметку.
    """
    if not text:
        return text
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

    # Жирный **text** или __text__
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text, flags=re.DOTALL)
    text = re.sub(r'__(.+?)__', r'<b>\1</b>', text, flags=re.DOTALL)

    # Курсив *text* или _text_
    text = re.sub(r'\*([^*\n]+?)\*', r'<i>\1</i>', text)
    text = re.sub(r'(?<!\w)_([^_\n]+?)_(?!\w)', r'<i>\1</i>', text)

    # Зачёркнутый ~~text~~
    text = re.sub(r'~~(.+?)~~', r'<s>\1</s>', text, flags=re.DOTALL)

    # Ссылки [text](url)
    text = re.sub(r'\[(.+?)\]\((https?://[^\)]+)\)', r'<a href="\2">\1</a>', text)

    for key, html in placeholders.items():
        text = text.replace(key, html)

    return text


class DBConnection(object):
    def __init__(self):
        self.db_path = f'{config.DIR}database.db' if hasattr(config, 'DIR') else 'database.db'
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
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
            
            self.c.execute('SELECT * FROM SETTINGS WHERE ID = 1')
            if self.c.fetchone() is None:
                self.c.execute('INSERT INTO SETTINGS (ID, PHOTO, VIDEO, TEXT, SPAM, TIMEOUT) VALUES (?, ?, ?, ?, ?, ?)',
                              [1, '', '', 'Текст по умолчанию', 0, 5])
            
            self.conn.commit()
            logger.info("База данных инициализирована успешно")
            
        except Exception as e:
            logger.error(f"Ошибка инициализации БД: {e}")
            raise
    
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
