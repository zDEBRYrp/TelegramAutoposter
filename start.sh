#!/bin/bash

# Переходим в директорию скрипта
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Запуск Autoposter 4.0..."

# Определяем python
if command -v python3 &> /dev/null; then
    PYTHON=python3
elif command -v python &> /dev/null; then
    PYTHON=python
else
    echo "Python не найден. Установите Python 3.8+"
    exit 1
fi

# Создаём venv если нет
if [ ! -d "venv" ]; then
    echo "Создание виртуального окружения..."
    $PYTHON -m venv venv
fi

# Активируем venv
source venv/bin/activate

# Устанавливаем зависимости если нужно
if [ ! -f "venv/requirements_installed.txt" ] || [ requirements.txt -nt venv/requirements_installed.txt ]; then
    echo "Установка зависимостей..."
    pip install --upgrade pip --quiet
    pip install -r requirements.txt
    touch venv/requirements_installed.txt
fi

echo "Запуск бота..."
python main.py
