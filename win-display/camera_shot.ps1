# 用本机摄像头直接拍一张（MediaCapture，无窗口）。用于观察 CS80 屏幕。
param([string]$Out = "$env:USERPROFILE\cs80_display\cam.jpg", [int]$Wait = 1200)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.Capture.MediaCapture, Windows.Media, ContentType = WindowsRuntime]
$null = [Windows.Media.Capture.MediaCaptureInitializationSettings, Windows.Media, ContentType = WindowsRuntime]
$null = [Windows.Media.MediaProperties.ImageEncodingProperties, Windows.Media, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.InMemoryRandomAccessStream, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.DataReader, Windows.Storage, ContentType = WindowsRuntime]

$ext = [System.WindowsRuntimeSystemExtensions]
$asTask1 = ($ext.GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
$asTaskA = ($ext.GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction' })[0]
function AwaitAction($op) { $t = $asTaskA.Invoke($null, @($op)); $t.Wait(-1) | Out-Null }
function Await($op, $type) { $t = $asTask1.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait(-1) | Out-Null; return $t.Result }
function ShowErr($e) { while ($e.InnerException) { $e = $e.InnerException }; "  " + $e.GetType().Name + " hr=0x" + ('{0:X8}' -f $e.HResult) + " :: " + $e.Message }

$mc = $null
try {
    $mc = New-Object Windows.Media.Capture.MediaCapture
    $settings = New-Object Windows.Media.Capture.MediaCaptureInitializationSettings
    $settings.StreamingCaptureMode = [Windows.Media.Capture.StreamingCaptureMode]::Video
    AwaitAction ($mc.InitializeAsync($settings))
    # 压低曝光（屏幕很亮，自动曝光会顶满）
    try {
        $e = $mc.VideoDeviceController.Exposure
        if ($e -and $e.Supported) {
            $e.TrySetAuto($false) | Out-Null
            $e.TrySetValue($e.Min) | Out-Null
            "exposure: min=$($e.Min) value=$($e.Value)"
        } else { "exposure: not supported" }
        $w = $mc.VideoDeviceController.WhiteBalance
        if ($w -and $w.Supported) { $w.TrySetAuto($false) | Out-Null; "wb: fixed" }
    } catch { "exposure tuning failed: $($_.Exception.Message)" }
    Start-Sleep -Milliseconds $Wait
    $props = [Windows.Media.MediaProperties.ImageEncodingProperties]::CreateJpeg()
    $stream = New-Object Windows.Storage.Streams.InMemoryRandomAccessStream
    AwaitAction ($mc.CapturePhotoToStreamAsync($props, $stream))
    $len = [int]$stream.Size
    $stream.Seek(0)
    $reader = New-Object Windows.Storage.Streams.DataReader($stream.GetInputStreamAt(0))
    $null = Await ($reader.LoadAsync([uint32]$len)) ([uint32])
    $bytes = New-Object byte[] $len
    $reader.ReadBytes($bytes)
    $dir = Split-Path -Parent $Out
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }
    [System.IO.File]::WriteAllBytes($Out, $bytes)
    "ok $Out $len"
} catch {
    "FAIL"; ShowErr $_.Exception
} finally {
    if ($mc) { try { $mc.Dispose() } catch {} }
}
