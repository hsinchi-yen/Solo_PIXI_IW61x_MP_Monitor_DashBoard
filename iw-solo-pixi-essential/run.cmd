@echo off
setlocal

echo Starting Docker Compose...
docker compose up -d --build

if %ERRORLEVEL% neq 0 (
    echo [ERROR] Failed to start Docker Compose.
    exit /b %ERRORLEVEL%
)

echo Waiting for API to become healthy...
set max_retries=30
set retry_count=0

:health_check
set /a retry_count+=1
curl -s -f http://localhost:8004/health >nul 2>&1

if %ERRORLEVEL% equ 0 (
    echo [SUCCESS] API is healthy!
    echo Opening browser...
    start http://localhost:8004
    exit /b 0
)

if %retry_count% geq %max_retries% (
    echo [ERROR] Timed out waiting for API health check.
    exit /b 1
)

echo Waiting... (%retry_count%/%max_retries%)
timeout /t 2 /nobreak >nul
goto health_check
