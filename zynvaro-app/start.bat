@echo off
echo.
echo  =========================================
echo   ZYNVARO — Starting Backend API
echo   Guidewire DEVTrails 2026
echo  =========================================
echo.

cd /d "%~dp0backend"

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Please install Python 3.10+
    pause
    exit /b 1
)

:: Install deps if needed
if not exist ".deps_installed" (
    echo Installing dependencies...
    pip install -r requirements.txt
    echo. > .deps_installed
)

echo.
echo  Backend:  http://localhost:9001
echo  API Docs: http://localhost:9001/api/docs
echo  App:      Open frontend/app.html in browser
echo.
echo  Demo Login:  9876543210 / demo1234
echo.

python -m uvicorn main:app --host 0.0.0.0 --port 9001 --reload
