# task_check.ps1 - schtasks patrol for BigCompute loop tasks (queue tech T10).
# Walks OSLoop-PM + OrderSentinel + GPU-IdleWatch: State/LastRun/LastResult.
# Exit 1 if any task is missing or not Ready/Running; the round's daily
# clearing note carries the anomaly line. Pure ASCII.
$names = @('BigCompute-OSLoop-PM', 'BigCompute-OrderSentinel', 'BigCompute-GPU-IdleWatch')
$bad = 0
foreach ($n in $names) {
    $t = Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue
    if (-not $t) { Write-Output ("MISSING  {0}" -f $n); $bad = 1; continue }
    $i = $t | Get-ScheduledTaskInfo
    Write-Output ("{0,-28} state={1,-8} last={2} result=0x{3:X} next={4}" -f $n, $t.State, $i.LastRunTime, $i.LastTaskResult, $i.NextRunTime)
    if ($t.State -notin @('Ready', 'Running')) { $bad = 1 }
}
if ($bad) { Write-Output 'PATROL: ANOMALY'; exit 1 } else { Write-Output 'PATROL: all green'; exit 0 }
