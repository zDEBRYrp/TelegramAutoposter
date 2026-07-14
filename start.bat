@echo off
echo Запуск Autoposter 4.0...

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Python не найден. Установите Python 3.8+
    pause
    exit /b 1
)

if not exist "venv" (
    echo Создание виртуального окружения...
    python -m venv venv
)

echo Активация виртуального окружения...
call venv\Scripts\activate.bat

if not exist "venv\requirements_installed.txt" (
    echo Установка зависимостей...
    pip install -q -r requirements.txt
    echo. > venv\requirements_installed.txt
) else (
    for /f "tokens=*" %%i in ('where /r . requirements.txt') do (
        set "req_file=%%i"
    )
    if exist "!req_file!" (
        for /f "tokens=*" %%j in ('where /r . venv\requirements_installed.txt') do (
            set "inst_file=%%j"
        )
        for /f "delims=" %%a in ('"powershell -Command "(Get-Item '!req_file!').LastWriteTime -gt (Get-Item '!inst_file!').LastWriteTime"') do set "update_req=%%a"
        if "!update_req!"=="True" (
            echo Обновление зависимостей...
            pip install -q -r requirements.txt
            echo. > venv\requirements_installed.txt
        )
    )
)

echo Запуск бота...
python main.py

pause
