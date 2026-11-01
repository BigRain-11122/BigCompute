# lane-task-guard.ps1 -- lane task self-healing guard (D-20261101-16 (1) deliverable)
#
# WHY: on 2026-11-01 three lane tasks were read back as Ready at 06:0x and then
# found Disabled at 12:01 right after they ran (rc=0). The disabling party left
# no trace (no machine-state.disabled.json, no TaskScheduler/Operational event),
# so attribution is not recoverable. This guard does not wait for attribution:
# when the fleet flag says "resumed", a lane task of this node that is Disabled
# and NOT on the disable whitelist gets re-enabled, and every revive is appended
# to cph4/fleet/state/lane-task-revive.jsonl (before/after/flag snapshot).
#
# The second half of D-16 (2) is the trace-first disable path: -Mode disable
# writes machine-state.disabled.json BEFORE touching the task, and refuses to
# disable anything without a reason. No untraced disable.
#
# Pure ASCII (PS5.1 -File safe). No popups: run through InvisibleRunner.vbs.
#
# Usage:
#   lane-task-guard.ps1 -SelfTest
#   lane-task-guard.ps1                      # report only (dry run)
#   lane-task-guard.ps1 -Apply                # actually re-enable
#   lane-task-guard.ps1 -Mode disable -TaskName BigCompute-GPU-IdleWatch `
#       -Reason "..." -ReopenWhen "..."
#
# Exit codes: 0 ok / 2 cannot write revive log / 3 some enable failed / 4 refused

param(
    [ValidateSet('check', 'disable')]
    [string]$Mode = 'check',
    [string[]]$TaskName = @(),
    [string]$TaskPattern = 'BigCompute-*',
    [string]$SelfTaskName = 'BigCompute-LaneTaskGuard',
    [string]$Reason = '',
    [string]$ReopenWhen = '',
    [string]$By = 'BigCompute-LaneTaskGuard',
    [string]$GroupRoot = '',
    [string[]]$OwnerFamilies = @('Big', 'HQ', 'Flux', 'Bm', 'MiniGame', 'OpenClaw'),
    [switch]$Apply,
    [switch]$SelfTest
)

$ErrorActionPreference = 'Stop'

$Project = Split-Path -Parent $PSScriptRoot        # ...\compute\BigCompute
if (-not $GroupRoot) { $GroupRoot = Split-Path -Parent (Split-Path -Parent $Project) }

$FlagPath        = Join-Path $GroupRoot '.codely-cli\machine-state.flag'
$DisabledSidecar = Join-Path $GroupRoot '.codely-cli\machine-state.disabled.json'
$ReviveLog       = Join-Path $GroupRoot 'cph4\fleet\state\lane-task-revive.jsonl'

# ---------------------------------------------------------------- pure helpers

function Get-FlagMode {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return 'absent' }
    $first = (Get-Content -LiteralPath $Path -TotalCount 1)
    if (-not $first) { return 'absent' }
    $token = ($first -split '\s+')[0]
    if (-not $token) { return 'absent' }
    return $token.ToLowerInvariant()
}

function Get-Whitelist {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return @() }
    try {
        $raw = Get-Content -LiteralPath $Path -Raw
        if (-not $raw -or -not $raw.Trim()) { return @() }
        $obj = $raw | ConvertFrom-Json
    } catch {
        return @()
    }
    $names = New-Object System.Collections.Generic.List[string]
    if ($obj -is [System.Array]) {
        foreach ($e in $obj) {
            if ($e -is [string]) { $names.Add($e) }
            elseif ($e -and $e.task) { $names.Add([string]$e.task) }
        }
    } else {
        foreach ($prop in @('tasks', 'disabled', 'items')) {
            $val = $obj.$prop
            if (-not $val) { continue }
            if ($val -is [System.Array]) {
                foreach ($e in $val) {
                    if ($e -is [string]) { $names.Add($e) }
                    elseif ($e -and $e.task) { $names.Add([string]$e.task) }
                }
            } else {
                foreach ($p in $val.PSObject.Properties) { $names.Add([string]$p.Name) }
            }
        }
    }
    return @($names | Where-Object { $_ })
}

function Test-Whitelisted {
    param([string]$Name, [string[]]$Whitelist)
    foreach ($w in $Whitelist) { if ($w -ieq $Name) { return $true } }
    return $false
}

