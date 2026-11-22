@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found. Please run install.bat first.
    pause
    exit /b 1
)

call ".venv\Scripts\activate.bat"

echo Starting AutoCut server on http://127.0.0.1:8000 ...
start "" cmd /c "timeout /t 2 /nobreak >nul && start http://127.0.0.1:8000"

python launcher.py
if errorlevel 1 (
    echo.
    echo [ERROR] AutoCut server stopped unexpectedly. Check logs\app.log for details.
    pause
)
