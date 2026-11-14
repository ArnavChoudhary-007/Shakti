@echo off
title Shakti (development checkout)
cd /d "%~dp0"

:: Development launcher for a git checkout. End users should use the
:: one-line installer instead (see README) - it needs no Python or Node.

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in your PATH.
    echo Install Python 3.10-3.12 from https://www.python.org/downloads/
    pause
    exit /b 1
)

if not exist ".venv" (
    echo [INFO] First run: creating a virtual environment and installing dependencies...
    python -m venv .venv || goto :fail
    call .venv\Scripts\activate.bat
    python -m pip install --upgrade pip
    pip install -r rag_pipeline\requirements.txt || goto :fail
) else (
    call .venv\Scripts\activate.bat
)

:: Build the web UI if it hasn't been built and Node.js is available.
if not exist "rag_pipeline\webui\index.html" (
    where npm >nul 2>&1
    if not errorlevel 1 (
        echo [INFO] Building the web interface...
        pushd web
        call npm ci || (popd & goto :fail)
        call npm run build || (popd & goto :fail)
        popd
    ) else (
        echo [WARN] Node.js not found - the web interface can't be built.
        echo        Install Node.js 20+ from https://nodejs.org and run this again.
    )
)

echo.
echo Starting Shakti at http://127.0.0.1:8000 - keep this window open.
python -m rag_pipeline
goto :eof

:fail
echo [ERROR] Setup failed - see the messages above.
pause
exit /b 1
