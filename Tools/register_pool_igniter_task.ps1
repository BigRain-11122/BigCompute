# Registers BigCompute-PoolIgniter: 15-min tick, silent VBS wrapper.
# D-20261108-09: four stocked cards read dispatchable for days while the pool
# stayed at effective=0, because a round only gets one look and the lane was
# closed at that moment.  This watcher takes the look every 15 minutes and runs
# the card the moment a lane actually opens (gate first, receipt after).
# Principal mirrors the box's existing BigCompute-* tasks (Interactive/Limited +
# wscript //B wrapper) - S4U registration needs elevation this session lacks.
# Pure ASCII (see iteration_loop.ps1 ENCODING RULE). Idempotent via -Force.
$Project = Split-Path -Parent $PSScriptRoot
$ign = Join-Path $Project 'Tools\pool_igniter.py'
$vbs = Join-Path $Project 'Tools\InvisibleRunner.vbs'
if (-not (Test-Path $ign)) { Write-Output "FATAL: $ign missing"; exit 1 }
if (-not (Test-Path $vbs)) { Write-Output "FATAL: $vbs missing"; exit 1 }
$py = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
if (-not $py) { Write-Output "FATAL: python.exe not on PATH"; exit 1 }
$a = New-ScheduledTaskAction -Execute 'wscript.exe' `
    -Argument ('//B //nologo "' + $vbs + '" "' + $py + '" "' + $ign + '" tick') `
    -WorkingDirectory $Project
$at = (Get-Date).AddMinutes(3)
$t = New-ScheduledTaskTrigger -Once -At $at -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration (New-TimeSpan -Days 3650)
$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 40)
$p = New-ScheduledTaskPrincipal -UserId "$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName 'BigCompute-PoolIgniter' -Action $a -Trigger $t `
    -Settings $s -Principal $p -Force -ErrorAction Stop | Out-Null
$chk = Get-ScheduledTask -TaskName 'BigCompute-PoolIgniter' -ErrorAction Stop
if (-not $chk) { throw "register failed: task not found after registration" }
Write-Output "registered BigCompute-PoolIgniter (15min tick, first fire $at, state=$($chk.State), logon=$($chk.Principal.LogonType))"