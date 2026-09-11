@echo off
chcp 65001 >nul
title Autoposter
cd /d "%~dp0"
setlocal enabledelayedexpansion
echo ========================================
echo   Запуск Autoposter...
echo ========================================

python --version
if %errorlevel% neq 0 (
    echo [ОШИБКА] Python не найден. Установите Python 3.11+
    pause
    exit /b 1
)

if not exist "venv\Scripts\activate.bat" (
    echo Создание виртуального окружения...
    rmdir /s /q venv 2>nul
    python -m venv venv
    if errorlevel 1 (
        echo [ОШИБКА] Не удалось создать venv
        pause
        exit /b 1
    )
)

if not exist "venv\Scripts\activate.bat" (
    echo [ОШИБКА] venv сломан: нет venv\Scripts\activate.bat
    echo Удалите папку venv и запустите снова.
    pause
    exit /b 1
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
        echo [ОШИБКА] Установка зависимостей не удалась
        pause
        exit /b 1
    )
    copy /y nul "venv\requirements_installed.txt" >nul
)

echo ----------------------------------------
echo Запуск бота...
echo ----------------------------------------
python main.py
echo ----------------------------------------
echo Бот остановлен.
pause
