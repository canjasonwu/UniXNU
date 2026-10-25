#!/usr/bin/env python3
"""Patch AMFI's PMGR requirement out of the kernelcache copy.

AMFI's ConfigurationSettings does, at 0x5aaeb00..0x5aaeb38:
    x0 = lookup("ApplePMGRNub")
    x1 = expected OSMetaClass
    x0 = safeMetaCast(x0, x1)
    cbz x0, 0x5aaecfc      -> the block that panics "AMFI: No PMGR?"

On hardware without Apple's power manager nothing creates that nub, so the cast
always fails. This replaces the cbz with a branch to the function's epilogue
(0x5aaec4c: restore frame and ret), so the check reports failure and returns
instead of aborting the boot.

emit_prelink() only rejects changes it makes itself to the original executable
bytes; a patched input is copied through unchanged, so the patch survives prelink.
"""
import hashlib
import struct
import sys
from pathlib import Path

sys.path.insert(0, '/root/UniXNU/kits/NativePrelink')
from native_prelink import macho, backed_offset  # noqa: E402

SOURCE = Path('/root/UniXNU/out/ipsw/21H461/kernelcache.decompressed')
OUTPUT = Path('/root/UniXNU/out/fuxi/kernelcache-amfi-patched')
PATCH_VA = 0xfffffff005aaeb38
BRANCH_VA = 0xfffffff005aaec4c

data = bytearray(SOURCE.read_bytes())
meta = macho(bytes(data))
at = backed_offset(meta['segments'], PATCH_VA, 4, len(data))
original, = struct.unpack_from('<I', data, at)
print('original instruction at 0x%x: 0x%08x' % (PATCH_VA, original))
if original != 0xb4000340:   # cbz x0, 0x5aaecfc
    raise SystemExit('unexpected instruction, refusing to patch')
offset = (BRANCH_VA - PATCH_VA) >> 2
patched = 0x14000000 | (offset & 0x3ffffff)
struct.pack_into('<I', data, at, patched)
OUTPUT.parent.mkdir(parents=True, exist_ok=True)
OUTPUT.write_bytes(bytes(data))
print('patched to 0x%08x (b 0x%x)' % (patched, BRANCH_VA))
print('sha256 %s' % hashlib.sha256(data).hexdigest())
