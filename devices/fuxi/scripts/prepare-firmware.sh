#!/usr/bin/env bash
# Apply every fuxi firmware patch to a uefi-fuxi checkout.
#
# This is the single place the patches live: the CI workflow calls it and a local
# build calls it, so the two cannot drift. Every step is idempotent, so it is safe
# to run on a tree that already has some of them.
#
# Usage:
#   devices/fuxi/scripts/prepare-firmware.sh [--uefi DIR] [--watchdog-seconds N]
#
# Run from the repository root. --uefi defaults to uefi-fuxi; pass an empty string
# to skip the firmware-only patches when there is no checkout.
set -euo pipefail

UEUFI=uefi-fuxi
WATCHDOG_SECONDS=90

while [ $# -gt 0 ]; do
  case "$1" in
    --uefi) UEUFI="$2"; shift 2 ;;
    --uefi=*) UEUFI="${1#*=}"; shift ;;
    --watchdog-seconds) WATCHDOG_SECONDS="$2"; shift 2 ;;
    --watchdog-seconds=*) WATCHDOG_SECONDS="${1#*=}"; shift ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

ROOT=$(pwd)
export ROOT UEUFI WATCHDOG_SECONDS

if [ ! -d "$UEUFI/Platforms/Xiaomi/fuxiPkg" ]; then
  echo "prepare-firmware: $UEUFI is not a uefi-fuxi checkout" >&2
  exit 1
fi

echo "prepare-firmware: patching $UEUFI (watchdog ${WATCHDOG_SECONDS}s)"

# The device config the firmware build reads.
mkdir -p "$UEUFI/Resources/Configs"
cp devices/fuxi/uefi-config/fuxi.toml "$UEUFI/Resources/Configs/fuxi.toml"
grep -q '0xA7000000' "$UEUFI/Resources/Configs/fuxi.toml"

python3 - <<'PYEOF'
import os
import re
import sys
from pathlib import Path

root = Path(os.environ["ROOT"])
uefi = root / os.environ["UEUFI"]
watchdog_seconds = int(os.environ["WATCHDOG_SECONDS"])

fuxi = uefi / "Platforms/Xiaomi/fuxiPkg"


def edit(path, fn):
    p = Path(path)
    if not p.is_file():
        sys.exit("missing file: %s" % p)
    before = p.read_text(encoding="utf-8")
    after = fn(before)
    if after != before:
        p.write_text(after, encoding="utf-8")
        print("  patched %s" % p.relative_to(root))


# --------------------------------------------------------------------------
# MemoryMapLib: the loader needs one contiguous conventional run, and the
# window is whatever the ADT says, so the run has to be big enough.
# --------------------------------------------------------------------------
def patch_memory_map(t):
    t = re.sub(r'[ \t]*\{"FD Reserved".*?\},\r?\n', '', t)
    t = re.sub(r'("UEFI FD",\s*)0xA7100000,\s*0x00300000', r'\g<1>0xA7000000, 0x00400000', t)

    # The vendor table reserves the kernel window at 0xA8000000; publish it.
    t, n = re.subn(r'(\{"Kernel",\s*0xA8000000,\s*0x10000000,[^}]*?)\bReserv\b', r'\1Conv', t)
    if n == 0 and not re.search(r'\{"Kernel",\s*0xA8000000,\s*0x10000000,[^}]*?Conv\b', t):
        sys.exit("Kernel window row not found in MemoryMapLib")

    # PIL Reserved, Display Demura, RSRV2 and DBI Dump are back to back from
    # 0x89800000 to the FD at 0xA7000000; releasing them yields one 490 MiB run.
    for row in ("PIL Reserved", "Display Demura", "RSRV2", "DBI Dump"):
        pattern = r'(\{"' + re.escape(row) + r'",[^}]*?)\bReserv\b'
        t, n = re.subn(pattern, r'\1Conv', t)
        if n == 0 and not re.search(r'\{"' + re.escape(row) + r'",[^}]*?Conv\b', t):
            sys.exit("reservation row not found: " + row)
    return t


edit(fuxi / "Library/MemoryMapLib/MemoryMapLib.c", patch_memory_map)

# --------------------------------------------------------------------------
# BootShim: invalidate the destination cache before the copy, or the firmware
# decompressor reads stale lines and fails with "Decompress Failed".
# --------------------------------------------------------------------------
SHIM_NEW = "".join([
    "  ldr   x6,  =FD_SIZE\n", "\n",
    "  /* Discard stale dirty lines covering the destination before copying. */\n",
    "  mrs   x9, ctr_el0\n", "  ubfx  x9, x9, #16, #4\n",
    "  mov   x10, #4\n", "  lsl   x9, x10, x9\n", "  sub   x10, x9, #1\n",
    "  bic   x11, x5, x10\n", "  add   x12, x5, x6\n",
    "_InvalLoop:\n", "  dc    ivac, x11\n", "  add   x11, x11, x9\n",
    "  cmp   x11, x12\n", "  b.lo  _InvalLoop\n", "  dsb   sy\n", "\n",
    "_CopyLoop:",
])


