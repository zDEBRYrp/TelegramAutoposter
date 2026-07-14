import sqlite3
import config
import logging
import os
from typing import Optional, Tuple, Any

logger = logging.getLogger(__name__)

# Импорт markdown2
try:
    import markdown2
    HAS_MARKDOWN = True
except ImportError:
    HAS_MARKDOWN = False


def markdown_to_html(text: str) -> str:
    """Конвертация Markdown в текст с форматированием для Telegram"""
    if not HAS_MARKDOWN or not text:
        return text
    try:
        html = markdown2.markdown(text, extras=['tables', 'fenced-code-blocks'])
        # Убираем все HTML теги для Telegram
        # Telegram поддерживает только: *bold*, _italic_, ~strikethrough~, `code`, ```pre```, [links](url), @mentions
        # Конвертируем основные теги в Markdown
        text = html
        
        # Убираем все остальные теги
        import re
        text = re.sub(r'<[^>]+>', '', text)
        
        # Конвертируем основные теги в Markdown
        text = re.sub(r'\*\*(.+?)\*\*', r'*\1*', text)  # bold
        text = re.sub(r'<strong>(.+?)</strong>', r'*\1*', text)
        text = re.sub(r'_([^_]+)_', r'_\1_', text)  # italic
        
        return text.strip()
    except Exception:
        return text


class DBConnection(object):
    def __init__(self):
        self.db_path = f'{config.DIR}database.db' if hasattr(config, 'DIR') else 'database.db'
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.c = self.conn.cursor()
        self.init_db()
    
    def init_db(self):
        """Инициализация таблиц БД"""
        try:
            # Таблица настроек
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
            
            # Таблица каналов
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
            
            # Вставка базовой настройки если нет
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
            return result[0] if result else 1
        except Exception as e:
            logger.error(f"Ошибка получения статуса спама канала: {e}")
            return 1
    
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
            # Убираем расширение для совместимости
            base_name = os.path.splitext(name)[0] if name else ''
            self.c.execute('UPDATE SETTINGS SET PHOTO = ? WHERE ID = ?', [base_name, 1])
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Ошибка изменения фото: {e}")
            return False
    
    def change_video(self, name: str) -> bool:
        try:
            base_name = os.path.splitext(name)[0] if name else ''
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
                          [str(channel_id), '', 1, 5])
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Ошибка добавления канала: {e}")
            return False
    
    # Методы для канальных постов
    def set_channel_post(self, channel_id: int, photo: str = '', video: str = '', text: str = '') -> bool:
        """Установка поста для конкретного канала"""
        try:
            photo_base = os.path.splitext(photo)[0] if photo else ''
            video_base = os.path.splitext(video)[0] if video else ''
            self.c.execute('''
                INSERT INTO CHANNELS (CHANNEL, POST_PHOTO, POST_VIDEO, POST_TEXT) 
                VALUES (?, ?, ?, ?)
                ON CONFLICT(CHANNEL) DO UPDATE SET
                    POST_PHOTO = excluded.POST_PHOTO,
                    POST_VIDEO = excluded.POST_VIDEO,
                    POST_TEXT = excluded.POST_TEXT
            ''', [str(channel_id), photo_base, video_base, text])
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Ошибка установки канального поста: {e}")
            return False
    
    def get_channel_post(self, channel_id: int) -> Optional[Tuple[str, str, str]]:
        """Получение поста для конкретного канала"""
        try:
            self.c.execute('SELECT POST_PHOTO, POST_VIDEO, POST_TEXT FROM CHANNELS WHERE CHANNEL = ?', [str(channel_id)])
            return self.c.fetchone()
        except Exception as e:
            logger.error(f"Ошибка получения канального поста: {e}")
            return None
    
    def clear_channel_post(self, channel_id: int) -> bool:
        """Очистка поста для канала"""
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


db = DBConnection()