function Select-Candidate {
    param([string[]]$Names, [string]$Self)
    $out = New-Object System.Collections.Generic.List[string]
    foreach ($n in $Names) {
        if (-not $n) { continue }
        if ($Self -and $n -ieq $Self) { continue }   # never revive/disable the guard itself
        $out.Add($n)
    }
    return @($out)
}

function New-ReviveRecord {
    param([string]$Task, [string]$Before, [string]$After, [string]$Flag, [string]$Who, [bool]$Applied)
    return [ordered]@{
        ts     = (Get-Date).ToString('yyyy-MM-ddTHH:mm:sszzz')
        task   = $Task
        before = $Before
        after  = $After
        flag   = $Flag
        by     = $Who
        apply  = $Applied
    }
}

function Test-CanDisable {
    param([string]$ReasonText, [string]$TaskNameText)
    if (-not $TaskNameText) { return 'refuse:no-task' }
    if (-not $ReasonText -or -not $ReasonText.Trim()) { return 'refuse:no-reason' }
    return 'ok'
}

function Get-TaskPrefix {
    param([string]$Name)
    if (-not $Name) { return '?' }
    $parts = $Name -split '-'
    if ($parts.Count -ge 1 -and $parts[0]) { return $parts[0] }
    return '?'
}

function Test-OwnerFamily {
    param([string]$Name, [string[]]$Families)
    if (-not $Name) { return $false }
    foreach ($f in @($Families)) {
        if (-not $f) { continue }
        if ($Name.Length -ge $f.Length -and $Name.Substring(0, $f.Length) -ieq $f) { return $true }
    }
    return $false
}

# D-20261101-20: an "ALL-READY / no violations" verdict is only meaningful
# together with the domain it was computed over. A guard whose pattern is
# "BigCompute-*" saying ALL-READY says nothing about Bigmoney-* or HQ-Patrol-*.
# So the domain is always printed, and disabled tasks OUTSIDE the domain are
# counted (read-only, never acted on: other owners' tasks are not ours to touch).
# Out-of-scope counting is narrowed to owner families (Windows' own built-in
# disabled tasks are noise); the raw out-of-domain total is printed too, so the
# narrowing itself stays checkable.
function Get-ScanDomain {
    param([object[]]$AllTasks, [string]$Pattern, [string]$Self, [string[]]$Explicit, [string[]]$OwnerFamilies)

    $inList = New-Object System.Collections.Generic.List[string]
    $inDisabled = New-Object System.Collections.Generic.List[string]
    $outOwner = New-Object System.Collections.Generic.List[string]
    $outOther = New-Object System.Collections.Generic.List[string]

    $explicitNames = @($Explicit | Where-Object { $_ })
    $mode = if ($explicitNames.Count -gt 0) { 'explicit' } else { 'pattern:' + $Pattern }

    foreach ($t in @($AllTasks)) {
        if (-not $t) { continue }
        $n = [string]$t.TaskName
        if (-not $n) { continue }
        if ($Self -and $n -ieq $Self) { continue }
        $isDisabled = ([string]$t.State -eq 'Disabled')

        $inScope = $false
        if ($explicitNames.Count -gt 0) {
            foreach ($e in $explicitNames) { if ($e -ieq $n) { $inScope = $true; break } }
        } elseif ($Pattern) {
            if ($n -like $Pattern) { $inScope = $true }
        }

        if ($inScope) {
            $inList.Add($n)
            if ($isDisabled) { $inDisabled.Add($n) }
        } elseif ($isDisabled) {
            if (Test-OwnerFamily -Name $n -Families $OwnerFamilies) { $outOwner.Add($n) } else { $outOther.Add($n) }
        }
    }

    $counts = @{}
    foreach ($n in $outOwner) {
        $p = Get-TaskPrefix -Name $n
        if ($counts.ContainsKey($p)) { $counts[$p] = $counts[$p] + 1 } else { $counts[$p] = 1 }
    }

    return [ordered]@{
        mode                  = $mode
        names                 = @($inList)
        disabled_in_scope     = @($inDisabled)
        out_of_scope_disabled = @($outOwner)
        out_of_scope_other    = @($outOther)
        out_prefix_counts     = $counts
    }
}

function Format-PrefixCounts {
    param($Counts)
    if (-not $Counts) { return '-' }
    $parts = New-Object System.Collections.Generic.List[string]
    foreach ($k in ($Counts.Keys | Sort-Object)) { $parts.Add(('{0}={1}' -f $k, $Counts[$k])) }
    if ($parts.Count -eq 0) { return '-' }
    return ($parts -join ' ')
}

