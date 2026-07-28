@echo off
title RAG Pipeline Launcher
color 0A

echo ===================================================
echo     Local-First RAG Pipeline - 1-Click Launcher
echo ===================================================
echo.

:: 1. Check for Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in your PATH. 
    echo Please install Python 3.11+ from https://www.python.org/downloads/
    pause
    exit /b
)

:: 2. Check for Ollama
ollama --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Ollama is not installed or not in your PATH. 
    echo Please install Ollama from https://ollama.ai/
    pause
    exit /b
)

:: 3. Setup Virtual Environment
if not exist ".venv" (
    echo [INFO] First time setup: Creating virtual environment...
    python -m venv .venv
    
    echo [INFO] Installing dependencies (this may take a few minutes)...
    call .venv\Scripts\activate.bat
    
    :: Install PyTorch CPU first for a smaller download footprint
    pip install torch --index-url https://download.pytorch.org/whl/cpu
    
    :: Install the rest of the requirements
    pip install -r requirements.txt
    
    echo [INFO] Pulling necessary AI models from Ollama (this will take a while)...
    ollama pull llama3.2
    ollama pull llava
    
    echo [INFO] Setup Complete!
) else (
    echo [INFO] Virtual environment found. Activating...
    call .venv\Scripts\activate.bat
)

:: 4. Start the Application
echo.
echo ===================================================
echo   Starting the Server... DO NOT CLOSE THIS WINDOW
echo ===================================================
echo.

:: Wait 3 seconds, then open the browser automatically
start "" "http://localhost:8000"

:: Launch the FastAPI server
uvicorn rag_pipeline.api.main:app --port 8000
