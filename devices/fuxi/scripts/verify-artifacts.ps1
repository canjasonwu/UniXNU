# Verify the fuxi test artifacts against the recorded manifest.
# ASCII-only on purpose: this file must survive Windows PowerShell 5.1 and pwsh 7.
#
# Usage:
#   pwsh -NoProfile -File C:\Users\Jason Wu\dsh\verify-artifacts.ps1
#   pwsh -NoProfile -File C:\Users\Jason Wu\dsh\verify-artifacts.ps1 -Json C:\path\to\artifacts-manifest.json
#
# Exit codes: 0 all match, 1 mismatch or missing file, 2 manifest unreadable.

param(
    [string]$Json = 'C:\Users\Jason Wu\dsh\artifacts-manifest.json'
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $Json)) {
    Write-Output "MISSING_MANIFEST $Json"
    exit 2
}

$manifest = Get-Content -LiteralPath $Json -Raw | ConvertFrom-Json
if ($null -eq $manifest) {
    Write-Output "EMPTY_MANIFEST $Json"
    exit 2
}

$bad = 0
$ok = 0
foreach ($entry in $manifest) {
    $letter = $entry.Letter
    $path = $entry.Path
    if (-not (Test-Path -LiteralPath $path)) {
        Write-Output ("{0} MISSING {1}" -f $letter, $path)
        $bad++
        continue
    }
    $item = Get-Item -LiteralPath $path
    $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($item.Length -ne [int64]$entry.Bytes) {
        Write-Output ("{0} SIZE_MISMATCH expected={1} actual={2} {3}" -f $letter, $entry.Bytes, $item.Length, $path)
        $bad++
        continue
    }
    if ($hash -ne $entry.Sha256) {
        Write-Output ("{0} HASH_MISMATCH expected={1} actual={2} {3}" -f $letter, $entry.Sha256.Substring(0, 16), $hash.Substring(0, 16), $path)
        $bad++
        continue
    }
    Write-Output ("{0} ok {1} {2}" -f $letter, $hash.Substring(0, 12), $path)
    $ok++
}

Write-Output ''
Write-Output ("checked {0} artifact(s): ok={1} bad={2}" -f ($ok + $bad), $ok, $bad)
if ($bad -gt 0) { exit 1 }
exit 0
