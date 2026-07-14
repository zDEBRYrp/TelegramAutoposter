# Autoposter 4.0

Telegram-бот для автоматической рассылки постов в супергруппы.

## Возможности

- Автоматическая рассылка постов с фото/видео
- Поддержка Markdown форматирования
- Индивидуальные посты для каждого канала
- Настройка интервала рассылки
- Управление через Telegram бота

## Установка

### Требования

- Python 3.11+
- Telegram API credentials (API_ID и API_HASH)

### Шаги установки

1. Клонировать репозиторий:
```bash
git clone https://github.com/yourusername/autoposter4.git
cd autoposter4
```

2. Создать виртуальное окружение:
```bash
python -m venv venv
source venv/bin/activate  # Linux/macOS
# или
venv\Scripts\activate  # Windows
```

3. Установить зависимости:
```bash
pip install -r requirements.txt
```

4. Настроить `.env` файл:
```bash
cp .env.example .env
# Отредактировать .env файл с вашими данными
```

5. Получить API credentials на [my.telegram.org](https://my.telegram.org/auth)

6. Запустить бота:
```bash
python main.py
```

## Конфигурация

В файле `.env` указать:

```env
TOKEN=your_bot_token_here
ADMINS=8295697775
DIR=
API_ID=your_api_id_here
API_HASH=your_api_hash_here
```

## Использование

После запуска бота отправить команду `/start` в личные сообщения.

## Безопасность

- `.env` файл игнорируется git (см. `.gitignore`)
- Не публиковать токен и API credentials
- Использовать отдельный API для продакшена