def patch_boot_shim(t):
    if "_InvalLoop" in t:
        return t
    old = "  ldr   x6,  =FD_SIZE\n\n_CopyLoop:"
    if old not in t:
        sys.exit("BootShim anchor not found")
    return t.replace(old, SHIM_NEW, 1)


edit(uefi / "BootShim/BootShim.S", patch_boot_shim)


# --------------------------------------------------------------------------
# FuxiBootMemoryDxe: nothing publishes the payload volume otherwise, so the
# boot manager reports "no boot entry". Enable it and drop in the override
# that can actually find the volume in the initrd.
# --------------------------------------------------------------------------
def enable_boot_memory_dxe(t):
    pat = re.compile(r'(?m)^[ \t]*#[ \t]*INF[ \t]+fuxiPkg/Drivers/FuxiBootMemoryDxe/FuxiBootMemoryDxe\.inf[ \t]*$')
    t, n = pat.subn("  INF fuxiPkg/Drivers/FuxiBootMemoryDxe/FuxiBootMemoryDxe.inf", t)
    return t


def enable_boot_memory_dsc(t):
    pat = re.compile(r'(?m)^[ \t]*#[ \t]*fuxiPkg/Drivers/FuxiBootMemoryDxe/FuxiBootMemoryDxe\.inf[ \t]*$')
    t, n = pat.subn("  fuxiPkg/Drivers/FuxiBootMemoryDxe/FuxiBootMemoryDxe.inf", t)
    return t


edit(fuxi / "Include/DXE.inc", enable_boot_memory_dxe)
edit(fuxi / "fuxi.dsc", enable_boot_memory_dsc)

override = root / "devices/fuxi/driver-overrides/FuxiBootMemoryDxe.c"
target = fuxi / "Drivers/FuxiBootMemoryDxe/FuxiBootMemoryDxe.c"
if not override.is_file():
    sys.exit("missing override: %s" % override)
target.parent.mkdir(parents=True, exist_ok=True)
target.write_bytes(override.read_bytes())
print("  installed FuxiBootMemoryDxe override")

# --------------------------------------------------------------------------
# FuxiWatchdog: resets through PSCI so an unattended test always comes back.
# It has to be dispatched before DarwinBoot blocks in its menu, so it goes in
# next to TimerDxe rather than at the end of the FDF.
# --------------------------------------------------------------------------
watchdog_dir = fuxi / "Drivers/FuxiWatchdog"
watchdog_dir.mkdir(parents=True, exist_ok=True)
watchdog_inf = root / "devices/fuxi/watchdog/FuxiWatchdog.inf"
watchdog_c = root / "devices/fuxi/watchdog/FuxiWatchdog.c"
(watchdog_dir / "FuxiWatchdog.inf").write_bytes(watchdog_inf.read_bytes())
ticks = watchdog_seconds * 10000000
source = watchdog_c.read_text(encoding="utf-8")
source, n = re.subn(r'(?m)^#define WD_TICKS .*$', "#define WD_TICKS  %dULL" % ticks, source)
if n == 0:
    sys.exit("WD_TICKS line not found in FuxiWatchdog.c")
(watchdog_dir / "FuxiWatchdog.c").write_text(source, encoding="utf-8")
print("  installed FuxiWatchdog (%d s)" % watchdog_seconds)


def add_watchdog_to_dxe(t):
    if "FuxiWatchdog" in t:
        return t
    anchor = "  INF ArmPkg/Drivers/TimerDxe/TimerDxe.inf"
    if anchor not in t:
        sys.exit("DXE.inc TimerDxe anchor not found")
    return t.replace(anchor, anchor + "\n  INF fuxiPkg/Drivers/FuxiWatchdog/FuxiWatchdog.inf", 1)


def add_watchdog_to_dsc(t):
    if "FuxiWatchdog" in t:
        return t
    key = "fuxiPkg/Drivers/FuxiKeyInputDxe/FuxiKeyInputDxe.inf"
    if key not in t:
        sys.exit("fuxi.dsc anchor not found")
    return t.replace(key, key + "\n  fuxiPkg/Drivers/FuxiWatchdog/FuxiWatchdog.inf", 1)


edit(fuxi / "Include/DXE.inc", add_watchdog_to_dxe)
edit(fuxi / "fuxi.dsc", add_watchdog_to_dsc)

# --------------------------------------------------------------------------
# DarwinBoot: lower the window floor so the window can be sized to the free run
# minus the 2 MiB alignment offset.
# --------------------------------------------------------------------------
window_h = root / "drivers/DarwinBoot/DarwinBootPkg/Driver/WindowSize.h"
if window_h.is_file():
    def lower_floor(t):
        return t.replace("size >= 0x10000000ULL", "size >= 0x0e000000ULL")
    edit(window_h, lower_floor)
else:
    print("  note: %s not present, skipping the window floor" % window_h.relative_to(root))

print("prepare-firmware: done")
PYEOF
