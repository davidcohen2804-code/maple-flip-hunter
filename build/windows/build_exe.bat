@echo off
REM Builds dist\MapleFlipHunter\MapleFlipHunter.exe on Windows 10/11.
REM Requires Python 3.11+ on PATH (https://www.python.org/downloads/windows/, tick "Add python.exe to PATH").
setlocal
cd /d "%~dp0..\.."
echo [1/4] creating virtual environment...
if not exist .venv ( python -m venv .venv || goto :fail )
call .venv\Scripts\activate.bat
echo [2/4] installing dependencies...
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt || goto :fail
echo [3/4] running unit tests...
python -m pytest tests\unit -q || goto :fail
echo [4/4] building executable...
python -m PyInstaller --noconfirm --clean build\windows\MapleFlipHunter.spec || goto :fail
echo.
echo Build complete: dist\MapleFlipHunter\MapleFlipHunter.exe
echo (zip the whole dist\MapleFlipHunter folder to distribute it)
exit /b 0
:fail
echo BUILD FAILED
exit /b 1
