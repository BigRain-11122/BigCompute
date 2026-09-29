# City3D interior bake spike runner (BigCompute M25 / R-20260929-city3d-interior-bake-lane).
# Usage: powershell -File Tools/bake_spike_assets/run_bake.ps1 -Lightmapper ProgressiveCPU|ProgressiveGPU
# Gate: run only in a CLEAN window (no other Tuanjie editor instances / no foreign production load),
# otherwise J1 (duration) / J2 (peak) metrics are contaminated. J4 ledger entry follows per work order.
param(
    [ValidateSet('ProgressiveCPU', 'ProgressiveGPU')][string]$Lightmapper = 'ProgressiveCPU',
    [string]$ProjectDir = 'C:\Users\sjs20\Desktop\FluxGroup\.codely-cli\labbench\city3d-bake-spike\BakeSpike',
    [string]$OutDir = 'C:\Users\sjs20\Desktop\FluxGroup\compute\BigCompute\state',
    [int]$TimeoutMin = 45,
    [string]$EditorExe = 'C:\Program Files\Tuanjie\Hub\Editor\2022.3.62t15\Editor\Tuanjie.exe'
)
$ErrorActionPreference = 'Stop'

# Window gate: refuse to run while other editor instances are alive (clean-window law).
$otherEditors = @(Get-Process -Name 'Tuanjie' -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -ne '' })
$editorProcs = @(Get-Process -Name 'Tuanjie' -ErrorAction SilentlyContinue)
if ($editorProcs.Count -gt 0) {
    Write-Host ("BLOCKED: {0} Tuanjie editor process(es) running - not a clean window (J1/J2 contamination). Aborting." -f $editorProcs.Count)
    exit 3
}
if (-not (Test-Path (Join-Path $ProjectDir 'Assets'))) {
    throw "spike project missing: $ProjectDir (run: python Tools/city3d_bake_spike.py scaffold)"
}

$ts = Get-Date -Format 'yyyyMMdd-HHmmss'
$dir = Join-Path $OutDir "bake-spike-$ts"
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$env:BAKE_SPIKE_LIGHTMAPPER = $Lightmapper
$env:BAKE_SPIKE_OUT = Join-Path $dir 'metrics.json'
$samplerCsv = Join-Path $dir 'vram_samples.csv'

# J2 external sampler: nvidia-smi every 5s while bake runs.
$sampler = Start-Job -ScriptBlock {
    param($csv)
    $sw = [System.IO.StreamWriter]::new($csv, $false, [System.Text.Encoding]::UTF8)
    try {
        while ($true) {
            $v = (& nvidia-smi --query-gpu=memory.used,utilization.gpu --format=csv,noheader,nounits) -join ','
            if ($v) { $sw.WriteLine((Get-Date -Format 'o') + ',' + $v); $sw.Flush() }
            Start-Sleep -Seconds 5
        }
    } finally { $sw.Dispose() }
} -ArgumentList $samplerCsv

$log = Join-Path $dir 'editor.log'
Write-Host ("bake-spike start: lightmapper={0} dir={1}" -f $Lightmapper, $dir)
$p = Start-Process -FilePath $EditorExe -ArgumentList @(
    '-batchmode', '-projectPath', $ProjectDir,
    '-executeMethod', 'City3DBakeSpike.Entry.Run', '-logFile', $log
) -PassThru
if (-not ($p.WaitForExit($TimeoutMin * 60 * 1000))) {
    Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
    $exit = 124
} else { $exit = $p.ExitCode }

Stop-Job $sampler -ErrorAction SilentlyContinue | Out-Null
Remove-Job $sampler -Force -ErrorAction SilentlyContinue | Out-Null
Start-Sleep -Seconds 1

$peak = 0
if (Test-Path $samplerCsv) {
    foreach ($line in Get-Content $samplerCsv) {
        if ($line -match ',(\d+),\s*(\d+)\s*$') {
            $mem = [int]$Matches[1]
            if ($mem -gt $peak) { $peak = $mem }
        }
    }
}
$summary = [ordered]@{
    ts = $ts; lightmapper = $Lightmapper; exit_code = $exit
    bake_seconds = $null; vram_peak_mib = $peak
}
$metricsPath = Join-Path $dir 'metrics.json'
if (Test-Path $metricsPath) {
    try { $m = Get-Content $metricsPath -Raw | ConvertFrom-Json; $summary.bake_seconds = $m.bake_seconds } catch {}
}
$summary | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $dir 'summary.json')
Write-Host ("bake-spike done exit={0} vram_peak={1}MiB dir={2}" -f $exit, $peak, $dir)
exit $exit
