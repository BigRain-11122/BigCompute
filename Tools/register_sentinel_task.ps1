# Registers BigCompute-OrderSentinel: 15-min tick, silent VBS wrapper.
# Self-built P0 ack<=30min wake path (O-20260926-2253-HQ-C face 4).
# PATH-AGNOSTIC: paths derived from this script's own location.
# Pure ASCII (see iteration_loop.ps1 ENCODING RULE). Idempotent via -Force.
$Project = Split-Path -Parent $PSScriptRoot
$sen = Join-Path $Project 'Tools\order_sentinel.ps1'
$vbs = Join-Path $Project 'Tools\InvisibleRunner.vbs'
if (-not (Test-Path $sen)) { Write-Output "FATAL: $sen missing"; exit 1 }
if (-not (Test-Path $vbs)) { Write-Output "FATAL: $vbs missing"; exit 1 }
$a = New-ScheduledTaskAction -Execute 'wscript.exe' `
    -Argument ('//B //nologo "' + $vbs + '" powershell.exe -NoProfile -ExecutionPolicy Bypass -File "' + $sen + '"') `
    -WorkingDirectory $Project
$at = (Get-Date).AddMinutes(2)
$t = New-ScheduledTaskTrigger -Once -At $at -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration (New-TimeSpan -Days 3650)
$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName 'BigCompute-OrderSentinel' -Action $a -Trigger $t -Settings $s -Force | Out-Null
Write-Output "registered BigCompute-OrderSentinel (15min tick, first fire $at)"
