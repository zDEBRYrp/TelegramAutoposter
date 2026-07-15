#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Запуск Autoposter 4.0..."
echo "Директория: $SCRIPT_DIR"

# Ищем python3
if command -v python3 &> /dev/null; then
    PYTHON=python3
else
    echo "Ошибка: python3 не найден"
    exit 1
fi

echo "Python: $($PYTHON --version)"

# Создаём venv если нет
if [ ! -d "venv" ]; then
    echo "Создание виртуального окружения..."
    $PYTHON -m venv venv
    if [ $? -ne 0 ]; then
        echo "venv не создался. Устанавливаю python3-venv..."
        apt-get install -y python3-venv python3-pip 2>/dev/null || \
        yum install -y python3-venv python3-pip 2>/dev/null || true
        $PYTHON -m venv venv
        if [ $? -ne 0 ]; then
            echo "Не удалось создать venv. Запускаю без него..."
            $PYTHON -m pip install -r requirements.txt --user --quiet
            $PYTHON main.py
            exit $?
        fi
    fi
fi

# Активируем venv
echo "Активация venv..."
source venv/bin/activate

# pip обновляем и ставим зависимости
if [ ! -f "venv/requirements_installed.txt" ] || [ requirements.txt -nt venv/requirements_installed.txt ]; then
    echo "Установка зависимостей..."
    pip install --upgrade pip --quiet
    pip install -r requirements.txt
    if [ $? -eq 0 ]; then
        touch venv/requirements_installed.txt
    fi
fi

echo "Запуск бота..."
python main.py
