@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo  CS80 刷机 (AT 软下载模式，不用短接)
echo ============================================
echo.

if exist py\python.exe goto havepy
echo [1/3] 解压便携 Python ...
powershell -NoProfile -Command "Expand-Archive -Force -LiteralPath 'python-embed.zip' -DestinationPath 'py'"
if not exist py\python.exe (echo 解压失败 & pause & exit /b 1)

:fixpth
> py\python313._pth echo python313.zip
>> py\python313._pth echo .
>> py\python313._pth echo ..
>> py\python313._pth echo import site
copy /y pyserial.whl tmp.zip >nul
powershell -NoProfile -Command "Expand-Archive -Force -LiteralPath 'tmp.zip' -DestinationPath 'py'"
del /q tmp.zip

:havepy
py\python.exe -c "import serial" 2>nul
if errorlevel 1 goto fixpth

echo [2/3] 自检固件 ...
py\python.exe -m third_party.sprdflash info fw.pac
if errorlevel 1 (echo 固件包有问题 & pause & exit /b 1)
echo.

echo [3/3] 只读检查设备 ...
py\python.exe flash_at.py --check
if errorlevel 1 (
  echo.
  echo 检查没通过。先把上面输出发我。
  pause & exit /b 1
)

echo.
echo 即将：AT+QDOWNLOAD=1 进入下载模式 - 探测 - 写入 fw.pac
echo 会格式化内部文件系统并重建 NV，原厂拉卡拉固件不可恢复。
pause

py\python.exe flash_at.py
echo.
echo 退出码 %errorlevel%
echo 刷完设备会自己重启，屏幕应进入 QuecPython 启动界面。
pause
