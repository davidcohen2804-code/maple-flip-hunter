@echo off
rem Builds the Friends Edition (one EXE). Usage: build_friends.bat https://license.your.host <PUBLIC_KEY_HEX>
cd /d "%~dp0\..\.."
if "%~2"=="" ( echo usage: build_friends.bat ^<license server URL^> ^<public key hex^> & exit /b 1 )
echo friends> maple_flip_hunter\_edition.txt
> maple_flip_hunter\friends_config.json echo {"server": "%~1", "public_key": "%~2"}
python -m PyInstaller --noconfirm --clean build\windows\MapleFlipHunterFriends.spec
echo developer> maple_flip_hunter\_edition.txt
del maple_flip_hunter\friends_config.json
echo.
echo Friends Edition: dist\MapleFlipHunterFriends.exe
