@echo off
set LEVEL=%1
if "%LEVEL%"=="" set LEVEL=patch

echo Bumping version (%LEVEL%)...
python bump_version.py %LEVEL%
if %errorlevel% neq 0 (
    echo Error bumping version
    pause
    exit /b 1
)

for /f "tokens=*" %%v in (version.txt) do set VERSION=%%v
echo New version: %VERSION%

git add .
git commit -m "v%VERSION%"
git push

echo Done!
pause
