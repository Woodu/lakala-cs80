# 点 QDisplay GUI 的「副屏」按钮：枚举子控件 -> BM_CLICK（脚本必须 UTF-8 BOM，否则中文乱码）
param([string]$Title = 'QDisplay', [string]$Want = '副屏', [switch]$List)
$ErrorActionPreference = 'Stop'
Add-Type @"
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public class CB {
  delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc p, IntPtr l);
  [DllImport("user32.dll")] static extern bool EnumChildWindows(IntPtr parent, EnumProc p, IntPtr l);
  [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr h, uint msg, IntPtr wp, IntPtr lp);
  public static IntPtr Top = IntPtr.Zero;
  public static IntPtr Found = IntPtr.Zero;
  public static string Want = "";
  public static List<string> Lines = new List<string>();
  public static IntPtr FindTop(string title) {
    Top = IntPtr.Zero;
    EnumWindows(delegate(IntPtr h, IntPtr l) {
      if (!IsWindowVisible(h)) return true;
      var t = new StringBuilder(512); GetWindowText(h, t, 512);
      if (t.ToString().Contains(title)) { Top = h; return false; }
      return true;
    }, IntPtr.Zero);
    return Top;
  }
  public static void Walk() {
    Lines.Clear(); Found = IntPtr.Zero;
    EnumChildWindows(Top, delegate(IntPtr h, IntPtr l) {
      var t = new StringBuilder(512); GetWindowText(h, t, 512);
      var c = new StringBuilder(256); GetClassName(h, c, 256);
      var name = t.ToString();
      var cls = c.ToString();
      Lines.Add(h.ToInt64() + "|" + cls + "|" + name);
      if (Want.Length > 0 && cls.Contains("BUTTON") && name.Contains(Want) && Found == IntPtr.Zero) Found = h;
      return true;
    }, IntPtr.Zero);
  }
}
"@
if ([CB]::FindTop($Title) -eq [IntPtr]::Zero) { 'no window'; exit 1 }
[CB]::Want = $Want
[CB]::Walk()
if ($List) { [CB]::Lines | ForEach-Object { $_ } }
if ([CB]::Found -ne [IntPtr]::Zero) {
    [CB]::SendMessage([CB]::Found, 0x00F5, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null
    'BM_CLICK -> ' + [CB]::Found
    Start-Sleep -Milliseconds 1500
} else { 'button not found: ' + $Want }
