#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Запуск Autoposter 4.0..."

# Устанавливаем pip и venv если нет
if ! python3 -m pip --version &> /dev/null; then
    echo "Устанавливаю pip и python3-venv..."
    apt-get update -q
    apt-get install -y python3-pip python3-venv
fi

# Создаём venv если нет
if [ ! -d "venv" ]; then
    echo "Создание виртуального окружения..."
    python3 -m venv venv
fi

# Активируем
source venv/bin/activate

# Устанавливаем зависимости
if [ ! -f "venv/requirements_installed.txt" ] || [ requirements.txt -nt venv/requirements_installed.txt ]; then
    echo "Установка зависимостей..."
    pip install --upgrade pip -q
    pip install -r requirements.txt
    touch venv/requirements_installed.txt
fi

echo "Запуск бота..."
python3 main.py