function Add-JsonLine {
    param([string]$Path, $Record)
    $dir = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $line = ($Record | ConvertTo-Json -Compress -Depth 5)
    Add-Content -LiteralPath $Path -Value $line -Encoding UTF8
}

# ------------------------------------------------------------------- self-test

function Invoke-SelfTest {
    $tmp = Join-Path $env:TEMP ('ltg-selftest-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $tmp -Force | Out-Null
    $checks = New-Object System.Collections.Generic.List[string]
    # nested helpers must reach these through script scope (PS5.1 function scope)
    $script:checks = $checks
    $script:fail = 0
    function Check([string]$name, [bool]$ok) {
        if ($ok) { $script:checks.Add("PASS $name") } else { $script:checks.Add("FAIL $name"); $script:fail++ }
    }

    $f1 = Join-Path $tmp 'flag1'; Set-Content -LiteralPath $f1 -Value 'resumed 2026-10-31T17:47:19+08:00' -Encoding ASCII
    Check 'S1 flag resumed parsed' ((Get-FlagMode $f1) -eq 'resumed')
    $f2 = Join-Path $tmp 'flag2'; Set-Content -LiteralPath $f2 -Value 'paused 2026-10-31T09:00:00+08:00' -Encoding ASCII
    Check 'S2 flag paused parsed' ((Get-FlagMode $f2) -eq 'paused')
    Check 'S3 missing flag = absent' ((Get-FlagMode (Join-Path $tmp 'nope')) -eq 'absent')

    $w1 = Join-Path $tmp 'w1.json'; Set-Content -LiteralPath $w1 -Value '["A-Task","B-Task"]' -Encoding UTF8
    Check 'S4 whitelist array' ((Get-Whitelist $w1) -contains 'B-Task')
    $w2 = Join-Path $tmp 'w2.json'; Set-Content -LiteralPath $w2 -Value '{"tasks":[{"task":"C-Task","reason":"ceo"}]}' -Encoding UTF8
    Check 'S5 whitelist object shape' ((Get-Whitelist $w2) -contains 'C-Task')
    Check 'S6 missing whitelist = empty' ((Get-Whitelist (Join-Path $tmp 'nope.json')).Count -eq 0)

    $c = Select-Candidate -Names @('BigCompute-A', 'BigCompute-LaneTaskGuard', 'BigCompute-B') -Self 'BigCompute-LaneTaskGuard'
    Check 'S7 self excluded from candidates' (($c -join ',') -eq 'BigCompute-A,BigCompute-B')

    $r = New-ReviveRecord -Task 'T' -Before 'Disabled' -After 'Ready' -Flag 'resumed' -Who 'guard' -Applied $true
    Check 'S8 record has 7 fields' ($r.Keys.Count -eq 7)
    $j = ($r | ConvertTo-Json -Compress)
    Check 'S9 record is single line' (-not ($j -match "`r|`n"))

    Check 'S10 disable refused without reason' ((Test-CanDisable -ReasonText '' -TaskNameText 'X') -eq 'refuse:no-reason')
    Check 'S11 disable refused without task' ((Test-CanDisable -ReasonText 'r' -TaskNameText '') -eq 'refuse:no-task')
    Check 'S12 disable allowed with both' ((Test-CanDisable -ReasonText 'r' -TaskNameText 'X') -eq 'ok')

    $logp = Join-Path $tmp 'sub\lane.jsonl'
    Add-JsonLine -Path $logp -Record $r
    Check 'S13 jsonl append creates dir + one line' ((Get-Content -LiteralPath $logp).Count -eq 1)
    Check 'S14 jsonl parses back' ((Get-Content -LiteralPath $logp -Raw | ConvertFrom-Json).task -eq 'T')

    $c2 = Select-Candidate -Names @('X') -Self ''
    Check 'S15 empty self keeps all' (($c2 -join ',') -eq 'X')

    Check 'S16 whitelist match is case-insensitive' (Test-Whitelisted -Name 'bigcompute-gpu-idlewatch' -Whitelist @('BigCompute-GPU-IdleWatch'))

    # D-20261101-20 domain-reporting checks
    $tasks = @(
        [pscustomobject]@{ TaskName = 'BigCompute-GPU-IdleWatch'; State = 'Ready' },
        [pscustomobject]@{ TaskName = 'BigCompute-OrderSentinel'; State = 'Disabled' },
        [pscustomobject]@{ TaskName = 'BigCompute-LaneTaskGuard'; State = 'Ready' },
        [pscustomobject]@{ TaskName = 'Bigmoney-IterationLoop'; State = 'Disabled' },
        [pscustomobject]@{ TaskName = 'HQ-Patrol-BigMoney'; State = 'Disabled' },
        [pscustomobject]@{ TaskName = 'HQ-Patrol-Group'; State = 'Ready' },
        [pscustomobject]@{ TaskName = 'Microsoft-Windows-Foo'; State = 'Disabled' }
    )
    $d1 = Get-ScanDomain -AllTasks $tasks -Pattern 'BigCompute-*' -Self 'BigCompute-LaneTaskGuard' -Explicit @() -OwnerFamilies $OwnerFamilies
    Check 'S17 domain lists in-scope names and excludes the guard itself' (($d1.names -join ',') -eq 'BigCompute-GPU-IdleWatch,BigCompute-OrderSentinel')
    Check 'S18 domain mode names the pattern' ($d1.mode -eq 'pattern:BigCompute-*')
    Check 'S19 out-of-scope disabled counted but not adopted' (($d1.out_of_scope_disabled.Count -eq 2) -and (($d1.out_of_scope_disabled -join ',') -eq 'Bigmoney-IterationLoop,HQ-Patrol-BigMoney'))
    Check 'S20 out-of-scope prefix summary groups by owner' (((Format-PrefixCounts -Counts $d1.out_prefix_counts)) -eq 'Bigmoney=1 HQ=1')
    Check 'S21 non-owner family (Windows builtin) counted separately' ($d1.out_of_scope_other.Count -eq 1)
    $d2 = Get-ScanDomain -AllTasks $tasks -Pattern 'BigCompute-*' -Self 'BigCompute-LaneTaskGuard' -Explicit @('Bigmoney-IterationLoop') -OwnerFamilies $OwnerFamilies
    Check 'S22 explicit mode scans exactly the named task' (($d2.mode -eq 'explicit') -and (($d2.names -join ',') -eq 'Bigmoney-IterationLoop'))
    $d3 = Get-ScanDomain -AllTasks $tasks -Pattern 'BigCompute-*' -Self '' -Explicit @() -OwnerFamilies $OwnerFamilies
    Check 'S23 empty self keeps every task' ($d3.names.Count -eq 3)
    Check 'S24 owner family match is case-insensitive' ((Test-OwnerFamily -Name 'bigmoney-loop' -Families $OwnerFamilies) -and -not (Test-OwnerFamily -Name 'Microsoft-Windows-X' -Families $OwnerFamilies))

    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue

    $fail = $script:fail
    foreach ($l in $checks) { Write-Output $l }
    if ($fail -eq 0) { Write-Output "SELFTEST $($checks.Count)/$($checks.Count) PASS"; exit 0 }
    Write-Output "SELFTEST FAIL count=$fail"
    exit 1
}

if ($SelfTest) { Invoke-SelfTest }

# --------------------------------------------------------------- disable mode

if ($Mode -eq 'disable') {
    $one = @($TaskName | Where-Object { $_ })
    if ($one.Count -ne 1) {
        $verdict = Test-CanDisable -ReasonText $Reason -TaskNameText ''
        Write-Output "GUARD verdict=$verdict (disable needs exactly one -TaskName)"
        exit 4
    }
    $verdict = Test-CanDisable -ReasonText $Reason -TaskNameText $one[0]
    if ($verdict -ne 'ok') {
        Write-Output "GUARD verdict=$verdict task=$($one[0])"
        exit 4
    }
    $flagMode = Get-FlagMode -Path $FlagPath
    $rec = [ordered]@{
        ts          = (Get-Date).ToString('yyyy-MM-ddTHH:mm:sszzz')
        task        = $one[0]
        by          = $By
        reason      = $Reason
        reopen_when = $ReopenWhen
        flag        = $flagMode
    }
    # trace BEFORE the state change (D-16 (2))
    $existing = @()
    if (Test-Path -LiteralPath $DisabledSidecar) {
        try { $existing = @(Get-Content -LiteralPath $DisabledSidecar -Raw | ConvertFrom-Json) } catch { $existing = @() }
    }
    $tmpF = $DisabledSidecar + '.tmp'
    $dir = Split-Path -Parent $DisabledSidecar
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    (@($existing) + @($rec)) | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $tmpF -Encoding UTF8
    Move-Item -LiteralPath $tmpF -Destination $DisabledSidecar -Force
    try {
        Disable-ScheduledTask -TaskName $one[0] -ErrorAction Stop | Out-Null
    } catch {
        Write-Output "GUARD verdict=DISABLE-FAILED task=$($one[0]) err=$($_.Exception.Message) (trace already written)"
        exit 3
    }
    Write-Output "GUARD verdict=DISABLED-TRACED task=$($one[0]) reason=$Reason reopen=$ReopenWhen trace=$DisabledSidecar"
    exit 0
}

# ------------------------------------------------------------------ check mode

$flagMode = Get-FlagMode -Path $FlagPath
if ($flagMode -ne 'resumed') {
    Write-Output "GUARD verdict=NOOP reason=flag-not-resumed flag=$flagMode"
    exit 0
}

$whitelist = Get-Whitelist -Path $DisabledSidecar

$allTasks = @(Get-ScheduledTask -ErrorAction SilentlyContinue | Select-Object TaskName, State)
$explicit = @($TaskName | Where-Object { $_ })
$domain = Get-ScanDomain -AllTasks $allTasks -Pattern $TaskPattern -Self $SelfTaskName -Explicit $explicit -OwnerFamilies $OwnerFamilies
$names = @($domain.names)

$revived = New-Object System.Collections.Generic.List[string]
$skippedWl = New-Object System.Collections.Generic.List[string]
$errors = New-Object System.Collections.Generic.List[string]
$disabledCount = 0

foreach ($n in $names) {
    $t = Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue
    if (-not $t) { continue }
    if ($t.State -ne 'Disabled') { continue }
    $disabledCount++
    if (Test-Whitelisted -Name $n -Whitelist $whitelist) { $skippedWl.Add($n); continue }
    $after = 'unchanged(dry-run)'
    if ($Apply) {
        try {
            Enable-ScheduledTask -TaskName $n -ErrorAction Stop | Out-Null
            $after = (Get-ScheduledTask -TaskName $n).State.ToString()
        } catch {
            $after = 'ERROR:' + $_.Exception.Message
            $errors.Add($n)
        }
    }
    $rec = New-ReviveRecord -Task $n -Before 'Disabled' -After $after -Flag $flagMode -Who $By -Applied ([bool]$Apply)
    try { Add-JsonLine -Path $ReviveLog -Record $rec }
    catch { Write-Output "GUARD verdict=LOG-WRITE-FAILED err=$($_.Exception.Message)"; exit 2 }
    $revived.Add($n)
}

$runMode = if ($Apply) { 'APPLY' } else { 'DRY-RUN' }
$verdict = if ($errors.Count -gt 0) { 'PARTIAL' } elseif ($revived.Count -gt 0) { 'REVIVED' } elseif ($disabledCount -eq 0) { 'ALL-READY' } else { 'WHITELISTED-ONLY' }
Write-Output ("GUARD verdict={0} mode={1} flag=resumed scanned={2} disabled={3} acted={4} whitelisted_skipped={5} errors={6} log={7} domain_mode={8}" -f `
    $verdict, $runMode, $names.Count, $disabledCount, $revived.Count, $skippedWl.Count, $errors.Count, $ReviveLog, $domain.mode)
# domain list is printed unconditionally: a readiness verdict without its
# domain cannot be checked, and "ALL-READY" over 6 tasks is not "fleet ready"
Write-Output ("  domain=" + (($names | Sort-Object) -join ','))
Write-Output ("  out_of_scope_disabled={0} acted=0 (owner families; read-only) prefixes={1}" -f `
    $domain.out_of_scope_disabled.Count, (Format-PrefixCounts -Counts $domain.out_prefix_counts))
Write-Output ("  out_of_scope_all_disabled={0} (incl. OS builtins; narrowing above is not a claim they are fine)" -f `
    ($domain.out_of_scope_disabled.Count + $domain.out_of_scope_other.Count))
if ($revived.Count -gt 0) { Write-Output ("  tasks=" + ($revived -join ',')) }
if ($skippedWl.Count -gt 0) { Write-Output ("  whitelisted=" + ($skippedWl -join ',')) }

if ($errors.Count -gt 0) { exit 3 }
exit 0