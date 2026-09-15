@echo off
chcp 65001 >nul
setlocal

set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "LINK=%STARTUP%\Dictation.lnk"
set "TASK=Dictation"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Unregister-ScheduledTask -TaskName '%TASK%' -Confirm:$false -ErrorAction SilentlyContinue"

if exist "%LINK%" del "%LINK%"

echo Транскрибатор убран из автозапуска Windows.
pause
