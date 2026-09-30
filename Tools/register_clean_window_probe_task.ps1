# Registers BigCompute-CleanWindowProbe: 15-min tick, silent VBS wrapper.
# BC-P-17 (proposal-filed 2026-09-29 22:43 round): clean-window discovery for
# true-window tests (M25/T26/E17/T15) + 10-05 review frequency evidence.
# PATH-AGNOSTIC: paths derived from this script's own location.
# Pure ASCII (see iteration_loop.ps1 ENCODING RULE). Idempotent via -Force.
$Project = Split-Path -Parent $PSScriptRoot
$probe = Join-Path $Project 'Tools\clean_window_probe.py'
$vbs = Join-Path $Project 'Tools\InvisibleRunner.vbs'
if (-not (Test-Path $probe)) { Write-Output "FATAL: $probe missing"; exit 1 }
if (-not (Test-Path $vbs)) { Write-Output "FATAL: $vbs missing"; exit 1 }
$py = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
if (-not $py) { Write-Output "FATAL: python.exe not on PATH"; exit 1 }
$a = New-ScheduledTaskAction -Execute 'wscript.exe' `
    -Argument ('//B //nologo "' + $vbs + '" "' + $py + '" "' + $probe + '" check') `
    -WorkingDirectory $Project
$at = (Get-Date).AddMinutes(2)
$t = New-ScheduledTaskTrigger -Once -At $at -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration (New-TimeSpan -Days 3650)
$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName 'BigCompute-CleanWindowProbe' -Action $a -Trigger $t -Settings $s -Force | Out-Null
Write-Output "registered BigCompute-CleanWindowProbe (15min tick, first fire $at, python=$py)"
