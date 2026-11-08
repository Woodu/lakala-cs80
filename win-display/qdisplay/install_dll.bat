@echo off
net stop QDisplayDevice >nul 2>&1
taskkill /IM QDisplay.exe /F >nul 2>&1
timeout /t 2 /nobreak >nul
if not exist "C:\Program Files\QDisplay\QDisplay.Windows.dll.orig" copy /Y "C:\Program Files\QDisplay\QDisplay.Windows.dll" "C:\Program Files\QDisplay\QDisplay.Windows.dll.orig" >nul
copy /Y "D:\project\lakala-cs80-main\win-display\qdisplay\build\QDisplay.Windows.dll" "C:\Program Files\QDisplay\QDisplay.Windows.dll"
echo COPY_EXIT=%ERRORLEVEL%
net start QDisplayDevice >nul 2>&1
echo SVC_EXIT=%ERRORLEVEL%
start "" "C:\Program Files\QDisplay\QDisplay.exe"
timeout /t 2 /nobreak >nul
echo done
