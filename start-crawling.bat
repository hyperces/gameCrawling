@echo off

:: Install Python packages
pip install -q -r "%~dp0requirements.txt"
if %errorlevel% neq 0 (
    echo [ERROR] pip install failed. Please check Python is installed.
    pause
    exit /b 1
)

:: Load .env file when present
if exist "%~dp0.env" (
    for /f "usebackq eol=# tokens=1,2 delims==" %%a in ("%~dp0.env") do set "%%a=%%b"
) else (
    echo [INFO] .env not found. Using default DB settings from src\config.py
)

:: Run crawling
cd "%~dp0src"
python batman_crawling.py

pause
