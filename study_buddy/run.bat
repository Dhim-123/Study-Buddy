@echo off
echo.
echo  ============================
echo   Study Buddy - Starting Up!
echo  ============================
echo.

:: Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  ERROR: Python is not installed!
    echo  Download it from: https://www.python.org/downloads/
    echo  Make sure to check "Add Python to PATH" during install.
    echo.
    pause
    exit /b
)

:: Install dependencies (only runs if needed)
echo  Installing dependencies...
pip install -r requirements.txt -q
echo  Done!
echo.

:: Start the server
echo  Starting Study Buddy server...
echo  If port 5000 is occupied, a different free port will be used.
echo  Your browser will open automatically to the correct URL.
echo  Or open: http://127.0.0.1:5000 (or the port shown below)
echo.
echo  Press Ctrl+C to stop the server.
echo.
python app.py
pause
