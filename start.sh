#!/bin/bash

# Скрипт запуска Autoposter 4.0

echo "🚀 Запуск Autoposter 4.0..."

# Проверка Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python3 не найден. Установите Python 3.8+"
    exit 1
fi

# Создание venv если его нет
if [ ! -d "venv" ]; then
    echo "📁 Создание виртуального окружения..."
    python3 -m venv venv
fi

# Активация venv
echo "⚡ Активация виртуального окружения..."
source venv/bin/activate

# Установка зависимостей если их нет
if [ ! -f "venv/requirements_installed.txt" ] || [ requirements.txt -nt venv/requirements_installed.txt ]; then
    echo "📦 Установка зависимостей..."
    pip install --quiet -r requirements.txt
    touch venv/requirements_installed.txt
fi

# Запуск бота
echo "🤖 Запуск бота..."
python main.py
