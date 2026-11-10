@echo off
setlocal
cd /d %~dp0
echo Flashing the STOCK firmware back (win-display\qdisplay\qdisplay_native.bin)...
py\python.exe qdisplay_app.py ..\..\win-display\qdisplay\qdisplay_native.bin --auto
pause
