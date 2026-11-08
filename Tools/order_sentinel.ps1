# BigCompute order/decision sentinel - self-built P0 ack<=30min wake path
# (O-20260926-2253-HQ-C face 4; silent law via InvisibleRunner.vbs wrapper).
# ENCODING RULE: pure ASCII (see iteration_loop.ps1).
#
# M167 (2026-11-08): whole-file hashing called any rewrite "a new order", so a
# concurrent rewrite with no new row burned a round. The decision is now made by
# Tools\sentinel_wake_judge.py (pure, selftest 10/10) over the SET of non-blank
# lines, because the group ledgers were measured to contain real C0 control
# bytes that make PowerShell's own text handling drift. This script only
# orchestrates: judge -> (skip | defer | launch) -> persist snapshot.
#
# Safety bias is unchanged: anything not provably identical still wakes. A
# missed CEO order costs far more than one idle round.
param(
    [switch]$JudgeOnly,      # decide and print, never launch / never persist
    [string]$DocsDir = '',   # fixture override
    [string]$StatePath = ''  # fixture override
)
$ErrorActionPreference = 'Continue'
$Project = Split-Path -Parent $PSScriptRoot
$judge = Join-Path $Project 'Tools\sentinel_wake_judge.py'
$logDir = Join-Path $Project 'logs\os-loop'
New-Item -ItemType Directory -Force $logDir | Out-Null
$sentLog = Join-Path $logDir 'sentinel.log'
function SLog([string]$m) {
    if ($JudgeOnly) { return }
    Add-Content -Path $sentLog -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $m" -Encoding UTF8
}
function Invoke-Judge([switch]$Persist) {
    $a = @('-B', $judge, 'judge')
    if ($DocsDir -ne '') { $a += @('--docs', $DocsDir) }
    if ($StatePath -ne '') { $a += @('--state', $StatePath) }
    if ($Persist) { $a += '--commit' }
    $out = & python.exe @a 2>&1
    $rc = $LASTEXITCODE
    $line = ($out | Where-Object { $_ -like 'VERDICT *' }) -join ' '
    return @{ rc = $rc; text = $line }
}
$j = Invoke-Judge
$verdict = 'WAKE-TRANSITION'
$detail = $j.text
if ($j.rc -eq 0 -and $j.text -match '^VERDICT\s+(\S+)') {
    $verdict = $Matches[1]
} else {
    # a broken judge must never read as "nothing new"
    $detail = 'judge failed rc=' + $j.rc + ' out=' + ($j.text -replace '\s+', ' ')
}
if ($JudgeOnly) {
    Write-Output ('JUDGE ' + $verdict + ' ' + $detail)
    exit 0
}
# T62 night-blind-window task-liveness probe (R-51 mitigation-3): silent
# pythonw fire-and-forget every tick; latched anomaly-only heartbeat alert
# (zero appends on normal ticks). Fallback chain pythonw -> python hidden.
try {
    Start-Process -FilePath 'pythonw.exe' -ArgumentList ('"' +
        (Join-Path $Project 'Tools\idle_selfcheck.py') + '" night_watch') -WindowStyle Hidden
} catch {
    try {
        Start-Process -FilePath 'python.exe' -ArgumentList ('"' +
            (Join-Path $Project 'Tools\idle_selfcheck.py') + '" night_watch') -WindowStyle Hidden
    } catch { SLog ('night_watch launch failed: ' + $_.Exception.Message) }
}
if ($verdict -eq 'SKIP') { exit 0 }
if ($verdict -eq 'BASELINE') {
    $null = Invoke-Judge -Persist
    SLog 'baseline lineset created (no wake)'
    exit 0
}
# change detected - if a round is in flight, leave the snapshot stale so the
# next tick wakes once the running round has finished (no lost wake)
$lock = Join-Path $logDir 'round.lock'
if (Test-Path $lock) {
    $raw = ''
    try { $raw = (Get-Content -Raw $lock -ErrorAction Stop).Trim() } catch {}
    $lockPid = 0
    if ([int]::TryParse($raw, [ref]$lockPid) -and $lockPid -gt 0) {
        $p = Get-Process -Id $lockPid -ErrorAction SilentlyContinue
        if ($p -and $p.ProcessName -match 'powershell|pwsh|codely|node|python') {
            SLog "change detected but round in flight (pid=$lockPid) - wake deferred"
            exit 0
        }
    }
}
$vbs = Join-Path $Project 'Tools\InvisibleRunner.vbs'
$launcher = Join-Path $Project 'Tools\iteration_loop.ps1'
if (Test-Path $vbs) {
    Start-Process -FilePath 'wscript.exe' -ArgumentList ('//B //nologo "' + $vbs + '" powershell.exe -NoProfile -ExecutionPolicy Bypass -File "' + $launcher + '"') -WorkingDirectory $Project -WindowStyle Hidden
}
$null = Invoke-Judge -Persist
SLog ('group orders/decisions changed - wake fired (' + $detail + ')')
exit 0