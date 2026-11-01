# Registers BigCompute-LaneTaskGuard: 5-min tick, silent VBS wrapper, S4U.
# D-20261101-16 (1): revive lane tasks found Disabled while flag=resumed.
# PATH-AGNOSTIC: paths derived from this script's own location.
# Pure ASCII (see iteration_loop.ps1 ENCODING RULE). Idempotent.
param(
    [int]$IntervalMinutes = 5,
    [switch]$ReportOnly
)
$Project = Split-Path -Parent $PSScriptRoot
$guard = Join-Path $Project 'Tools\lane-task-guard.ps1'
$vbs = Join-Path $Project 'Tools\InvisibleRunner.vbs'
if (-not (Test-Path $guard)) { Write-Output "FATAL: $guard missing"; exit 1 }
if (-not (Test-Path $vbs)) { Write-Output "FATAL: $vbs missing"; exit 1 }

$inner = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "' + $guard + '"'
if (-not $ReportOnly) { $inner = $inner + ' -Apply' }

$a = New-ScheduledTaskAction -Execute 'wscript.exe' `
    -Argument ('//B //nologo "' + $vbs + '" ' + $inner) `
    -WorkingDirectory $Project
$at = (Get-Date).AddMinutes(1)
$t = New-ScheduledTaskTrigger -Once -At $at -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) -RepetitionDuration (New-TimeSpan -Days 3650)
$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 3)
Register-ScheduledTask -TaskName 'BigCompute-LaneTaskGuard' -Action $a -Trigger $t -Settings $s -Force | Out-Null

try {
    $x = Export-ScheduledTask -TaskName 'BigCompute-LaneTaskGuard'
    $x2 = $x -replace '<LogonType>[^<]*</LogonType>','<LogonType>S4U</LogonType>' -replace '<StartWhenAvailable>true</StartWhenAvailable>','<StartWhenAvailable>false</StartWhenAvailable>'
    Register-ScheduledTask -TaskName 'BigCompute-LaneTaskGuard' -Xml $x2 -Force -ErrorAction Stop | Out-Null
    Write-Output "registered BigCompute-LaneTaskGuard (${IntervalMinutes}min, S4U via XML flip, SWA=false, apply=$(-not $ReportOnly)), first fire $at"
} catch {
    Write-Output "WARN: S4U XML flip failed ($($_.Exception.Message)) -> BigCompute-LaneTaskGuard left at default principal, first fire $at"
}