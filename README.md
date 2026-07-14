# TelegramAutoposter 1.1.0

Telegram-бот для автоматической рассылки постов в супергруппы.

## 🚀 Возможности

- Автоматическая рассылка постов с фото/видео
- Поддержка Markdown форматирования
- Индивидуальные посты для каждого канала
- Настройка интервала рассылки
- Управление через Telegram бота
- Автоматическое обновление с GitHub

## 📋 Требования

- Python 3.11+
- Telegram API credentials (API_ID и API_HASH)

## 🚀 Установка

### Шаг 1: Клонировать репозиторий

```bash
git clone https://github.com/zdebryrp/TelegramAutoposter.git
cd TelegramAutoposter
```

### Шаг 2: Создать виртуальное окружение

```bash
python -m venv venv
source venv/bin/activate  # Linux/macOS
# или
venv\Scripts\activate  # Windows
```

### Шаг 3: Установить зависимости

```bash
pip install -r requirements.txt
```

### Шаг 4: Настроить `.env` файл

```bash
cp .env.example .env
# Отредактировать .env файл с вашими данными
```

### Шаг 5: Получить API credentials

1. Зайти на [my.telegram.org](https://my.telegram.org/auth)
2. Войти с помощью телефона
3. Перейти в **API tools**
4. Создать новое приложение
5. Скопировать `API_ID` и `API_HASH`

### Шаг 6: Запустить бота

```bash
python main.py
```

## ⚙️ Конфигурация

В файле `.env` указать:

```env
TOKEN=ваш_токен_бота_здесь
ADMINS=8295697775
DIR=
API_ID=ваш_api_id_здесь
API_HASH=ваш_api_hash_здесь
```

### Переменные

| Переменная | Описание |
|------------|----------|
| TOKEN | Токен бота (получить у @BotFather) |
| ADMINS | ID администраторов (через запятую) |
| DIR | Путь к файлам медиа |
| API_ID | ID приложения с my.telegram.org |
| API_HASH | Hash приложения с my.telegram.org |

## 📱 Использование

1. Открыть бота в Telegram
2. Отправить команду `/start`
3. Использовать клавиатуру для управления

## 📄 Лицензия

GNU GPL v3 - смотрите файл [LICENSE](LICENSE)

**Правила использования:**
- ✅ Можно копировать и распространять
- ✅ Можно изменять и создавать производные работы
- ❌ Нельзя продавать или использовать в коммерческих целях
- ✅ Исходный код должен быть открыт

## 🔧 Обновление

Бот имеет встроенную систему обновлений. Команда `/update` или кнопка "Обновление" проверит наличие новых версий.

---

TelegramAutoposter 1.1.0 - Automatic posting bot for Telegram channels.