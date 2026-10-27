#!/usr/bin/env bash
# Boot the fuxi chain in QEMU without a device.
#
# Stages the built kernel, DarwinBoot and a synthetic Apple device tree into an ESP,
# then runs it under AAVMF + QEMU virt. The kernel console appears in the serial log,
# so a panic or a hang can be read without a phone and without a photo - which is what
# the on-device probes could not deliver.
#
# Requirements: qemu-system-aarch64, AAVMF (qemu-efi-aarch64), dtc, and a built
# out/fuxi/kernel/kernel.macho plus uefi-fuxi/Build/.../DarwinBoot.efi.
#
# Findings from the first runs:
#   - 1 GiB RAM is not enough. Firmware code writes the fuxi splash framebuffer at
#     0xB8000000, which is unmapped below that, and the data abort surfaces as
#     "Synchronous Exception" from the firmware handler.
#   - the XNU entry needs DarwinBoot staged as a driver in unixnu.toml; without it the
#     entry returns Unsupported ("no loader protocol installed").
#   - with 2 GiB the handoff completes and the kernel goes silent, which is where the
#     offline debugging continues.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
OUT=$ROOT/out/qemu-fuxi
ESP=$OUT/esp
MEMORY_MIB=${MEMORY_MIB:-2048}
SECONDS_TO_RUN=${SECONDS_TO_RUN:-150}
FIRMWARE=${FIRMWARE:-/usr/share/AAVMF/AAVMF_CODE.fd}
VARS_TEMPLATE=${VARS_TEMPLATE:-/usr/share/AAVMF/AAVMF_VARS.fd}

rm -rf "$OUT"
mkdir -p "$ESP/EFI/BOOT" "$ESP/EFI/UniXNU/Payloads"

cp "$ROOT/package/Build/Smoke/esp/EFI/BOOT/BOOTAA64.EFI" "$ESP/EFI/BOOT/BOOTAA64.EFI"
cp "$ROOT/out/fuxi/kernel/kernel.macho" "$ESP/EFI/UniXNU/Payloads/kernel.macho"
cp "$ROOT/uefi-fuxi/Build/fuxiPkg/DEBUG_CLANGPDB/AARCH64/DarwinBoot.efi" \
   "$ESP/EFI/UniXNU/Payloads/DarwinBoot.efi"

python3 "$ROOT/scripts/qemu-entry/make-dt.py" \
    --output "$ESP/EFI/UniXNU/Payloads/kernel.macho.adt" \
    --armvirt --armcpu --smp 2 --platform-compatible linux,dummy-virt \
    --owned-memory-size 0x10000000 --dram-size 0x40000000 \
    --osenvironment main --panic-log-address 0x7F000000

cat > "$ESP/EFI/UniXNU/unixnu.toml" <<'TOML'
version = 1
[boot]
timeout = 2
default = "xnu"
[kits]
autoload = false
[[driver]]
path = "Payloads/DarwinBoot.efi"
[[entry]]
id = "xnu"
title = "fuxi-qemu"
type = "xnu"
path = "Payloads/kernel.macho"
args = "-v serial=3 debug=0x14e kt_fixed_zones=120 -no_slto_panic"
TOML

cp "$VARS_TEMPLATE" "$OUT/vars.fd"
echo "RAM ${MEMORY_MIB} MiB, firmware $FIRMWARE"
timeout "$SECONDS_TO_RUN" qemu-system-aarch64 \
    -M virt -cpu cortex-a72 -m "$MEMORY_MIB" -smp 2 \
    -drive if=pflash,format=raw,readonly=on,file="$FIRMWARE" \
    -drive if=pflash,format=raw,file="$OUT/vars.fd" \
    -drive if=none,id=esp,format=raw,readonly=on,file="fat:$ESP" \
    -device virtio-blk-pci,drive=esp -device qemu-xhci -device usb-kbd -nic none \
    -display none -no-reboot -serial "file:$OUT/serial.log" || true

echo "=== serial log ==="
tail -40 "$OUT/serial.log" 2>/dev/null || echo "no log"
