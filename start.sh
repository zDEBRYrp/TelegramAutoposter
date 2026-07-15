#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Запуск Autoposter 4.0..."

# Создаём venv если нет
if [ ! -d "venv" ]; then
    echo "Создание виртуального окружения..."
    python3 -m venv venv 2>&1
    # Если папка так и не создалась - устанавливаем python3-venv
    if [ ! -d "venv" ]; then
        echo "Устанавливаю python3-venv..."
        apt-get install -y python3-venv python3-pip 2>&1
        python3 -m venv venv 2>&1
    fi
fi

# Финальная проверка
if [ ! -f "venv/bin/activate" ]; then
    echo "Не удалось создать venv. Запускаю без него..."
    python3 -m pip install -r requirements.txt --break-system-packages -q
    python3 main.py
    exit $?
fi

echo "Активация venv..."
source venv/bin/activate

echo "Установка зависимостей..."
pip install --upgrade pip -q
pip install -r requirements.txt -q
if [ $? -eq 0 ]; then
    touch venv/requirements_installed.txt
fi

echo "Запуск бота..."
python3 main.py
