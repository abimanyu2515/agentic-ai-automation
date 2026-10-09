@echo off
REM Start Backend Server with Professional Report Engine Enabled

echo ========================================
echo Starting Backend with Professional Report Engine
echo ========================================
echo.

REM Set environment variable for professional report engine
set REPORT_ENGINE=professional

echo Environment Variables:
echo   REPORT_ENGINE=%REPORT_ENGINE%
echo.

REM Start the backend server
echo Starting backend server...
cd backend
python main.py

pause
