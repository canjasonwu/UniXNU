# Batch test helper for the fuxi XNU bring-up images.
# ASCII-only on purpose: this file must survive Windows PowerShell 5.1 and pwsh 7.
# Requires: platform-tools (adb/fastboot) at the Android SDK path below.
#
# Usage:
#   pwsh -NoProfile -File C:\Users\Jason Wu\dsh\test-batch.ps1 -Image A
#   pwsh -NoProfile -File C:\Users\Jason Wu\dsh\test-batch.ps1 -Image A -WaitSeconds 300
#
# -Image letters:
#   A = artifacts-SIGBUS/boot.img          HFS root, EL0 abort + SIGBUS probes
#   B = artifacts-SIGBUS-CONTROL/boot.img  mockfs root, control
#   C = artifacts-HFS-NAKED/boot.img       HFS root, probe without any stack use (when built)
#
# The script only runs "fastboot boot": it never flashes or erases anything.

param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('A', 'B', 'C')]
    [string]$Image,

    [int]$WaitSeconds = 240
)

$ErrorActionPreference = 'Stop'

$sdk = 'C:\Users\Jason Wu\AppData\Local\Android\Sdk\platform-tools'
$adb = Join-Path $sdk 'adb.exe'
$fb = Join-Path $sdk 'fastboot.exe'

$images = @{
    A = 'C:\Users\Jason Wu\dsh\artifacts-SIGBUS\boot.img'
    B = 'C:\Users\Jason Wu\dsh\artifacts-SIGBUS-CONTROL\boot.img'
    C = 'C:\Users\Jason Wu\dsh\artifacts-HFS-NAKED\boot.img'
}

$path = $images[$Image]
if (-not (Test-Path -LiteralPath $path)) {
    Write-Output "MISSING_IMAGE $Image -> $path"
    Write-Output 'Build it first, or pick another -Image.'
    exit 3
}

if (-not (Test-Path -LiteralPath $fb)) {
    Write-Output "MISSING_FASTBOOT $fb"
    exit 3
}

Write-Output ("image {0}: {1}" -f $Image, $path)
$hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
$size = (Get-Item -LiteralPath $path).Length
Write-Output ("sha256 {0}" -f $hash)
Write-Output ("size   {0} bytes" -f $size)

function Get-FastbootState {
    $text = (& $fb devices 2>&1 | Out-String)
    if ($text -match '(?m)^\S+\s+fastboot\s*$') { return 'fastboot' }
    if (Test-Path -LiteralPath $adb) {
        $adbText = (& $adb devices 2>&1 | Out-String)
        if ($adbText -match '(?m)^\S+\s+device\s*$') { return 'android' }
    }
    return 'none'
}

$deadline = (Get-Date).AddSeconds($WaitSeconds)
$state = Get-FastbootState
while ($state -eq 'android' -and (Get-Date) -lt $deadline) {
    Write-Output 'android is up, rebooting to bootloader'
    & $adb reboot bootloader | Out-Null
    Start-Sleep -Seconds 8
    $state = Get-FastbootState
}
while ($state -eq 'none' -and (Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 3
    $state = Get-FastbootState
}

if ($state -ne 'fastboot') {
    Write-Output ("NOT_READY state={0}" -f $state)
    Write-Output 'Hold power for 10-15 seconds so the phone reaches fastboot, then rerun.'
    exit 2
}

Write-Output 'booting the image (fastboot boot, nothing is written to storage)'
$boot = (& $fb boot $path 2>&1 | Out-String)
Write-Output $boot.Trim()

Write-Output ''
Write-Output 'Wait 20-30 seconds, then photograph the screen and send it back.'
Write-Output 'Expected text with these probes:'
Write-Output '  fuxi-dabort-el0: esr=... far=... pc=...   user data access fault'
Write-Output '  fuxi-iabort-el0: esr=... far=... pc=...   user instruction fetch fault'
Write-Output '  fuxi-sigbus: caller=... pid=...           kernel delivered SIGBUS'
Write-Output '  initproc failed to start ...              no user fault recorded (image B control)'
