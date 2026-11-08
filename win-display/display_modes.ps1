Add-Type @"
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public class DM {
  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Ansi)]
  public struct DISPLAY_DEVICE {
    public int cb;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst=32)] public string DeviceName;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst=128)] public string DeviceString;
    public int StateFlags;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst=128)] public string DeviceID;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst=128)] public string DeviceKey;
  }
  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Ansi)]
  public struct DEVMODE {
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst=32)] public string dmDeviceName;
    public short dmSpecVersion, dmDriverVersion, dmSize, dmDriverExtra;
    public int dmFields;
    public int dmPositionX, dmPositionY, dmDisplayOrientation, dmDisplayFixedOutput;
    public short dmColor, dmDuplex, dmYResolution, dmTTOption, dmCollate;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst=32)] public string dmFormName;
    public short dmLogPixels;
    public int dmBitsPerPel, dmPelsWidth, dmPelsHeight, dmDisplayFlags, dmDisplayFrequency;
  }
  [DllImport("user32.dll", CharSet=CharSet.Ansi)] public static extern bool EnumDisplayDevices(string dev, uint n, ref DISPLAY_DEVICE d, uint flags);
  [DllImport("user32.dll", CharSet=CharSet.Ansi)] public static extern bool EnumDisplaySettings(string dev, int mode, ref DEVMODE dm);
  public static List<string> Run() {
    var res = new List<string>();
    var dd = new DISPLAY_DEVICE();
    dd.cb = Marshal.SizeOf(dd);
    for (uint i = 0; EnumDisplayDevices(null, i, ref dd, 0); i++) {
      string line = dd.DeviceName + " | " + dd.DeviceString + " | flags=" + dd.StateFlags;
      res.Add(line);
      var dm = new DEVMODE();
      dm.dmSize = (short)Marshal.SizeOf(dm);

      var modes = new HashSet<string>();
      for (int m = 0; EnumDisplaySettings(dd.DeviceName, m, ref dm); m++) {
        string key = dm.dmPelsWidth + "x" + dm.dmPelsHeight + "@" + dm.dmDisplayFrequency;
        if (modes.Add(key) && (dm.dmPelsWidth == 800 || modes.Count < 6)) res.Add("    " + key + " bpp=" + dm.dmBitsPerPel);
      }
      res.Add("    total modes=" + modes.Count);
      dd = new DISPLAY_DEVICE(); dd.cb = Marshal.SizeOf(dd);
    }
    return res;
  }
}
"@
[DM]::Run() | ForEach-Object { $_ }
