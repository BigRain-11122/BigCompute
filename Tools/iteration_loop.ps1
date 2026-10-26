# BigCompute OS loop - headless round launcher (daily 22:43, silent law).
#
# Round budget v2 (D-20261019-04 cross-line port; canonical source =
# quant/bigmoney/Tools/iteration_loop.ps1 @ 945c3f60, O-20261018-0948 root
# fix 1): kills happen only on EVIDENCE - $RoundSilenceMinutes of zero
# output (true hang) or the $RoundTimeoutMinutes hard cap. A productive
# long round that keeps streaming output is never killed by wall clock
# alone. Pre-port audit (D-20261019-04 face 2): this copy still carried
# the v1 25-min wall-clock guillotine; legitimate rounds were tree-killed
# mid-flight on 10-08 (evidence: state/heartbeat.txt "round timeout
# killed (age over 25min)" rows).
# Ported 2026-09-24 from the proven BigLife/BigMoney pattern (Tools/iteration_loop.ps1).
#
# ENCODING RULE: this file must stay PURE ASCII. powershell.exe 5.1 decodes
# BOM-less .ps1 as ANSI/GBK. All Chinese content lives in
# Tools\iteration_prompt.txt (UTF-8, read at runtime with explicit -Encoding).
#
# Self-heal recipe (run from an agent round when Get-ScheduledTask
# BigCompute-OSLoop is missing - path-agnostic, works on any machine):
#   powershell -NoProfile -ExecutionPolicy Bypass -File Tools/register_loop_task.ps1
param(
    [string]$Project = (Split-Path -Parent $PSScriptRoot),
    [int]$LockMaxAgeMinutes = 90,
    [int]$RoundTimeoutMinutes = 240,
    [int]$RoundSilenceMinutes = 25
)
$ErrorActionPreference = 'Continue'
Set-Location $Project
$logDir = Join-Path $Project 'logs\os-loop'
New-Item -ItemType Directory -Force $logDir | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$runLog = Join-Path $logDir "run_$stamp.log"
$roundOut = Join-Path $logDir "round_$stamp.out"
$roundErr = Join-Path $logDir "round_$stamp.err"
$heart = Join-Path $Project 'state\heartbeat.txt'

function Log([string]$m) {
    $line = "$(Get-Date -Format 'HH:mm:ss') $m"
    Write-Output $line
    Add-Content -Path $runLog -Value $line -Encoding UTF8
}
function Beat([string]$m) {
    # T131 sanctioned direct write (exemption, enumerated 2026-10-27): the
    # engine's own os-loop line is written here because it must survive even
    # when python/round_append is unavailable (round timeout, kill path).
    # PS Add-Content always terminates the value with a newline and appends
    # UTF-8 bytes, so tail integrity (BC-P-45) holds. Everything else --
    # round ledger, 日清, 刻痕 -- goes through Tools/round_append.py.
    New-Item -ItemType Directory -Force (Split-Path -Parent $heart) | Out-Null
    Add-Content -Path $heart -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') os-loop: $m" -Encoding UTF8
}

# v2 kill path (D-20261019-04): recursive tree kill - taskkill /T walks the
# whole descendant chain (v3 hardening, tech T3 2026-09-28; the old
# one-level CIM kill could leak grandchildren, e.g. node->python VRAM
# holders, past a timeout) + belt-and-braces recursive CIM sweep, then a
# post-kill survivor re-scan: any survivor logs a ZOMBIE_SURVIVOR row for
# the audit lane (child-clear verification, never silent).
function Stop-RoundTreeChecked([int]$rootPid, [string]$why) {
    $null = & taskkill.exe /PID $rootPid /T /F 2>&1
    $frontier = @($rootPid)
    $guard = 0
    $killed = @()
    while ($frontier.Count -gt 0 -and $guard -lt 20) {
        $next = @()
        foreach ($pp in $frontier) {
            $kids = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$pp" -ErrorAction SilentlyContinue)
            foreach ($k in $kids) {
                $next += [int]$k.ProcessId
                $killed += [int]$k.ProcessId
            }
        }
        foreach ($pp in $next) { Stop-Process -Id $pp -Force -ErrorAction SilentlyContinue }
        $frontier = $next
        $guard++
    }
    Stop-Process -Id $rootPid -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    $survivors = 0
    foreach ($procId in $killed) {
        if (Get-Process -Id $procId -ErrorAction SilentlyContinue) {
            $survivors++
            Log "ZOMBIE_SURVIVOR pid=$procId survived tree-kill ($why) -- audit lane face"
        }
    }
    if ($survivors -eq 0) { Log "kill tree self-check ($why): 0 survivors (child-clear verified)" }
}

