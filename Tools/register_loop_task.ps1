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
$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
Register-ScheduledTask -TaskName $TaskName -Action $a -Trigger $t -Settings $s -Force | Out-Null
Write-Output "registered $TaskName lane ${Hour}:${Minute} daily (project=$Project), first fire $at"
