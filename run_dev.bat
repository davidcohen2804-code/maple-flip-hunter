@echo off
REM Run from source on Windows (no EXE needed): creates .venv, installs deps, starts the app.
setlocal
cd /d "%~dp0"
if not exist .venv ( python -m venv .venv || exit /b 1 )
call .venv\Scripts\activate.bat
python -m pip install -q -r requirements.txt || exit /b 1
python -m maple_flip_hunter %*
