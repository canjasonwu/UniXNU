#!/usr/bin/env python3
"""Decode the fuxi-{i,d}abort-el0 panic line.

Usage:
    python3 decode-el0.py "esr=96000045 far=1000000f0 pc=1000004c8"

It prints the exception class, the fault status code with its meaning, the user virtual address
relative to the probe's __TEXT base (0x100000000 unless the line shows another base), and the
instruction bytes at that offset taken from the probe binary that is inside the HFS volume.

All inputs are hex without 0x. Missing values may be omitted; the script decodes what it has.
"""
import struct
import sys
from pathlib import Path

# Fault status codes, ARMv8 ESR_ELx.ISS.FSC for aborts from a lower exception level.
FSC = {
    0x00: "address size fault, level 0",
    0x01: "address size fault, level 1",
    0x02: "address size fault, level 2",
    0x03: "address size fault, level 3",
    0x04: "translation fault, level 0",
    0x05: "translation fault, level 1",
    0x06: "translation fault, level 2",
    0x07: "translation fault, level 3",
    0x08: "access flag fault, level 0",
    0x09: "access flag fault, level 1",
    0x0A: "access flag fault, level 2",
    0x0B: "access flag fault, level 3",
    0x0C: "permission fault, level 0",
    0x0D: "permission fault, level 1",
    0x0E: "permission fault, level 2",
    0x0F: "permission fault, level 3",
    0x10: "synchronous external abort",
    0x14: "synchronous external abort on translation table walk, level 1",
    0x15: "synchronous external abort on translation table walk, level 2",
    0x16: "synchronous external abort on translation table walk, level 3",
    0x18: "parity or ECC error on memory access",
    0x21: "alignment fault",
    0x30: "debug event",
}

EC = {
    0x20: "instruction abort from EL0",
    0x21: "instruction abort from EL1",
    0x24: "data abort from EL0",
    0x25: "data abort from EL1",
    0x26: "SP alignment fault",
    0x00: "unknown/uncategorized",
    0x3C: "BRK instruction",
}

HEADER = Path("/root/hfs-populate/xfer/sbin/launchd")


def parse(argv):
    values = {}
    for arg in argv:
        for part in arg.replace(",", " ").split():
            if "=" in part:
                key, raw = part.split("=", 1)
                try:
                    values[key.strip().lower().rstrip(":")] = int(raw, 16)
                except ValueError:
                    pass
    return values


def main(argv):
    v = parse(argv)
    if not v:
        print(__doc__)
        return 1
    esr = v.get("esr")
    far = v.get("far")
    pc = v.get("pc")
    print("input:", " ".join("%s=%s" % (k, hex(val)) for k, val in sorted(v.items())))
    if esr is not None:
        ec = (esr >> 26) & 0x3F
        fsc = esr & 0x3F
        wnr = (esr >> 6) & 1
        print("EC  = 0x%02x  %s" % (ec, EC.get(ec, "unknown")))
        print("FSC = 0x%02x  %s" % (fsc, FSC.get(fsc, "unknown")))
        if ec in (0x24, 0x25):
            print("WnR = %d (%s)" % (wnr, "write" if wnr else "read"))
        if ec in (0x24, 0x25) and fsc >= 0x0C and fsc <= 0x0F:
            print("=> permission fault: the page exists but the access is not allowed.")
            print("   SIGBUS in this kernel = EXC_BAD_ACCESS with code != KERN_INVALID_ADDRESS")
            print("   (bsd/uxkern/ux_exception.c:54).")
    if far is not None:
        print("FAR = %s" % hex(far))
        base = 0x100000000
        if far >= base:
            print("      FAR - probe __TEXT base = +0x%x" % (far - base))
        else:
            print("      FAR is below the probe image (stack or guard page region)")
    if pc is not None:
        base = 0x100000000
        print("PC  = %s" % hex(pc))
        offset = pc - base
        if offset >= 0:
            print("      PC - probe __TEXT base = +0x%x" % offset)
            if HEADER.is_file():
                data = HEADER.read_bytes()
                # __TEXT file offset 0 corresponds to the probe base in this non-PIE image.
                if 0 <= offset < len(data):
                    words = data[offset:offset + 16]
                    print("      bytes at that offset: %s" % words.hex())
                    for i in range(0, len(words) - 3, 4):
                        print("        +0x%02x  0x%08x" % (i, struct.unpack_from("<I", words, i)[0]))
        else:
            print("      PC is not inside the probe image")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
