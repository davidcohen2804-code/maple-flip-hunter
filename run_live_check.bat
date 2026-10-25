@echo off
REM Full verification on THIS Windows PC: unit tests, Windows runtime checks (audio + notifications),
REM live source tests against cozy.markets / maplestory.watch, and a 120 s headless engine run.
REM Output: reports\VERIFICATION_REPORT.md (+ XML/JSON evidence). Send that folder back for review.
setlocal
cd /d "%~dp0"
if not exist .venv ( python -m venv .venv || goto :fail )
call .venv\Scripts\activate.bat
python -m pip install -q -r requirements.txt || goto :fail
if not exist reports mkdir reports
echo [1/4] unit tests
python -m pytest tests\unit -q --junitxml=reports\unit-results.xml
echo [2/4] Windows runtime checks (you should hear two sounds and see a notification)
python -m pytest tests\windows -q -rA --junitxml=reports\windows-results.xml
echo [3/4] live source tests (about 3 minutes)
set MFH_LIVE=1
set MFH_LIVE_SECONDS=120
python -m pytest tests\live -q -rA --junitxml=reports\live-results.xml
echo [4/4] headless engine run (120 s)
python -m maple_flip_hunter.headless --seconds 120 --out reports\exe_headless_result.json >nul
python tools\check_headless_result.py reports\exe_headless_result.json
python tools\ci_report.py reports > reports\VERIFICATION_REPORT.md
echo.
echo Done. Report: reports\VERIFICATION_REPORT.md
exit /b 0
:fail
echo FAILED
exit /b 1
