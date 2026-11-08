$ErrorActionPreference = 'Stop'
$base = 'HKLM:\SYSTEM\CurrentControlSet\Enum\USB\VID_2C7C&PID_0901&MI_20'
New-Item -Path $base -Force | Out-Null
New-Item -Path "$base\qd0001" -Force | Out-Null
New-Item -Path "$base\qd0001\Device Parameters" -Force | Out-Null
New-ItemProperty -Path "$base\qd0001\Device Parameters" -Name PortName -Value 'COM15' -PropertyType String -Force | Out-Null
New-ItemProperty -Path "$base\qd0001" -Name FriendlyName -Value 'Quectel USB Data VCOM (mapped)' -PropertyType String -Force | Out-Null
New-ItemProperty -Path "$base\qd0001" -Name DeviceDesc -Value 'Unisoc Generic Serial' -PropertyType String -Force | Out-Null
$v = (Get-ItemProperty -Path "$base\qd0001\Device Parameters" -Name PortName).PortName
Write-Output ("created MI_20 -> " + $v)
