@echo off
rem CS80 panel launcher: start host.py (if not already running) + open the editor
rem in a chromeless Edge/Chrome app window. Keep that window open (minimize is fine):
rem it is the pixel source. Close it and the screen falls back to the waiting page.
rem
rem   run-windows.cmd                      start
rem   set PORT=9000 ^& run-windows.cmd      custom port
rem
rem NOTE: keep this file ASCII-only. cmd.exe reads it in the OEM codepage and
rem chokes on UTF-8 Chinese in comments.
setlocal enabledelayedexpansion
cd /d "%~dp0"
if "%PORT%"=="" set PORT=8787
set URL=http://127.0.0.1:%PORT%/

powershell -NoProfile -Command "try{(New-Object Net.Sockets.TcpClient('127.0.0.1',%PORT%)).Close();exit 0}catch{exit 1}"
if not errorlevel 1 goto ready

set PY=%~dp0..\win-flash\flash\py\python.exe
if not exist "!PY!" set PY=python
echo [cs80] starting host: "!PY!" host.py --port %PORT%
start "CS80 host" /min "!PY!" "%~dp0host.py" --port %PORT%
set /a tries=0

:wait
powershell -NoProfile -Command "try{(New-Object Net.Sockets.TcpClient('127.0.0.1',%PORT%)).Close();exit 0}catch{exit 1}"
if not errorlevel 1 goto ready
set /a tries+=1
if !tries! lss 40 (
  ping -n 2 127.0.0.1 >nul
  goto wait
)
echo [cs80] host did not come up in 20s. Check %TEMP%\cs80_host.log (python missing?)
pause
exit /b 1

:ready
set EDGE=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe
if not exist "%EDGE%" set EDGE=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe
if not exist "%EDGE%" set EDGE=%ProgramFiles%\Google\Chrome\Application\chrome.exe
if not exist "%EDGE%" set EDGE=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe
if exist "%EDGE%" (
  start "" "%EDGE%" --app=%URL% --window-size=1180,1000 --user-data-dir="%LOCALAPPDATA%\cs80-panel\webview"
) else (
  echo [cs80] Edge/Chrome not found, opening %URL% in the default browser
  start "" %URL%
)
