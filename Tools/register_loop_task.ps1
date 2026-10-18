# Registers a BigCompute OS-loop daily lane task (silent VBS wrapper).
# Defaults keep the original 22:43 night lane (task BigCompute-OSLoop, before
# the 23:00 decision-round reporting cutoff).
# O-20260926-2253-HQ-C face 4 (T2 cadence, effective immediately):
#   midday lane = -Hour 12 -Minute 43 -TaskName BigCompute-OSLoop-PM
# PATH-AGNOSTIC: every path is derived from this script's own location.
# Pure ASCII (see iteration_loop.ps1 ENCODING RULE). Idempotent via -Force.
# Re-registering refreshes the task definition - safe self-heal.
param(
    [int]$Hour = 22,
    [int]$Minute = 43,
    [string]$TaskName = 'BigCompute-OSLoop'
)
$Project = Split-Path -Parent $PSScriptRoot
$launcher = Join-Path $Project 'Tools\iteration_loop.ps1'
$vbs = Join-Path $Project 'Tools\InvisibleRunner.vbs'
if (-not (Test-Path $launcher)) { Write-Output "FATAL: $launcher missing"; exit 1 }
if (-not (Test-Path $vbs)) { Write-Output "FATAL: $vbs missing"; exit 1 }
$a = New-ScheduledTaskAction -Execute 'wscript.exe' `
    -Argument ('//B //nologo "' + $vbs + '" powershell.exe -NoProfile -ExecutionPolicy Bypass -File "' + $launcher + '"') `
    -WorkingDirectory $Project
$at = Get-Date -Hour $Hour -Minute $Minute -Second 0
if ($at -le (Get-Date)) { $at = $at.AddDays(1) }
$t = New-ScheduledTaskTrigger -Daily -At $at
# ExecutionTimeLimit 250 (D-20261019-04 adaptation): the loop's v2 budget
# allows a legitimate round up to 240min (hard cap) - the task-level limit
# must sit above it or the scheduler becomes the new wall-clock killer
# (the very face the v2 port removes). The old 30min limit was sized for
# the retired v1 25-min script kill.
$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 250)
Register-ScheduledTask -TaskName $TaskName -Action $a -Trigger $t -Settings $s -Force | Out-Null
# S4U LAW (fleet 2026-09-29 ruling + 2026-10-15 re-adjudication): lane
# tasks register S4U; InteractiveToken is the regression face (desktop
# black-window root cause; the 10-15 sister-pit left this recipe's default
# principal unfixed on the bm-a seat). This host's Register-ScheduledTask
# has no -LogonType parameter (WinPS 5.1 PSScheduledTask module face), so
# S4U is applied via the proven XML flip (register default, export, patch
# LogonType + SWA=false, re-register from XML). Flip failure leaves the
# task alive at default principal and reports the regression honestly.
try {
    $x = Export-ScheduledTask -TaskName $TaskName
    $x2 = $x -replace '<LogonType>[^<]*</LogonType>','<LogonType>S4U</LogonType>' -replace '<StartWhenAvailable>true</StartWhenAvailable>','<StartWhenAvailable>false</StartWhenAvailable>'
    Register-ScheduledTask -TaskName $TaskName -Xml $x2 -Force -ErrorAction Stop | Out-Null
    Write-Output "registered $TaskName lane ${Hour}:${Minute} daily (project=$Project, logon=S4U via XML flip, SWA=false, task-limit 250min), first fire $at"
} catch {
    Write-Output "WARN: S4U XML flip failed ($($_.Exception.Message)) -> $TaskName left at default principal (LogonType regression face: manual S4U re-pass needed from elevated host), first fire $at"
}
