using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.Runtime.InteropServices;
using System.IO;
using System.Diagnostics;
using System.Net.NetworkInformation;
using QDisplay.Core;
namespace QDisplay.Windows {
    public static class Pictures {
        public static byte[] Pixels(Bitmap bitmap){Rectangle rect=new Rectangle(0,0,800,1280);BitmapData data=bitmap.LockBits(rect,ImageLockMode.ReadOnly,PixelFormat.Format32bppArgb);
            try{byte[] rgb=new byte[800*1280*4],result=new byte[NativeLink.FrameBytes];for(int y=0;y<1280;y++)Marshal.Copy(IntPtr.Add(data.Scan0,y*data.Stride),rgb,y*3200,3200);
                for(int i=0,p=0;i<rgb.Length;i+=4,p+=2){ushort color=(ushort)((rgb[i]>>3)<<11|(rgb[i+1]>>2)<<5|(rgb[i+2]>>3));result[p]=(byte)color;result[p+1]=(byte)(color>>8);}return result;
            }finally{bitmap.UnlockBits(data);}}
        public static Bitmap Photo(string path){using(Image source=Image.FromFile(path)){Bitmap b=new Bitmap(800,1280);using(Graphics g=Graphics.FromImage(b)){
            g.Clear(Color.FromArgb(12,17,25));g.InterpolationMode=InterpolationMode.HighQualityBicubic;
            double scale=Math.Min(800.0/source.Width,1280.0/source.Height);int w=(int)(source.Width*scale),h=(int)(source.Height*scale);g.DrawImage(source,(800-w)/2,(1280-h)/2,w,h);}return b;}}
        [StructLayout(LayoutKind.Sequential)]struct Cursor {public int Size,Flags;public IntPtr Handle;public int X,Y;}
        [DllImport("user32.dll")]static extern bool GetCursorInfo(ref Cursor cursor);
        [DllImport("user32.dll")]static extern bool DrawIconEx(IntPtr dc,int x,int y,IntPtr icon,int width,int height,uint step,IntPtr brush,uint flags);
        public static Bitmap Capture(){Rectangle rect=Displays.Bounds();Bitmap b=new Bitmap(800,1280);try{using(Graphics g=Graphics.FromImage(b)){
            g.CopyFromScreen(rect.Location,Point.Empty,rect.Size,CopyPixelOperation.SourceCopy);Cursor cursor=new Cursor{Size=Marshal.SizeOf(typeof(Cursor))};
            if(GetCursorInfo(ref cursor)&&(cursor.Flags&1)!=0&&rect.Contains(cursor.X,cursor.Y)){IntPtr dc=g.GetHdc();try{DrawIconEx(dc,cursor.X-rect.X,cursor.Y-rect.Y,cursor.Handle,0,0,0,IntPtr.Zero,3);}finally{g.ReleaseHdc(dc);}}}return b;
        }catch{b.Dispose();throw;}}
        public static Bitmap Logo(int size){Bitmap b=new Bitmap(size,size);using(Graphics g=Graphics.FromImage(b)){
            g.SmoothingMode=SmoothingMode.AntiAlias;g.Clear(Color.Transparent);float s=size/128f;
            using(SolidBrush back=new SolidBrush(Color.FromArgb(18,28,43)))g.FillEllipse(back,2*s,2*s,124*s,124*s);
            using(Pen p=new Pen(Color.FromArgb(66,211,214),8*s)){g.DrawRectangle(p,25*s,30*s,78*s,57*s);g.DrawLine(p,64*s,91*s,64*s,101*s);g.DrawLine(p,45*s,103*s,83*s,103*s);g.DrawLine(p,86*s,76*s,104*s,94*s);}
        }return b;}
    }
    public sealed class Dashboard : IDisposable {
        [StructLayout(LayoutKind.Sequential)]struct Memory {public uint Length,Load;public ulong Total,Available,PageTotal,PageAvailable,VirtualTotal,VirtualAvailable,Extended;}
        [DllImport("kernel32.dll")]static extern bool GetSystemTimes(out long idle,out long kernel,out long user);
        [DllImport("kernel32.dll")]static extern bool GlobalMemoryStatusEx(ref Memory memory);
        long previousIdle,previousTotal,previousNetwork;DateTime last=DateTime.MinValue;readonly Queue<float> cpu=new Queue<float>(),memory=new Queue<float>(),gpu=new Queue<float>();
        public double Cpu,Used,Total,Gpu=Double.NaN,GpuTemp=Double.NaN,Network;public string GpuName="",CpuName="";int tick;
        readonly Font small=new Font("Microsoft YaHei UI",20),medium=new Font("Microsoft YaHei UI",29),large=new Font("Segoe UI",48,FontStyle.Regular);
        readonly Font cpuFont=new Font("Segoe UI",16);
        public Dashboard(){try{CpuName=Convert.ToString(Microsoft.Win32.Registry.GetValue(@"HKEY_LOCAL_MACHINE\HARDWARE\DESCRIPTION\System\CentralProcessor\0","ProcessorNameString",""));}catch{}}
        public void Sample(){long idle,kernel,user;if(GetSystemTimes(out idle,out kernel,out user)){long total=kernel+user;Cpu=previousTotal==0?0:Math.Max(0,Math.Min(100,100.0*(total-previousTotal-idle+previousIdle)/Math.Max(1,total-previousTotal)));previousTotal=total;previousIdle=idle;}
            Memory m=new Memory{Length=(uint)Marshal.SizeOf(typeof(Memory))};if(GlobalMemoryStatusEx(ref m)){Total=m.Total/1073741824.0;Used=(m.Total-m.Available)/1073741824.0;}
            long bytes=0;foreach(NetworkInterface ni in NetworkInterface.GetAllNetworkInterfaces())if(ni.OperationalStatus==OperationalStatus.Up&&ni.NetworkInterfaceType!=NetworkInterfaceType.Loopback){try{IPv4InterfaceStatistics s=ni.GetIPv4Statistics();bytes+=s.BytesReceived+s.BytesSent;}catch{}}
            DateTime now=DateTime.UtcNow;if(last!=DateTime.MinValue)Network=Math.Max(0,(bytes-previousNetwork)/(now-last).TotalSeconds/1048576);previousNetwork=bytes;last=now;
            if(tick++%3==0){try{using(Process p=Process.Start(new ProcessStartInfo("nvidia-smi.exe","--query-gpu=name,utilization.gpu,temperature.gpu --format=csv,noheader,nounits"){UseShellExecute=false,CreateNoWindow=true,RedirectStandardOutput=true,RedirectStandardError=true})){
                if(p.WaitForExit(500)){string[] values=p.StandardOutput.ReadToEnd().Trim().Split(',');if(values.Length>=3){GpuName=values[0].Trim();double.TryParse(values[1].Trim(),out Gpu);double.TryParse(values[2].Trim(),out GpuTemp);}}else p.Kill();}}catch{}}
            Push(cpu,(float)Cpu);Push(memory,Total>0?(float)(Used*100/Total):0);Push(gpu,Double.IsNaN(Gpu)?0:(float)Gpu);
        }
        static void Push(Queue<float> q,float value){q.Enqueue(value);while(q.Count>60)q.Dequeue();}
        void Text(Graphics g,string t,Font f,Color c,float x,float y){using(Brush b=new SolidBrush(c))g.DrawString(t,f,b,x,y);}
        void Chart(Graphics g,Queue<float> q,Rectangle r,Color color){using(Pen grid=new Pen(Color.FromArgb(39,54,73),1)){for(int i=0;i<=4;i++)g.DrawLine(grid,r.Left,r.Top+r.Height*i/4,r.Right,r.Top+r.Height*i/4);for(int i=0;i<=6;i++)g.DrawLine(grid,r.Left+r.Width*i/6,r.Top,r.Left+r.Width*i/6,r.Bottom);}
            float[] values=q.ToArray();if(values.Length<2)return;PointF[] points=new PointF[values.Length];for(int i=0;i<points.Length;i++)points[i]=new PointF(r.Right-(values.Length-1-i)*r.Width/59f,r.Bottom-values[i]*r.Height/100f);
            using(Pen p=new Pen(color,3))g.DrawLines(p,points);
        }
        static string JsonField(string json,string key){int i=json.IndexOf("\""+key+"\"");if(i<0)return "";i=json.IndexOf(':',i)+1;if(i<=0)return "";while(i<json.Length&&Char.IsWhiteSpace(json[i]))i++;if(i<json.Length&&json[i]=='"'){int e=json.IndexOf('"',i+1);return e>i?json.Substring(i+1,e-i-1):"";}int e2=i;while(e2<json.Length&&",}\r\n ".IndexOf(json[e2])<0)e2++;return json.Substring(i,e2-i).Trim();}
        public Bitmap Draw(){
            Bitmap b=new Bitmap(800,1280);
            using(Graphics g=Graphics.FromImage(b)){
                g.SmoothingMode=SmoothingMode.AntiAlias;
                g.Clear(Color.FromArgb(12,18,28));
                Color white=Color.FromArgb(236,242,247),muted=Color.FromArgb(144,163,186),cyan=Color.FromArgb(62,213,221),violet=Color.FromArgb(154,142,250),line=Color.FromArgb(39,54,73);
                DateTime now=DateTime.Now;
                string[] wd={"日","一","二","三","四","五","六"};
                Text(g,now.ToString("yyyy-MM-dd")+"  星期"+wd[(int)now.DayOfWeek],medium,muted,38,42);
                using(Font clock=new Font("Segoe UI Light",104))Text(g,now.ToString("HH:mm"),clock,white,30,88);
                using(Pen pen=new Pen(line,2)){g.DrawLine(pen,38,262,762,262);g.DrawLine(pen,38,596,762,596);g.DrawLine(pen,38,1148,762,1148);}
                string st="idle",task="",since="";
                try{string json=File.ReadAllText(@"C:\Users\XBS\cs80_display\state.json");st=JsonField(json,"state");task=JsonField(json,"task");since=JsonField(json,"since");}catch{}
                Color sc=st=="running"?Color.FromArgb(82,200,142):st=="waiting"?Color.FromArgb(242,190,70):st=="done"?Color.FromArgb(82,200,142):st=="error"?Color.FromArgb(236,92,92):Color.FromArgb(128,136,150);
                string stt=st=="running"?"运行中":st=="waiting"?"等待输入":st=="done"?"已完成":st=="error"?"出错":"空闲";
                using(SolidBrush dot=new SolidBrush(sc))g.FillEllipse(dot,38,306,44,44);
                Text(g,stt,large,white,100,292);
                double secs=0;double.TryParse(since,out secs);
                string dur="";
                if(secs>0&&(st=="running"||st=="waiting")){long el=(long)Math.Max(0,(DateTime.UtcNow-new DateTime(1970,1,1,0,0,0,DateTimeKind.Utc)).TotalSeconds-secs);dur=string.Format("已持续 {0:00}:{1:00}:{2:00}",el/3600,el/60%60,el%60);}
                Text(g,dur,small,muted,104,376);
                if(task.Length>0){int n=Math.Min(task.Length,44);Text(g,task.Substring(0,n),medium,white,38,432);if(task.Length>44)Text(g,task.Length>88?task.Substring(44,44):task.Substring(44),medium,white,38,480);}
                Text(g,"CPU",medium,cyan,38,626);Text(g,Cpu.ToString("0")+"%",large,white,520,608);Chart(g,cpu,new Rectangle(40,696,720,150),cyan);
                Text(g,CpuName.Length>46?CpuName.Substring(0,46):CpuName,cpuFont,muted,40,858);
                Text(g,"内存",medium,violet,38,898);Text(g,Used.ToString("0.0")+" / "+Total.ToString("0.0")+" GB",medium,white,330,906);
                using(SolidBrush track=new SolidBrush(line))g.FillRectangle(track,38,952,724,14);
                if(Total>0){using(SolidBrush fill=new SolidBrush(violet))g.FillRectangle(fill,38,952,(int)(724*Math.Min(100,Used*100/Total)/100),14);}
                Text(g,"网络",medium,muted,38,1000);Text(g,Network.ToString("0.00")+" MB/s",medium,white,330,1000);
                Text(g,Environment.MachineName,small,muted,38,1168);
                Text(g,"CS80 · QDisplay",small,muted,520,1168);
            }
            return b;
        }
        public void Dispose(){small.Dispose();medium.Dispose();large.Dispose();cpuFont.Dispose();}
    }
}
