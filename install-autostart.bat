@echo off
chcp 65001 >nul
setlocal

set "BASE=%~dp0"
set "BASE=%BASE:~0,-1%"
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "LINK=%STARTUP%\Dictation.lnk"
set "VBS=%BASE%\autostart-launch.vbs"
set "TASK=Dictation"

if not exist "%VBS%" (
    echo Не найден autostart-launch.vbs
    pause
    exit /b 1
)

if not exist "%BASE%\.venv\Scripts\pythonw.exe" (
    echo Не найден .venv\Scripts\pythonw.exe
    echo Сначала запустите start.bat для установки зависимостей.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Unregister-ScheduledTask -TaskName '%TASK%' -Confirm:$false -ErrorAction SilentlyContinue;" ^
  "$action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument '\"\"%VBS%\"\"';" ^
  "$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME;" ^
  "$trigger.Delay = 'PT20S';" ^
  "$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries;" ^
  "Register-ScheduledTask -TaskName '%TASK%' -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null"
if errorlevel 1 (
    echo.
    echo Предупреждение: не удалось создать задачу планировщика.
    echo Будет использован ярлык в папке автозагрузки.
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
      "$s = (New-Object -ComObject WScript.Shell).CreateShortcut('%LINK%');" ^
      "$s.TargetPath = 'wscript.exe';" ^
      "$s.Arguments = '\"\"%VBS%\"\"';" ^
      "$s.WorkingDirectory = '%BASE%';" ^
      "$s.Description = 'Голосовая диктовка';" ^
      "$s.Save()"
) else (
    if exist "%LINK%" del "%LINK%"
    echo.
    echo Создана задача планировщика: %TASK%
    echo Запуск через 20 секунд после входа в Windows.
)

echo.
echo Готово: транскрибатор добавлен в автозапуск Windows.
if exist "%LINK%" echo Ярлык: %LINK%
echo Лог автозапуска: %BASE%\autostart.log
echo.
echo Убрать из автозапуска: remove-autostart.bat
echo.
pause
