@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================================
echo   Build QA_Layout_Jurnal.exe (Aplikasi QA PDF Mandiri)
echo ============================================================

where python >nul 2>nul
if errorlevel 1 (
    echo [GALAT] Python 3 tidak ditemukan di PATH.
    echo Unduh Python 3.10+ dari https://www.python.org/downloads/ dan centang "Add Python to PATH".
    pause
    exit /b 1
)

if not exist ".venv_build" (
    echo [1/4] Membuat virtual environment .venv_build...
    python -m venv .venv_build
    if errorlevel 1 (
        echo [GALAT] Gagal membuat .venv_build.
        pause
        exit /b 1
    )
)

call ".venv_build\Scripts\activate.bat"
if errorlevel 1 (
    echo [GALAT] Gagal mengaktifkan .venv_build.
    pause
    exit /b 1
)

echo [2/4] Menginstal dependensi dan PyInstaller...
python -m pip install --upgrade pip
pip install -r requirements.txt pyinstaller
if errorlevel 1 (
    echo [GALAT] Gagal menginstal dependensi Python.
    pause
    exit /b 1
)

echo [3/4] Membangun QA_Layout_Jurnal.exe...
pyinstaller --clean --noconfirm qa_jurnal.spec
if errorlevel 1 (
    echo [GALAT] PyInstaller gagal membangun file .exe.
    pause
    exit /b 1
)

echo [4/4] Memeriksa hasil build...
if exist "dist\QA_Layout_Jurnal.exe" (
    echo.
    echo [SUKSES] File portabel siap didistribusikan:
    echo   %CD%\dist\QA_Layout_Jurnal.exe
    echo.
) else (
    echo [GALAT] File dist\QA_Layout_Jurnal.exe tidak ditemukan.
    pause
    exit /b 1
)

pause
