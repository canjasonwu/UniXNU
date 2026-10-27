#!/usr/bin/env bash
# Build the full fuxi boot image locally: firmware patches, kernel, payload,
# firmware, boot.img. Mirrors .github/workflows/build-fuxi-kernel.yml step for
# step, so a local build and a CI build produce the same image.
#
# Usage:
#   devices/fuxi/scripts/local-build.sh [--all] [--kernel] [--firmware]
#
# With no flags it reuses whatever already exists (kernel.macho, payload.img,
# firmware) and only rebuilds what is missing. --all forces everything.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
cd "$REPO"

CHIP_ID=0x1234567890abcdef
DO_KERNEL=auto
DO_FIRMWARE=auto
DO_PAYLOAD=auto

while [ $# -gt 0 ]; do
  case "$1" in
    --all) DO_KERNEL=1; DO_FIRMWARE=1; DO_PAYLOAD=1; shift ;;
    --kernel) DO_KERNEL=1; shift ;;
    --firmware) DO_FIRMWARE=1; shift ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

# The Linux toolchain from scripts/linux/setup-toolchain.py.
if [ -d /opt/unixnu-linux ]; then
  export PATH="/opt/unixnu-linux/bin:$PATH"
fi

if [ -z "${GITHUB_TOKEN:-}" ]; then
  TOKEN_FILE="${GITHUB_TOKEN_FILE:-/mnt/c/Users/Jason Wu/dsh/fork-add/tools/.gh-token}"
  if [ -f "$TOKEN_FILE" ]; then
    GITHUB_TOKEN=$(tr -d ' \r\n' < "$TOKEN_FILE")
    export GITHUB_TOKEN
  fi
fi

step() { echo; echo "=== $* ==="; }
have() { [ -e "$1" ]; }

# The composed tree under out/fuxi/tree is not refreshed for new device files, and
# a stale copy there silently builds the wrong object, so sync what this pipeline
# owns before the first build step.
mkdir -p out/fuxi/tree/devices/fuxi/platform-power
cp devices/fuxi/platform-power/ShengPlatformPower.cpp    out/fuxi/tree/devices/fuxi/platform-power/ShengPlatformPower.cpp 2>/dev/null || true
cp scripts/build-fuxi-platform-power.py out/fuxi/tree/scripts/ 2>/dev/null || true
cp devices/fuxi/scripts/build-arm-driver.py out/fuxi/tree/scripts/ 2>/dev/null || true

step "prepare the firmware tree"
bash devices/fuxi/scripts/prepare-firmware.sh --uefi uefi-fuxi --watchdog-seconds 90

step "defconfig"
python3 ./unixnu defconfig fuxi_defconfig -D devices/fuxi

if [ "$DO_KERNEL" != 0 ] || ! have out/fuxi/kernel/kernel.macho; then
  step "fetch sources"
  python3 ./unixnu fetch

  step "mirror the availability headers"
# The Linux toolchain installs them under usr/include, while the archive builders
# reuse kernel compile records that search usr/local/include, so mirror them there.
FR=.cache/fakeroot
if [ -d out/fuxi/tree/$FR/usr/include ] && [ ! -e out/fuxi/tree/$FR/usr/local/include/Availability.h ]; then
  mkdir -p out/fuxi/tree/$FR/usr/local/include
  cp -n out/fuxi/tree/$FR/usr/include/Availability*.h out/fuxi/tree/$FR/usr/local/include/ 2>/dev/null || true
fi

step "build kernel, pass 1 (compile records)"
  python3 ./unixnu build kernel > kernel-build-pass1.log 2>&1 || true
  cp out/fuxi/logs/kernel-bootstrap.log out/fuxi/logs/kernel-bootstrap-pass1.log 2>/dev/null || true

  step "build kernel, pass 2 (archives and link)"
  python3 ./unixnu build kernel > kernel-build.log 2>&1
  tail -3 kernel-build.log
fi

if [ "$DO_PAYLOAD" != 0 ] || ! have out/fuxi/payload/payload.img; then
  step "build payload volume"
  # A data ramdisk gives the kernel an md device to root from; mockfs maps
  # that device as the executable it runs.
  # PID 1 lives on the data ramdisk: mockfs maps that device as the executable
  # it runs, so the ramdisk content IS the init binary (padded to a full block).
  CLT=/opt/unixnu-linux/CommandLineTools
  "$CLT/usr/bin/clang" -target arm64-apple-ios17.0 -nostdlib -static \
    --ld-path="$CLT/usr/bin/ld" -Wl,-e,__start -o out/fuxi/init \
    devices/fuxi/platform-power/init.c > init-build.log 2>&1
  tail -1 init-build.log
  python3 -c "d=open('out/fuxi/init','rb').read(); open('out/fuxi/data-ramdisk.img','wb').write(d + bytes(65536 - len(d)))"
  python3 ./unixnu build payload --uefi uefi-fuxi --unique-chip-id "$CHIP_ID" \
    --data-ramdisk out/fuxi/data-ramdisk.img > payload.log 2>&1
  tail -2 payload.log

  step "loader preflight"
  # DarwinBoot bounces back to its menu, with no kernel output at all, when the
  # ADT ramdisk property disagrees with the ramdisk actually supplied. Catch that
  # here instead of on the phone.
  ADT_TMP=$(mktemp)
  if mcopy -n -o -i out/fuxi/payload/payload.img \
      ::/EFI/UniXNU/Payloads/kernel.macho.adt "$ADT_TMP" 2>/dev/null; then
    if grep -aq PackedRAMDisk "$ADT_TMP"; then
      if ! grep -q '^packed_data_ramdisk = true' devices/fuxi/board.toml; then
        echo "preflight: ADT says PackedRAMDisk but board.toml says raw" >&2
        exit 1
      fi
    elif grep -aq RAMDisk "$ADT_TMP"; then
      if grep -q '^packed_data_ramdisk = true' devices/fuxi/board.toml; then
        echo "preflight: ADT says RAMDisk but board.toml says packed" >&2
        exit 1
      fi
    else
      echo "preflight: ADT has no ramdisk property but a data ramdisk was passed" >&2
      exit 1
    fi
    echo "preflight: ADT ramdisk property matches the build"
  else
    echo "preflight: could not extract the ADT from the payload image" >&2
    exit 1
  fi
  rm -f "$ADT_TMP"
fi

if [ "$DO_FIRMWARE" != 0 ] || ! have uefi-fuxi/Build; then
  step "build firmware"
  python3 ./unixnu build firmware --uefi uefi-fuxi --edk2 uefi-fuxi/Mu_Basecore --skip-preflight > firmware-build.log 2>&1
  tail -3 firmware-build.log
fi

step "make boot image"
mkdir -p out/fuxi/boot
python3 devices/fuxi/make-bootimg.py \
  --uefi uefi-fuxi \
  --dtb devices/fuxi/fuxi-live.dtb \
  --ramdisk out/fuxi/payload/payload.img \
  --header-version 2 \
  --dtb-field \
  --output out/fuxi/boot/boot.img > bootimg.log 2>&1

step "result"
ls -l out/fuxi/boot/boot.img out/fuxi/kernel/kernel.macho out/fuxi/payload/payload.img 2>/dev/null
sha256sum out/fuxi/boot/boot.img
