# Cross-check the fuxi batch: test-script letters, artifact manifest, files on disk and hashes quoted
# in the docs. ASCII only on purpose (must parse under pwsh 7 and Windows PowerShell 5.1).
#
# Two classes of output:
#   errors        contradictions that would make a test round wrong (stale hash, wrong path, gone file)
#   informational historical artifact directories from earlier rounds (not part of the current batch)
#
# Usage:
#   pwsh -NoProfile -File C:\Users\Jason Wu\dsh\verify-consistency.ps1
#   pwsh -NoProfile -File C:\Users\Jason Wu\dsh\verify-consistency.ps1 -ListUntracked
#
# Exit codes: 0 consistent, 1 errors found, 2 a required input is missing.

param(
    [switch]$ListUntracked
)

$ErrorActionPreference = 'Stop'
$root = 'C:\Users\Jason Wu\dsh'
$errors = @()
$checks = 0

function Get-Manifest {
    $path = Join-Path $root 'artifacts-manifest.json'
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
}

$manifest = Get-Manifest
if ($null -eq $manifest) {
    Write-Output 'MISSING_MANIFEST artifacts-manifest.json'
    exit 2
}

$byLetter = @{}
foreach ($entry in $manifest) { $byLetter[$entry.Letter] = $entry }

# 1) the test script maps letters to paths; those must agree with the manifest both ways
$scriptPath = Join-Path $root 'test-batch.ps1'
if (-not (Test-Path -LiteralPath $scriptPath)) {
    Write-Output 'MISSING_SCRIPT test-batch.ps1'
    exit 2
}
$script = Get-Content -LiteralPath $scriptPath -Raw
$scriptMap = [ordered]@{}
foreach ($m in [regex]::Matches($script, "(?m)^\s+([A-Z][0-9]?)\s*=\s*'([^']+boot\.img)'")) {
    $scriptMap[$m.Groups[1].Value] = $m.Groups[2].Value
}
foreach ($letter in $scriptMap.Keys) {
    $checks++
    if (-not $byLetter.ContainsKey($letter)) {
        $errors += "LETTER_NOT_IN_MANIFEST $letter"
        continue
    }
    if ($scriptMap[$letter] -ne $byLetter[$letter].Path) {
        $errors += ("LETTER_PATH_MISMATCH {0}: script={1} manifest={2}" -f $letter, $scriptMap[$letter], $byLetter[$letter].Path)
    }
}
foreach ($letter in @($byLetter.Keys)) {
    $checks++
    if (-not $scriptMap.Contains($letter)) { $errors += "MANIFEST_LETTER_MISSING_FROM_SCRIPT $letter" }
}

# 2) every manifest entry must exist with the recorded size and hash
foreach ($entry in $manifest) {
    $checks++
    if (-not (Test-Path -LiteralPath $entry.Path)) {
        $errors += ("GONE {0} {1}" -f $entry.Letter, $entry.Path)
        continue
    }
    $item = Get-Item -LiteralPath $entry.Path
    if ($item.Length -ne [int64]$entry.Bytes) {
        $errors += ("SIZE {0}: manifest={1} disk={2}" -f $entry.Letter, $entry.Bytes, $item.Length)
        continue
    }
    $hash = (Get-FileHash -LiteralPath $entry.Path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($hash -ne $entry.Sha256) {
        $errors += ("HASH {0}: manifest={1} disk={2}" -f $entry.Letter, $entry.Sha256.Substring(0, 12), $hash.Substring(0, 12))
    }
}

# 3) artifact directories: untracked ones are historical (informational), tracked ones must exist
$dirs = @(Get-ChildItem -LiteralPath $root -Directory -Filter 'artifacts-*' -ErrorAction SilentlyContinue)
$known = @{}
foreach ($entry in $manifest) {
    $parent = Split-Path -Leaf (Split-Path -Parent $entry.Path)
    $known[$parent] = $true
}
$untracked = @()
foreach ($dir in $dirs) {
    $checks++
    if (-not $known.ContainsKey($dir.Name)) { $untracked += $dir.Name }
}
foreach ($name in @($known.Keys)) {
    $checks++
    if (-not ($dirs | Where-Object { $_.Name -eq $name })) { $errors += ("MANIFEST_DIR_GONE {0}" -f $name) }
}

# 4) docs that quote a letter with a hex prefix must quote the manifest's hash
$docs = @('START-HERE.md', 'TEST-RUNBOOK.md', 'HANDOVER.md', 'ARTIFACTS-MANIFEST.md', 'FINAL-STATUS.md')
foreach ($doc in $docs) {
    $path = Join-Path $root $doc
    if (-not (Test-Path -LiteralPath $path)) { continue }
    $text = Get-Content -LiteralPath $path -Raw
    foreach ($m in [regex]::Matches($text, '\|\s*`?\*{0,2}([A-Z][0-9]?)\*{0,2}`?\s*\|\s*`?([0-9a-f]{8,12})`?')) {
        $letter = $m.Groups[1].Value
        $prefix = $m.Groups[2].Value
        $checks++
        if (-not $byLetter.ContainsKey($letter)) {
            $errors += ("DOC_LETTER_UNKNOWN {0} in {1}" -f $letter, $doc)
            continue
        }
        if (-not $byLetter[$letter].Sha256.StartsWith($prefix)) {
            $errors += ("DOC_HASH_STALE {0} in {1}: doc={2} disk={3}" -f $letter, $doc, $prefix, $byLetter[$letter].Sha256.Substring(0, 12))
        }
    }
}

Write-Output ("consistency: {0} check(s) across {1} tracked image(s)" -f $checks, $manifest.Count)
Write-Output ("informational: {0} historical artifact director(ies) outside the current batch" -f $untracked.Count)
if ($ListUntracked -and $untracked.Count -gt 0) {
    foreach ($name in ($untracked | Sort-Object)) { Write-Output ("  " + $name) }
} elseif ($untracked.Count -gt 0) {
    $sample = ($untracked | Sort-Object | Select-Object -First 8) -join ', '
    $tail = if ($untracked.Count -gt 8) { ', ...' } else { '' }
    Write-Output ("  e.g. {0}{1}  (use -ListUntracked for the full list)" -f $sample, $tail)
}

if ($errors.Count -eq 0) {
    Write-Output 'consistency: ok'
    exit 0
}
foreach ($problem in $errors) { Write-Output ("ERROR " + $problem) }
Write-Output ("consistency: {0} error(s)" -f $errors.Count)
exit 1
