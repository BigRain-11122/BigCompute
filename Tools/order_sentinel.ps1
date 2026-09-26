# BigCompute order/decision sentinel - self-built P0 ack<=30min wake path
# (O-20260926-2253-HQ-C face 4; silent law via InvisibleRunner.vbs wrapper).
# ENCODING RULE: pure ASCII (see iteration_loop.ps1).
# Every 15min: hash group docs/orders.md + docs/decisions.md; on change and
# no round in flight, fire one headless OSLoop round and refresh snapshot.
$ErrorActionPreference = 'Continue'
$Project = Split-Path -Parent $PSScriptRoot
$GroupDocs = Join-Path (Split-Path -Parent (Split-Path -Parent $Project)) 'docs'
$files = @(
    (Join-Path $GroupDocs 'orders.md'),
    (Join-Path $GroupDocs 'decisions.md')
)
$logDir = Join-Path $Project 'logs\os-loop'
New-Item -ItemType Directory -Force $logDir | Out-Null
$sentLog = Join-Path $logDir 'sentinel.log'
$snapshot = Join-Path $Project 'state\sentinel.snapshot'
New-Item -ItemType Directory -Force (Split-Path -Parent $snapshot) | Out-Null
function SLog([string]$m) {
    Add-Content -Path $sentLog -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $m" -Encoding UTF8
}
$hashes = @()
foreach ($f in $files) {
    if (Test-Path $f) { $hashes += (Get-FileHash -Path $f -Algorithm SHA256).Hash }
    else { $hashes += 'MISSING:' + $f }
}
$current = ($hashes -join '|')
$prev = $null
if (Test-Path $snapshot) { $prev = (Get-Content -Raw $snapshot -ErrorAction SilentlyContinue) }
if ($null -eq $prev -or $prev.Trim() -eq '') {
    Set-Content -Path $snapshot -Value $current -Encoding ASCII
    SLog 'baseline snapshot created (no wake)'
    exit 0
}
if ($prev.Trim() -eq $current) { exit 0 }
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
Start-Process -FilePath 'wscript.exe' -ArgumentList ('//B //nologo "' + $vbs + '" powershell.exe -NoProfile -ExecutionPolicy Bypass -File "' + $launcher + '"') -WorkingDirectory $Project -WindowStyle Hidden
Set-Content -Path $snapshot -Value $current -Encoding ASCII
SLog 'group orders/decisions changed - wake fired (headless round launched)'
exit 0
