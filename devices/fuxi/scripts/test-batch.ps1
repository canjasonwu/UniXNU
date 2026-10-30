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
#   C = artifacts-HFS-NAKED/boot.img       HFS root, probe without any stack use
#   D = artifacts-LOADINFO/boot.img        HFS root, kernel prints entry/sp/stack after exec
#   E = artifacts-NAKED-MOCKFS/boot.img    mockfs root, same kernel and probe as D (control)
#   F = artifacts-VMFAULT/boot.img         HFS root, vm_fault verdict, no-stack probe
#   G = artifacts-VMFAULT-STACK/boot.img   HFS root, vm_fault verdict, stack-touching probe
#   H = artifacts-4K-HFS/boot.img          HFS root, 4K-aligned probe
#   I = artifacts-4K-MOCKFS/boot.img       mockfs root, 4K-aligned probe (control)
#   J = artifacts-OMNIDIAG/boot.img        HFS root, every probe in one kernel
#   K = artifacts-HFS-EXIT1/boot.img       HFS root, getpid+exit(1) probe (did userland run?)
#   M = artifacts-HFS-OPEN/boot.img        HFS root, open-only probe (exit 5 opened, 6 failed)
#   N = artifacts-HFS-DATA/boot.img        HFS root, probe with a __DATA segment
#   S = artifacts-SIZE256/boot.img         421 MB boot.img, capacity test only (no userland experiment)
#   V = artifacts-NATIVEUFS-START/boot.img HFS root, NativeUfs start hook + its read-only root check
#   W = artifacts-UFS-CLOCK/boot.img       UFS clock-branch enable probe (no driver)
#
# The script only runs "fastboot boot": it never flashes or erases anything.

param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'M', 'N', 'S', 'V', 'W')]
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
    D = 'C:\Users\Jason Wu\dsh\artifacts-LOADINFO\boot.img'
    E = 'C:\Users\Jason Wu\dsh\artifacts-NAKED-MOCKFS\boot.img'
    F = 'C:\Users\Jason Wu\dsh\artifacts-VMFAULT\boot.img'
    G = 'C:\Users\Jason Wu\dsh\artifacts-VMFAULT-STACK\boot.img'
    H = 'C:\Users\Jason Wu\dsh\artifacts-4K-HFS\boot.img'
    I = 'C:\Users\Jason Wu\dsh\artifacts-4K-MOCKFS\boot.img'
    J = 'C:\Users\Jason Wu\dsh\artifacts-OMNIDIAG\boot.img'
    K = 'C:\Users\Jason Wu\dsh\artifacts-HFS-EXIT1\boot.img'
    M = 'C:\Users\Jason Wu\dsh\artifacts-HFS-OPEN\boot.img'
    N = 'C:\Users\Jason Wu\dsh\artifacts-HFS-DATA\boot.img'
    S = 'C:\Users\Jason Wu\dsh\artifacts-SIZE256\boot.img'
    V = 'C:\Users\Jason Wu\dsh\artifacts-NATIVEUFS-START\boot.img'
    W = 'C:\Users\Jason Wu\dsh\artifacts-UFS-CLOCK\boot.img'
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
