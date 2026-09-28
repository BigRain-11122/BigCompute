# Registers BigCompute-GPU-IdleWatch: 15-min tick, silent VBS wrapper.
# T-20260928-28 (CEO Self-Drive v2.0 L252 dedicated dispatch, step 2).
# PATH-AGNOSTIC: paths derived from this script's own location.
# Pure ASCII (see iteration_loop.ps1 ENCODING RULE). Idempotent via -Force.
$Project = Split-Path -Parent $PSScriptRoot
$col = Join-Path $Project 'Tools\gpu_idle_collector.py'
$vbs = Join-Path $Project 'Tools\InvisibleRunner.vbs'
if (-not (Test-Path $col)) { Write-Output "FATAL: $col missing"; exit 1 }
if (-not (Test-Path $vbs)) { Write-Output "FATAL: $vbs missing"; exit 1 }
$py = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
if (-not $py) { Write-Output "FATAL: python.exe not on PATH"; exit 1 }
$a = New-ScheduledTaskAction -Execute 'wscript.exe' `
    -Argument ('//B //nologo "' + $vbs + '" "' + $py + '" "' + $col + '" sample') `
    -WorkingDirectory $Project
$at = (Get-Date).AddMinutes(2)
$t = New-ScheduledTaskTrigger -Once -At $at -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration (New-TimeSpan -Days 3650)
$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName 'BigCompute-GPU-IdleWatch' -Action $a -Trigger $t -Settings $s -Force | Out-Null
Write-Output "registered BigCompute-GPU-IdleWatch (15min tick, first fire $at, python=$py)"
