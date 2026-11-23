@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ==================================================
echo   AutoCut - Local Offline Setup (Windows CPU)
echo ==================================================
echo.

REM 1. Check Python 3.11+
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in PATH.
    echo Please install Python 3.11+ from https://www.python.org/downloads/ and check "Add Python to PATH".
    pause
    exit /b 1
)

for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PY_VER=%%v
echo [OK] Found Python %PY_VER%

REM 2. Create virtual environment
if not exist ".venv" (
    echo [1/4] Creating Python virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
) else (
    echo [1/4] Virtual environment already exists.
)

call ".venv\Scripts\activate.bat"

REM 3. Install Python dependencies (local external-transcript workflow)
echo [2/4] Installing Python packages (FastAPI, rapidfuzz, OpenCV, Pillow)...
python -m pip install --upgrade pip
pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Failed to install Python requirements. Check your internet connection during first install.
    pause
    exit /b 1
)

REM 4. Check FFmpeg
echo [3/4] Checking FFmpeg...
where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo [WARNING] FFmpeg was not found in PATH.
    where winget >nul 2>&1
    if not errorlevel 1 (
        echo Attempting to install FFmpeg via winget...
        winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
    ) else (
        echo Please install FFmpeg manually: winget install Gyan.FFmpeg
    )
) else (
    for /f "tokens=1-3" %%a in ('ffmpeg -version ^| findstr /b "ffmpeg version"') do echo [OK] %%a %%b %%c
)

REM 5. Download offline Arabic & Monospace fonts into web/fonts/
echo [4/4] Downloading offline Arabic UI fonts (IBM Plex Sans Arabic)...
if not exist "web\fonts" mkdir "web\fonts"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ProgressPreference = 'SilentlyContinue';" ^
  "$fonts = @{" ^
  "  'IBMPlexSansArabic-Regular.woff2' = 'https://cdn.jsdelivr.net/gh/IBM/plex@master/IBM-Plex-Sans-Arabic/fonts/complete/woff2/IBMPlexSansArabic-Regular.woff2';" ^
  "  'IBMPlexSansArabic-Medium.woff2' = 'https://cdn.jsdelivr.net/gh/IBM/plex@master/IBM-Plex-Sans-Arabic/fonts/complete/woff2/IBMPlexSansArabic-Medium.woff2';" ^
  "  'IBMPlexSansArabic-SemiBold.woff2' = 'https://cdn.jsdelivr.net/gh/IBM/plex@master/IBM-Plex-Sans-Arabic/fonts/complete/woff2/IBMPlexSansArabic-SemiBold.woff2';" ^
  "  'IBMPlexSansArabic-Bold.woff2' = 'https://cdn.jsdelivr.net/gh/IBM/plex@master/IBM-Plex-Sans-Arabic/fonts/complete/woff2/IBMPlexSansArabic-Bold.woff2'" ^
  "};" ^
  "foreach ($k in $fonts.Keys) {" ^
  "  $dest = Join-Path 'web\fonts' $k;" ^
  "  if (-not (Test-Path $dest)) {" ^
  "    try { Invoke-WebRequest -Uri $fonts[$k] -OutFile $dest -TimeoutSec 20; Write-Host ('  [OK] ' + $k) }" ^
  "    catch { Write-Host ('  [WARN] Could not download ' + $k + ' - will fallback to Segoe UI / Tahoma') }" ^
  "  }" ^
  "}"

echo.
echo ==================================================
echo   Installation complete! Double-click run.bat to start AutoCut.
echo ==================================================
pause
