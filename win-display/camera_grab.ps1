# 截取 Windows「相机」UWP 窗口画面（用来观察 CS80 屏幕）。只置前+截屏，不抢相机占用。
param([string]$Out = "$env:USERPROFILE\cs80_display\cam_shot.png", [int]$Wait = 900)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
Add-Type -ReferencedAssemblies System.Drawing @"
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;

public class CamWin {
  delegate bool EnumProc(IntPtr h, IntPtr l);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
  [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc p, IntPtr l);
  [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);

  public static List<string> List() {
    var res = new List<string>();
    EnumWindows(delegate(IntPtr h, IntPtr l) {
      if (!IsWindowVisible(h)) return true;
      var t = new StringBuilder(512); GetWindowText(h, t, 512);
      var c = new StringBuilder(256); GetClassName(h, c, 256);
      if (c.ToString() == "ApplicationFrameWindow")
        res.Add(h.ToInt64() + "|" + c.ToString() + "|" + t.ToString());
      return true;
    }, IntPtr.Zero);
    return res;
  }
}
"@

$cams = @([CamWin]::List())
if ($cams.Count -eq 0) { "no ApplicationFrameWindow"; exit 1 }
$line = $cams | Where-Object { $_ -match '相机|Camera' } | Select-Object -First 1
if (-not $line) { $line = $cams | Select-Object -First 1 }
"candidate: $line"
$hwnd = [IntPtr][int64]($line -split '\|')[0]

if ([CamWin]::IsIconic($hwnd)) { [CamWin]::ShowWindow($hwnd, 9) | Out-Null; Start-Sleep -Milliseconds 500 }
[CamWin]::SetForegroundWindow($hwnd) | Out-Null
Start-Sleep -Milliseconds $Wait

$r = New-Object CamWin+RECT
[CamWin]::GetWindowRect($hwnd, [ref]$r) | Out-Null
$w = $r.R - $r.L; $hh = $r.B - $r.T
if ($w -le 0 -or $hh -le 0) { "bad rect"; exit 1 }

$bmp = New-Object System.Drawing.Bitmap $w, $hh
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($r.L, $r.T, 0, 0, (New-Object System.Drawing.Size $w, $hh))
$dir = Split-Path -Parent $Out
if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
"ok $Out ${w}x${hh}"
