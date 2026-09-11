@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
echo Запуск Autoposter...

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Python не найден. Установите Python 3.11+
    pause
    exit /b 1
)

if not exist "venv\Scripts\activate.bat" (
    echo Создание виртуального окружения...
    rmdir /s /q venv 2>nul
    python -m venv venv
    if errorlevel 1 (
        echo ОШИБКА: не удалось создать venv
        pause
        exit /b 1
    )
)

echo Активация виртуального окружения...
call venv\Scripts\activate.bat

set "NEED_INSTALL=0"
if not exist "venv\requirements_installed.txt" (
    set "NEED_INSTALL=1"
) else (
    for /f "delims=" %%a in ('powershell -NoProfile -Command "if ((Get-Item 'requirements.txt').LastWriteTime -gt (Get-Item 'venv\requirements_installed.txt').LastWriteTime) { '1' } else { '0' }"') do set "NEED_INSTALL=%%a"
)

if "!NEED_INSTALL!"=="1" (
    echo Установка зависимостей...
    python -m pip install --upgrade pip
    pip install -r requirements.txt
    if errorlevel 1 (
        echo ОШИБКА установки зависимостей
        pause
        exit /b 1
    )
    copy /y nul "venv\requirements_installed.txt" >nul
)

echo Запуск бота...
python main.py

pause
