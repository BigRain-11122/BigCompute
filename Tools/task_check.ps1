# task_check.ps1 - schtasks patrol for BigCompute loop tasks (queue tech T10).
# Walks OSLoop-PM + OrderSentinel + GPU-IdleWatch + CleanWindowProbe:
# State/LastRun/LastResult (probe task added with BC-P-17, 09-30).
# tech T18 (2026-09-28): orphan round.lock detection. The lock holds the live
# round's worker PID; the next tick takes a dead-PID lock over at once. A lock
# still sitting there >30min with a dead PID means BOTH wake channels
# (sentinel 15min tick + scheduled rounds) missed >=2 ticks - silent failure
# made visible. Detection only: takeover/cleanup stays with the next tick.
# Exit 1 if any task is missing or not Ready/Running, or an orphan lock shows;
# the round's daily clearing note carries the anomaly line. Pure ASCII.
param([string]$LockPath = '')
$names = @('BigCompute-OSLoop-PM', 'BigCompute-OrderSentinel', 'BigCompute-GPU-IdleWatch', 'BigCompute-CleanWindowProbe')
$bad = 0
foreach ($n in $names) {
    $t = Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue
    if (-not $t) { Write-Output ("MISSING  {0}" -f $n); $bad = 1; continue }
    $i = $t | Get-ScheduledTaskInfo
    Write-Output ("{0,-28} state={1,-8} last={2} result=0x{3:X} next={4}" -f $n, $t.State, $i.LastRunTime, $i.LastTaskResult, $i.NextRunTime)
    if ($t.State -notin @('Ready', 'Running')) { $bad = 1 }
}
# ---- T18: orphan round.lock (logs/os-loop/round.lock) ----
if (-not $LockPath) { $LockPath = Join-Path $PSScriptRoot '..\logs\os-loop\round.lock' }
if (Test-Path $LockPath) {
    $raw = ''
    try { $raw = (Get-Content -Raw $LockPath -ErrorAction Stop).Trim() } catch {}
    $age = ((Get-Date) - (Get-Item $LockPath).LastWriteTime).TotalMinutes
    $lockPid = 0
    if ([int]::TryParse($raw, [ref]$lockPid) -and $lockPid -gt 0) {
        $p = Get-Process -Id $lockPid -ErrorAction SilentlyContinue
        $alive = ($p -and $p.ProcessName -match 'powershell|pwsh|codely|node|python')
        if (-not $alive -and $age -gt 30) {
            Write-Output ("ORPHAN-LOCK round.lock pid={0} dead age={1}min - wake channels silent (T18)" -f $lockPid, [int]$age)
            $bad = 1
        }
    } elseif ($age -gt 120) {
        # legacy non-PID lock: takeover age is 90 (LockMaxAgeMinutes) and ticks
        # fire every <=15min, so >120min with no takeover = channels silent
        Write-Output ("ORPHAN-LOCK round.lock legacy format age={0}min - wake channels silent (T18)" -f [int]$age)
        $bad = 1
    }
}
if ($bad) { Write-Output 'PATROL: ANOMALY'; exit 1 } else { Write-Output 'PATROL: all green'; exit 0 }
