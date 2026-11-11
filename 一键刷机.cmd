@echo off
rem ---------------------------------------------------------------------------
rem  CS80 one-click flasher. Double-click this file.
rem  First run unpacks the portable Python (win-flash\flash\python-embed.zip)
rem  and pyserial, then hands over to win-flash\flash\flash_oneclick.py.
rem  All user-facing text is printed by Python (UTF-8), so this file stays ASCII.
rem ---------------------------------------------------------------------------
setlocal
chcp 65001 >nul 2>nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

cd /d "%~dp0win-flash\flash" 2>nul
if errorlevel 1 goto :nodir

if not exist "py\python.exe" call :bootstrap
if not exist "py\python.exe" goto :fail
if not exist "py\Lib\site-packages\serial\__init__.py" call :deps
py\python.exe -c "import serial" >nul 2>nul
if errorlevel 1 goto :fail

py\python.exe flash_oneclick.py %*
set RC=%ERRORLEVEL%
echo.
if not "%CS80_NO_PAUSE%"=="1" pause
exit /b %RC%

rem ---------------------------------------------------------------- 便携 Python
:bootstrap
echo [setup] unpacking portable Python (first run only) ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -Force -LiteralPath 'python-embed.zip' -DestinationPath 'py'" 2>nul
if not exist "py\python.exe" goto :eof
>"py\python313._pth" echo python313.zip
>>"py\python313._pth" echo .
>>"py\python313._pth" echo Lib/site-packages
goto :eof

:deps
echo [setup] installing pyserial into the portable Python ...
if not exist "py\Lib\site-packages" mkdir "py\Lib\site-packages"
set "TMPW=%TEMP%\cs80_pyserial_%RANDOM%.zip"
copy /y "pyserial.whl" "%TMPW%" >nul
powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -Force -LiteralPath '%TMPW%' -DestinationPath 'py\Lib\site-packages'" 2>nul
del /f /q "%TMPW%" >nul 2>nul
goto :eof

rem ---------------------------------------------------------------- 出错
:nodir
echo [error] cannot find win-flash\flash - keep this .cmd at the repo root.
if not "%CS80_NO_PAUSE%"=="1" pause
exit /b 1

:fail
echo.
echo [error] portable Python setup failed.
echo         Delete the folder win-flash\flash\py and run this file again.
if not "%CS80_NO_PAUSE%"=="1" pause
exit /b 1
