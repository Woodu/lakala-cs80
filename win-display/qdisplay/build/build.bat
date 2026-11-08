@echo off
cd /d "%~dp0"
"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe" /nologo /target:library /optimize+ /unsafe+ /out:QDisplay.Windows.dll /r:QDisplay.Core.dll /r:System.Drawing.dll /r:System.Web.Extensions.dll AssemblyInfo.cs Ipc.cs Displays.cs Graphics.cs Loopback.cs SystemLog.cs FolderPicker.cs DesktopCapture.cs
echo EXITCODE=%ERRORLEVEL%