# ---- single-instance guard v2 (group standard D-20260925-03:
# lock holds PID, liveness probe before takeover, CreateNew atomic grab) ----
$lock = Join-Path $logDir 'round.lock'
$lockProcPattern = 'powershell|pwsh|codely|node|python'
function Test-RoundPidAlive([int]$procId) {
    $p = Get-Process -Id $procId -ErrorAction SilentlyContinue
    if (-not $p) { return $false }
    return ($p.ProcessName -match $lockProcPattern)
}
if (Test-Path $lock) {
    $raw = ''
    try { $raw = (Get-Content -Raw $lock -ErrorAction Stop).Trim() } catch {}
    $oldPid = 0
    if ([int]::TryParse($raw, [ref]$oldPid) -and $oldPid -gt 0) {
        if (Test-RoundPidAlive $oldPid) {
            Log "skip: previous round still running (pid=$oldPid alive)"; Beat 'skip (round in flight)'; exit 0
        }
        Log "stale lock pid=$oldPid not alive - taking over"
    } else {
        # legacy timestamp-format lock (pre-v2): age fallback only for the transition
        $age = ((Get-Date) - (Get-Item $lock).LastWriteTime).TotalMinutes
        if ($age -lt $LockMaxAgeMinutes) { Log "skip: legacy lock fresh (age=$([int]$age)min)"; Beat 'skip (round in flight)'; exit 0 }
        Log "legacy lock expired (age=$([int]$age)min) - taking over"
    }
    Remove-Item -Path $lock -Force -ErrorAction SilentlyContinue
}
try {
    $fs = New-Object System.IO.FileStream($lock, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
    $sw = New-Object System.IO.StreamWriter($fs)
    $sw.Write($PID.ToString())
    $sw.Dispose()
} catch {
    Log "skip: lock atomically grabbed by a concurrent launcher (CreateNew lost)"; Beat 'skip (round in flight)'; exit 0
}

try {
    Log "BigCompute os-loop round start $stamp"
    # sec 9.6 idle hard-trigger self-check step (O-20261007-2315, BC-P-54):
    # reads machine verdict + pool/queue counts, appends the idle_rounds/
    # agenda_starved dual fields to state\heartbeat.txt via round_append.
    # Runs BEFORE the headless round spawns so the agent consumes the fields.
    try {
        & python (Join-Path $Project 'Tools\idle_selfcheck.py') check 2>&1 |
            ForEach-Object { Log "idle_selfcheck: $_" }
    } catch { Log "idle_selfcheck failed (non-fatal): $_" }
    $codelyPath = (Get-Command codely -ErrorAction SilentlyContinue).Source
    if (-not $codelyPath) { Log 'FATAL: codely not on PATH for this context'; Beat 'error codely missing'; exit 2 }
    $promptFile = Join-Path $Project 'Tools\iteration_prompt.txt'
    if (-not (Test-Path $promptFile)) { Log 'FATAL: iteration_prompt.txt missing'; Beat 'error prompt file missing'; exit 2 }
    $prompt = (Get-Content -Raw -Encoding UTF8 $promptFile).Trim()
    if ($prompt.Length -lt 50) { Log 'FATAL: iteration_prompt.txt too short'; Beat 'error prompt file empty'; exit 2 }
    if ($prompt.Contains('"')) { $prompt = $prompt.Replace('"', "'") }
    $argLine = '-y -p "' + $prompt + '"'
    Log "spawning headless round (hard-cap ${RoundTimeoutMinutes}min, silence-kill ${RoundSilenceMinutes}min, prompt_chars=$($prompt.Length), out=$roundOut)"
    # T0 CLOUD-OFF guard (CEO 2026-10-20): automation is local-only; never spawn the cloud CLI.
    $__co = $PSScriptRoot
    while ($__co -and -not (Test-Path (Join-Path $__co 'cph4\fleet\CLOUD-OFF-LOCAL-ONLY-v1.md'))) { $__co = Split-Path -Parent $__co }
    if ($__co) {
        $__cl = Join-Path $__co 'media\logs\cloud-off-suppressed.log'
        $__cd = Split-Path -Parent $__cl
        if (-not (Test-Path $__cd)) { New-Item -ItemType Directory -Force -Path $__cd | Out-Null }
        Add-Content -Path $__cl -Value ((Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' | ' + [System.IO.Path]::GetFileName($PSCommandPath) + ' | suppressed cloud round (T0)') -Encoding UTF8
        exit 0
    }
    $p = Start-Process -FilePath $codelyPath -ArgumentList $argLine -WorkingDirectory $Project -PassThru -NoNewWindow -RedirectStandardOutput $roundOut -RedirectStandardError $roundErr
    $null = $p.Handle
    # hold the worker pid in the lock: if this launcher is hard-killed, an
    # orphaned worker is still detected as alive by the next tick (D-20260925-03)
    try { Set-Content -Path $lock -Value $p.Id -Encoding ASCII } catch {}
    # v2 liveness budget (D-20261019-04): poll in 30s slices. "Activity" =
    # the round's stdout/stderr redirect files gaining bytes. Kill ONLY on
    # evidence of a hang (silent for $RoundSilenceMinutes) or the hard cap.
    # A productive long round streams tool output continuously and is never
    # killed by wall clock alone (the pre-port v1 25-min guillotine caused
    # the fleet dead-window epidemic; see header note).
    $startAt = Get-Date
    $lastMove = Get-Date
    $lastSize = -1L
    while (-not $p.WaitForExit(30000)) {
        $size = 0L
        foreach ($f in @($roundOut, $roundErr)) {
            try { $fi = Get-Item $f -ErrorAction SilentlyContinue; if ($fi) { $size += $fi.Length } } catch { }
        }
        if ($size -ne $lastSize) { $lastSize = $size; $lastMove = Get-Date }
        $silentMin = ((Get-Date) - $lastMove).TotalMinutes
        $totalMin = ((Get-Date) - $startAt).TotalMinutes
        if ($silentMin -ge $RoundSilenceMinutes) {
            Log "ROUND SILENCE-KILL: no output for $([int]$silentMin)min (total $([int]$totalMin)min) - killing headless process tree"
            try { Stop-RoundTreeChecked $p.Id 'silence-kill' } catch { Log "kill failed: $_" }
            Beat "round silence-killed (no output $([int]$silentMin)min, total $([int]$totalMin)min)"
            exit 3
        }
        if ($totalMin -ge $RoundTimeoutMinutes) {
            Log "ROUND HARD-CAP after ${RoundTimeoutMinutes}min - killing headless process tree"
            try { Stop-RoundTreeChecked $p.Id 'hard-cap' } catch { Log "kill failed: $_" }
            Beat "round hard-cap killed (age over ${RoundTimeoutMinutes}min)"
            exit 3
        }
    }
    $p.Refresh()
    Log "headless round finished exit=$($p.ExitCode) (ran $([int]((Get-Date) - $startAt).TotalMinutes)min)"
    Beat "round done exit=$($p.ExitCode)"
    exit $p.ExitCode
}
finally {
    Remove-Item $lock -Force -ErrorAction SilentlyContinue
}
