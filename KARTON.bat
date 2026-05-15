@echo off
setlocal
set PROJECT_DIR=%~dp0
title KARTON

:: ─── CEK PYTHON ─────────────────────────────────────────────────
python --version >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python tidak ditemukan di sistem ini.
    echo.
    echo Silakan download dan install Python 3.10 dari:
    echo   https://www.python.org/downloads/release/python-31011/
    echo.
    echo PENTING: Saat install, centang "Add Python to PATH"
    pause
    exit /b 1
)

:: ─── CEK APAKAH SEMUA LIBRARY SUDAH TERINSTALL ──────────────────
echo Cek installasi...
python -c "import flask, flask_socketio, cv2, easyocr, torch, torchvision, pandas, openpyxl, xlsxwriter, PIL, numpy, pygrabber, psycopg2, sqlalchemy; assert torch.__version__.startswith('2.5')" >nul 2>&1
IF %ERRORLEVEL% EQU 0 (
    goto :jalankan_migrate
)

:: ─── JIKA BELUM TERINSTALL: TAMPILKAN PROSES INSTALASI ──────────
title KARTON - Setup dan Installer

echo ============================================================
echo   KARTON - Persiapan Instalasi
echo ============================================================
echo.

for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PY_VER=%%v
echo [INFO] Python terdeteksi: %PY_VER%
echo.

:: ─── [1/6] UPGRADE PIP ──────────────────────────────────────────
echo [1/6] Memperbarui pip ke versi terbaru...
python -m pip install --upgrade pip
IF %ERRORLEVEL% NEQ 0 (
    echo [PERINGATAN] Gagal update pip, melanjutkan dengan versi saat ini...
)
echo.

:: ─── [2/6] INSTALL PYTORCH ──────────────────────────────────────
echo [2/6] Memeriksa PyTorch...
python -c "import torch; assert torch.__version__.startswith('2.5'), 'versi salah'" >nul 2>&1
IF %ERRORLEVEL% EQU 0 (
    echo       PyTorch 2.5.x sudah terinstall, melewati langkah ini.
) ELSE (
    echo       Menginstall PyTorch ^(torch + torchvision^)...
    echo       File cukup besar, pastikan koneksi internet stabil.
    echo.
    python -m pip uninstall torch torchvision -y >nul 2>&1
    python -m pip install torch==2.5.1 torchvision==0.20.1 --index-url https://download.pytorch.org/whl/cpu
    IF %ERRORLEVEL% NEQ 0 (
        echo.
        echo [ERROR] Gagal menginstall PyTorch.
        echo         Kemungkinan penyebab: koneksi internet lambat atau terputus.
        echo         Coba jalankan ulang KARTON.bat setelah koneksi stabil.
        echo         Atau jalankan manual di Command Prompt:
        echo           pip install torch==2.5.1 torchvision==0.20.1 --index-url https://download.pytorch.org/whl/cpu
        pause
        exit /b 1
    )
    echo       PyTorch berhasil diinstall.
)
echo.

:: ─── [3/6] INSTALL PILLOW ───────────────────────────────────────
echo [3/6] Menginstall Pillow...
python -m pip install "Pillow>=10.0.0"
IF %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Gagal menginstall Pillow.
    pause
    exit /b 1
)
echo       Pillow berhasil diinstall.
echo.

:: ─── [4/6] INSTALL SEMUA LIBRARY (tanpa opencv dan easyocr) ─────
echo [4/6] Menginstall semua library pendukung...
echo       Harap tunggu, proses ini membutuhkan waktu...
echo.
python -m pip install -r "%PROJECT_DIR%requirements.txt"
IF %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Gagal menginstall satu atau lebih library.
    echo         Perhatikan pesan error di atas untuk tahu library mana yang bermasalah.
    echo         Coba jalankan ulang KARTON.bat setelah koneksi internet stabil.
    pause
    exit /b 1
)
echo.
echo       Library pendukung berhasil diinstall.
echo.

:: ─── [5/6] INSTALL EASYOCR (--no-deps) lalu BERSIHKAN OPENCV ────
echo [5/6] Menginstall EasyOCR dan menyelesaikan konflik OpenCV...
echo.

:: Install easyocr tanpa menarik opencv-python-headless otomatis
python -m pip install easyocr==1.7.2 --no-deps
IF %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Gagal menginstall EasyOCR.
    pause
    exit /b 1
)

:: Hapus opencv-python-headless jika sempat terinstall
python -m pip uninstall opencv-python-headless -y >nul 2>&1
echo       [OK] opencv-python-headless dibersihkan jika ada.

:: Install opencv-python (versi penuh, mendukung kamera dan GUI)
python -m pip install opencv-python==4.10.0.84
IF %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Gagal menginstall OpenCV.
    pause
    exit /b 1
)
echo       [OK] opencv-python 4.10.0.84 berhasil diinstall.
echo.

:: Verifikasi tidak ada konflik
python -c "import cv2; print('       OpenCV OK - versi:', cv2.__version__)"
IF %ERRORLEVEL% NEQ 0 (
    echo [ERROR] OpenCV gagal dimuat setelah instalasi.
    echo         Coba jalankan manual: pip uninstall opencv-python-headless -y
    echo         Lalu: pip install opencv-python==4.10.0.84
    pause
    exit /b 1
)

python -c "import easyocr; print('       EasyOCR OK - versi:', easyocr.__version__)"
IF %ERRORLEVEL% NEQ 0 (
    echo [ERROR] EasyOCR gagal diverifikasi.
    pause
    exit /b 1
)
echo.

echo ============================================================
echo   Instalasi selesai! Menjalankan migrasi database...
echo ============================================================
echo.

:: ─── [6/6] JALANKAN MIGRATE.PY ──────────────────────────────────
:jalankan_migrate
title KARTON - Migrasi Database
cd /d "%PROJECT_DIR%"
echo.
python migrate.py
IF %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Migrasi database gagal.
    echo         Pastikan:
    echo           - PostgreSQL sudah berjalan
    echo           - Database sudah dibuat
    echo           - Konfigurasi di config.py sudah benar
    echo.
    echo         Perbaiki masalah di atas, lalu jalankan ulang KARTON.bat
    pause
    goto :eof
)
echo.
echo [INFO] Migrasi selesai. Menjalankan aplikasi KARTON...
echo.
timeout /t 2 >nul

:: ─── JALANKAN APLIKASI ──────────────────────────────────────────
:jalankan_app
title KARTON
cls
cd /d "%PROJECT_DIR%"
start /b cmd /c "timeout /t 3 >nul && start http://localhost:5000"
python app.py

pause