@echo off
setlocal

echo [LOCAL] gameCrawling Docker crawl starting...
cd /d "%~dp0"

if exist "%~dp0.env.local" (
    copy /Y "%~dp0.env.local" "%~dp0.env" >nul
)

docker compose run --rm python python manage.py crawl %*
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo [ERROR] gameCrawling Docker crawl failed.
)

exit /b %EXIT_CODE%
