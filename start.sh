#!/bin/bash
echo "Запуск Autoposter 4.0..."

if ! command -v python3 &> /dev/null; then
    echo "Python3 не найден. Установите Python 3.8+"
    exit 1
fi

if [ ! -d "venv" ]; then
    echo "Создание виртуального окружения..."
    python3 -m venv venv
fi

echo "Активация виртуального окружения..."
source venv/bin/activate

if [ ! -f "venv/requirements_installed.txt" ] || [ requirements.txt -nt venv/requirements_installed.txt ]; then
    echo "Установка зависимостей..."
    pip install --quiet -r requirements.txt
    touch venv/requirements_installed.txt
fi

echo "Запуск бота..."
python main.py
