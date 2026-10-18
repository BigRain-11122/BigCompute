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
# 5-min limit is correct here (D-20261019-04 note): the sentinel is a
# scan-only tick; the wake it fires is a DETACHED process (Start-Process
# wscript), so the round it launches is NOT subject to this task's limit
# - only to the loop's own v2 budget (silence 25min / hard cap 240min).
$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName 'BigCompute-OrderSentinel' -Action $a -Trigger $t -Settings $s -Force | Out-Null
# S4U LAW (fleet 2026-09-29 ruling + 2026-10-15 re-adjudication): see
# register_loop_task.ps1 - XML flip form, honest fail on flip error.
try {
    $x = Export-ScheduledTask -TaskName 'BigCompute-OrderSentinel'
    $x2 = $x -replace '<LogonType>[^<]*</LogonType>','<LogonType>S4U</LogonType>' -replace '<StartWhenAvailable>true</StartWhenAvailable>','<StartWhenAvailable>false</StartWhenAvailable>'
    Register-ScheduledTask -TaskName 'BigCompute-OrderSentinel' -Xml $x2 -Force -ErrorAction Stop | Out-Null
    Write-Output "registered BigCompute-OrderSentinel (15min tick, logon=S4U via XML flip, SWA=false), first fire $at"
} catch {
    Write-Output "WARN: S4U XML flip failed ($($_.Exception.Message)) -> BigCompute-OrderSentinel left at default principal, first fire $at"
}
