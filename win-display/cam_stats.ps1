param([string]$Img = "$env:USERPROFILE\cs80_display\cam.jpg",
      [int]$X0 = 470, [int]$X1 = 780, [int]$Y0 = 150, [int]$Y1 = 700, [int]$Bands = 8)

$ErrorActionPreference = 'Stop'
Add-Type -ReferencedAssemblies System.Drawing -TypeDefinition @"
using System;
using System.Drawing;
using System.Text;
public class CamStats {
  public static string Run(string path, int x0, int x1, int y0, int y1, int bands) {
    var sb = new StringBuilder();
    using (var bmp = new Bitmap(path)) {
      sb.AppendLine("image " + bmp.Width + "x" + bmp.Height);
      int step = Math.Max(1, (y1 - y0) / bands);
      for (int i = 0; i < bands; i++) {
        int ya = y0 + i * step;
        int yb = Math.Min(ya + step - 1, y1 - 1);
        long sr = 0, sg = 0, sb2 = 0; int n = 0;
        for (int y = ya; y <= yb; y += 2)
          for (int x = x0; x <= x1; x += 2) { var c = bmp.GetPixel(x, y); sr += c.R; sg += c.G; sb2 += c.B; n++; }
        if (n == 0) continue;
        int r = (int)(sr / n), g = (int)(sg / n), b = (int)(sb2 / n);
        int mx = Math.Max(r, Math.Max(g, b)), mn = Math.Min(r, Math.Min(g, b));
        string hue;
        if (mx - mn < 14) hue = (mx > 235) ? "SAT_WHITE" : (mx < 25 ? "BLACK" : "GRAY");
        else if (r == mx && g >= b) hue = "RED_YEL";
        else if (g == mx) hue = "GREEN";
        else if (b == mx) hue = "BLUE";
        else hue = "MAGENTA";
        sb.AppendLine(String.Format("band{0} y={1,4}-{2,4}  R={3,3} G={4,3} B={5,3}  {6}", i, ya, yb, r, g, b, hue));
      }
    }
    return sb.ToString();
  }
}
"@
"Img=<$Img> exists=" + (Test-Path -LiteralPath $Img)
[CamStats]::Run($Img, $X0, $X1, $Y0, $Y1, $Bands)
