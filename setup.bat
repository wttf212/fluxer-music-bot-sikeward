@echo off
REM Fluxer Music Bot - Windows Setup Script
REM This script installs all dependencies automatically.

echo ============================================
echo   Fluxer Music Bot - Setup (Windows)
echo ============================================
echo.

REM 1. Python dependencies
echo [1/3] Installing Python dependencies...
pip install -r requirements.txt
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: pip install failed. Make sure Python 3.10+ is installed.
    pause
    exit /b 1
)
echo       Done.
echo.

REM 2. Deno (JS runtime for yt-dlp signature solving)
echo [2/3] Installing Deno (JS runtime)...
where deno >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo       Deno already installed.
) else (
    powershell -Command "irm https://deno.land/install.ps1 | iex"
    if %ERRORLEVEL% NEQ 0 (
        echo ERROR: Deno installation failed.
        pause
        exit /b 1
    )
    echo       Done.
)
echo.

REM 3. bgutil-pot (YouTube PO token generator)
echo [3/3] Downloading bgutil-pot (PO token generator)...
if exist bgutil-pot.exe (
    echo       bgutil-pot.exe already exists, skipping download.
) else (
    powershell -Command "Invoke-WebRequest -Uri 'https://github.com/jim60105/bgutil-ytdlp-pot-provider-rs/releases/latest/download/bgutil-pot-windows-x86_64.exe' -OutFile 'bgutil-pot.exe'"
    if %ERRORLEVEL% NEQ 0 (
        echo ERROR: Failed to download bgutil-pot.
        echo        Download manually from: https://github.com/jim60105/bgutil-ytdlp-pot-provider-rs/releases
        pause
        exit /b 1
    )
    echo       Done.
)
echo.

REM 4. Config check
if not exist config.yaml (
    echo [!] config.yaml not found. Creating from template...
    copy config.example.yaml config.yaml >nul 2>&1
    if exist config.yaml (
        echo     Created config.yaml - edit it with your bot token before running!
    ) else (
        echo     WARNING: Could not create config.yaml. Copy config.example.yaml manually.
    )
)
echo.

echo ============================================
echo   Setup complete!
echo.
echo   Next steps:
echo   1. Edit config.yaml with your bot token
echo   2. Run: python main.py
echo ============================================
pause
