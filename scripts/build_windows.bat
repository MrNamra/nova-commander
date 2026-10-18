@echo off
REM ===========================================================================
REM Windows Batch Build Script for WinSCP Cross-Platform
REM ===========================================================================
setlocal enabledelayedexpansion

echo ===================================================
echo Building WinSCP for Windows
echo ===================================================

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH!
    pause
    exit /b 1
)

echo [1/3] Installing/verifying dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller

echo [2/3] Compiling standalone executable...
python scripts\build_windows.py

echo [3/3] Checking for Inno Setup compiler (ISCC.exe)...
set ISCC_PATH="C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if exist %ISCC_PATH% (
    echo Compiling Windows installer with Inno Setup...
    %ISCC_PATH% scripts\windows_installer.iss
    echo [SUCCESS] Windows Installer created in dist/
) else (
    echo [INFO] Inno Setup not found at default location.
    echo Standalone executable is ready at dist\WinSCP.exe
)

echo ===================================================
echo Build completed!
echo ===================================================
pause
