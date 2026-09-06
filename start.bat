@echo off
setlocal enabledelayedexpansion
echo Zapusk Autoposter 4.0...

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Python ne nayden. Ustanovite Python 3.11+
    pause
    exit /b 1
)

if not exist "venv\Scripts\activate.bat" (
    echo Sozdanie virtualnogo okruzheniya...
    rmdir /s /q venv 2>nul
    python -m venv venv
    if errorlevel 1 (
        echo OSHIBKA: ne udalos sozdat venv
        pause
        exit /b 1
    )
)

echo Aktivaciya virtualnogo okruzheniya...
call venv\Scripts\activate.bat

set "NEED_INSTALL=0"
if not exist "venv\requirements_installed.txt" (
    set "NEED_INSTALL=1"
) else (
    for /f "delims=" %%a in ('powershell -NoProfile -Command "if ((Get-Item 'requirements.txt').LastWriteTime -gt (Get-Item 'venv\requirements_installed.txt').LastWriteTime) { '1' } else { '0' }"') do set "NEED_INSTALL=%%a"
)

if "!NEED_INSTALL!"=="1" (
    echo Ustanovka zavisimostey...
    python -m pip install --upgrade pip
    pip install -r requirements.txt
    if errorlevel 1 (
        echo OSHIBKA ustanovki zavisimostey
        pause
        exit /b 1
    )
    copy /y nul "venv\requirements_installed.txt" >nul
)

echo Zapusk bota...
python main.py

pause
