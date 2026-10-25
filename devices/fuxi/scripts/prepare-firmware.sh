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
# Run from the repository root.
set -euo pipefail

UEUFI=uefi-fuxi
WATCHDOG_SECONDS=90

while [ $# -gt 0 ]; do
  case "$1" in
    --uefi) UEUFI="$2"; shift 2 ;;
    --uefi=*) UEUFI="${1#*=}"; shift ;;
    --watchdog-seconds) WATCHDOG_SECONDS="$2"; shift 2 ;;
    --watchdog-seconds=*) WATCHDOG_SECONDS="${1#*=}"; shift ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
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


# ---------------------------------------------------------------------------
# MemoryMapLib
#
# The 5th field of a row is the EFI_RESOURCE_TYPE and the 7th is the
# EFI_MEMORY_TYPE. Flipping only the memory type leaves the region a reserved
# resource, and DXE keeps treating it as reserved, so rows are rewritten whole
# to the shape of "DXE Heap", which really is conventional memory.
# ---------------------------------------------------------------------------
CONVENTIONAL = "AddMem,   SYS_MEM, SYS_MEM_CAP, Conv,   WRITE_BACK_XN"
ROW_RE = r'\{"%s",\s*(0x[0-9A-Fa-f]+),\s*(0x[0-9A-Fa-f]+),[^}]*\}'


def set_row(source, name, tail):
    pattern = re.compile(ROW_RE % re.escape(name))
    source, n = pattern.subn(
        lambda m: '{"%s", %s, %s, %s}' % (name, m.group(1), m.group(2), tail), source)
    if n == 0:
        sys.exit("row not found: " + name)
    return source


def patch_memory_map(t):
    t = re.sub(r'[ \t]*\{"FD Reserved".*?\},\r?\n', '', t)
    t = re.sub(r'("UEFI FD",\s*)0xA7100000,\s*0x00300000', r'\g<1>0xA7000000, 0x00400000', t)

    # The kernel window the loader allocates from.
    t = set_row(t, "Kernel", CONVENTIONAL)

    # PIL Reserved, Display Demura and RSRV2 are back to back from 0x89800000 up
    # to the DBI Dump hole at 0xA6100000, which gives one ~473 MiB run. PIL is
    # modem/DSP firmware that this build never starts; the other two are display
    # and modem scratch.
    for row in ("PIL Reserved", "Display Demura", "RSRV2"):
        t = set_row(t, row, CONVENTIONAL)
    return t


edit(fuxi / "Library/MemoryMapLib/MemoryMapLib.c", patch_memory_map)

# ---------------------------------------------------------------------------
# BootShim: invalidate the destination cache before the copy, or the firmware
# decompressor reads stale lines and fails with "Decompress Failed".
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# FuxiBootMemoryDxe: nothing publishes the payload volume otherwise, so the boot
# manager reports "no boot entry". Enable it and drop in the override that can
# find the volume in the initrd.
# ---------------------------------------------------------------------------
def enable_boot_memory_dxe(t):
    pat = re.compile(r'(?m)^[ \t]*#[ \t]*INF[ \t]+fuxiPkg/Drivers/FuxiBootMemoryDxe/FuxiBootMemoryDxe\.inf[ \t]*$')
    t, _ = pat.subn("  INF fuxiPkg/Drivers/FuxiBootMemoryDxe/FuxiBootMemoryDxe.inf", t)
    return t


def enable_boot_memory_dsc(t):
    pat = re.compile(r'(?m)^[ \t]*#[ \t]*fuxiPkg/Drivers/FuxiBootMemoryDxe/FuxiBootMemoryDxe\.inf[ \t]*$')
    t, _ = pat.subn("  fuxiPkg/Drivers/FuxiBootMemoryDxe/FuxiBootMemoryDxe.inf", t)
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

# ---------------------------------------------------------------------------
# FuxiWatchdog: resets through PSCI so an unattended test always comes back. It
# has to be dispatched before DarwinBoot blocks in its menu, so it goes next to
# TimerDxe rather than at the end of the FDF.
# ---------------------------------------------------------------------------
watchdog_dir = fuxi / "Drivers/FuxiWatchdog"
watchdog_dir.mkdir(parents=True, exist_ok=True)
(watchdog_dir / "FuxiWatchdog.inf").write_bytes(
    (root / "devices/fuxi/watchdog/FuxiWatchdog.inf").read_bytes())
ticks = watchdog_seconds * 10000000
source = (root / "devices/fuxi/watchdog/FuxiWatchdog.c").read_text(encoding="utf-8")
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

# ---------------------------------------------------------------------------
# DarwinBoot: lower the window floor so the window can be sized to the free run
# minus the 2 MiB alignment offset.
# ---------------------------------------------------------------------------
window_h = root / "drivers/DarwinBoot/DarwinBootPkg/Driver/WindowSize.h"
if window_h.is_file():
    def lower_floor(t):
        return t.replace("size >= 0x10000000ULL", "size >= 0x0e000000ULL")
    edit(window_h, lower_floor)
else:
    print("  note: no drivers/DarwinBoot submodule, skipping the window floor")

print("prepare-firmware: done")
PYEOF
