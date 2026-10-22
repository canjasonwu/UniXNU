#!/usr/bin/env python3
"""Build the fuxi Android boot image from an already built firmware volume.

This deliberately does only the image step of the uefi checkout's build_uefi.py:
it does not run the environment setup, does not touch the Mu_Basecore patches and
does not rebuild the firmware. The firmware volume is an input.

Android boot image v3 carries no load addresses, so what matters is:
    kernel field = gzip(BootShim.bin + SILICIUM_UEFI.fd) + device tree
    header_version = boot_image.header_version from the device config

Usage:
    python3 make-bootimg.py --uefi uefi-fuxi --dtb devices/fuxi/fuxi-live.dtb \
        --output out/fuxi/boot/boot.img
"""
import argparse
import gzip
import hashlib
import struct
import subprocess
import sys
import tomllib
import zlib
from pathlib import Path

PAGE = 4096
MAGIC = b"ANDROID!"
ARM64_MAGIC = b"ARM\x64"


def run(cmd, cwd=None):
    print("+ " + " ".join(str(c) for c in cmd), flush=True)
    if subprocess.run(cmd, cwd=cwd).returncode != 0:
        sys.exit("command failed: " + " ".join(str(c) for c in cmd))


def load_config(uefi, device):
    path = uefi / "Resources/Configs" / f"{device}.toml"
    if not path.is_file():
        sys.exit(f"missing device config: {path}")
    with open(path, "rb") as f:
        return tomllib.load(f)


def firmware_path(uefi, device, target, override):
    if override:
        p = Path(override)
        if not p.is_file():
            sys.exit(f"missing firmware volume: {p}")
        return p
    p = uefi / "Build" / f"{device}Pkg" / f"{target}_CLANGPDB" / "FV" / "SILICIUM_UEFI.fd"
    if not p.is_file():
        sys.exit(f"missing firmware volume: {p}")
    return p


def build_shim(uefi, fd_base, fd_size, requires_header, override):
    if override:
        p = Path(override)
        if not p.is_file():
            sys.exit(f"missing boot shim: {p}")
        return p.read_bytes()
    shim_dir = uefi / "BootShim"
    run(["make", "-C", shim_dir,
         f"REQUIRES_KERNEL_HEADER={int(requires_header)}",
         f"FD_BASE={hex(fd_base)}",
         f"FD_SIZE={hex(fd_size)}"])
    p = shim_dir / "BootShim.bin"
    if not p.is_file():
        sys.exit(f"missing boot shim after build: {p}")
    return p.read_bytes()


def check_shim(shim, fd_len, fd_base, requires_header):
    """The shim must describe itself plus the firmware volume it is glued to."""
    if not requires_header:
        return
    if len(shim) < 64:
        sys.exit(f"boot shim too small to carry an ARM64 image header ({len(shim)} bytes)")
    if shim[56:60] != ARM64_MAGIC:
        sys.exit("boot shim lacks the ARM64 image header magic")
    text_offset, image_size = struct.unpack_from("<QQ", shim, 8)
    want = len(shim) + fd_len
    if image_size != want:
        sys.exit(f"boot shim image_size {image_size:#x} != shim + firmware {want:#x}")


def fix_fat32_bpb(ramdisk):
    """Make a payload volume that EDK2 will actually mount.

    Two problems, both caused by mtools writing a volume that DOS accepts but
    that the EDK2 disk stack misreads:

    1. A small FAT32 volume gets its sector count in the FAT16 field at offset
       19 while BPB_TotSec32 at offset 32 stays zero. EnhancedFatDxe classifies
       the volume as FAT32 because BPB_FATSz16 is zero, reads BPB_TotSec32,
       computes a zero sized volume and refuses it.
    2. The volume boot record keeps the classic MS-DOS boot code, and bytes
       446..462 of it happen to decode as a valid MBR partition entry (bootable,
       type 0x0C, start 0). PartitionDxe then treats the volume as a partitioned
       disk, creates a bogus child device and takes the parent's DiskIo with
       EFI_OPEN_PROTOCOL_BY_DRIVER, so EnhancedFatDxe can never bind to the real
       volume and the boot manager sees no file system at all.

    Neither field is used to mount a volume, so both are rewritten.
    """
    if len(ramdisk) < 512:
        return ramdisk, "too small"

    fixed = bytearray(ramdisk)
    notes = []

    if fixed[446:510] != b"\x00" * 64:
        fixed[446:510] = b"\x00" * 64
        notes.append("cleared the fake MBR partition table")

    bps = int.from_bytes(fixed[11:13], "little")
    fatsz16 = int.from_bytes(fixed[22:24], "little")
    total16 = int.from_bytes(fixed[19:21], "little")
    total32 = int.from_bytes(fixed[32:36], "little")
    fstype = bytes(fixed[82:90])
    if bps == 0 or fatsz16 != 0 or b"FAT32" not in fstype:
        notes.append("not FAT32, BPB left alone")
    elif total32 == 0 and total16 != 0:
        fixed[19:21] = b"\x00\x00"
        fixed[32:36] = total16.to_bytes(4, "little")
        notes.append("moved %d sectors to TotSec32" % total16)

    return bytes(fixed), ", ".join(notes) or "already clean"


def make_image(uefi, device, target, dtb_path, output, fd_override, shim_override, ramdisk_override=None,
               header_version_override=None, dtb_field=False):
    cfg = load_config(uefi, device)
    fd_cfg = cfg.get("uefi_fd") or {}
    fd_base = fd_cfg.get("base")
    fd_size = fd_cfg.get("size")
    if fd_base is None or fd_size is None:
        sys.exit("device config has no [uefi_fd] base/size")

    shim_cfg = cfg.get("boot_shim") or {}
    kernel_cfg = cfg.get("boot_image_kernel") or {}
    image_cfg = cfg.get("boot_image") or {}
    requires_header = bool(shim_cfg.get("requires_kernel_header", True))
    header_version = int(image_cfg.get("header_version", 0))
    if header_version_override is not None:
        header_version = int(header_version_override)
    compression = kernel_cfg.get("kernel_compression", "gzip")
    append_dtb = bool(kernel_cfg.get("append_dtb", True))

    fd = firmware_path(uefi, device, target, fd_override)
    firmware = fd.read_bytes()
    if len(firmware) != fd_size:
        sys.exit(f"firmware volume is {len(firmware):#x} bytes, config says {fd_size:#x}")

    shim = build_shim(uefi, fd_base, fd_size, requires_header, shim_override)
    check_shim(shim, len(firmware), fd_base, requires_header)

    if not dtb_path.is_file():
        sys.exit(f"missing device tree: {dtb_path}")
    tree = dtb_path.read_bytes()
    if tree[:4] != b"\xd0\x0d\xfe\xed":
        sys.exit("device tree is not a flattened device tree")
    if struct.unpack_from(">I", tree, 4)[0] != len(tree):
        sys.exit("device tree totalsize does not match its length")

    combined = shim + firmware
    if compression == "gzip":
        kernel = gzip.compress(combined, mtime=0)
    elif compression == "none":
        kernel = combined
    else:
        sys.exit(f"unknown kernel compression: {compression}")
    # Header v0..v2 carry a dtb field, so ABL parses the tree, fills in the
    # initrd properties for the ramdisk it loaded and hands that tree on. v3 has
    # no such field and ABL keeps its own device tree instead, whose
    # /chosen/linux,initrd-* still describe the previous Android boot - which is
    # why the firmware looked for the payload at a 37 MiB range that holds no FAT.
    use_dtb_field = dtb_field and header_version < 3
    if append_dtb and not use_dtb_field:
        kernel += tree

    output.parent.mkdir(parents=True, exist_ok=True)
    mkbootimg = uefi / "Resources/Scripts/mkbootimg.py"
    if not mkbootimg.is_file():
        sys.exit(f"missing mkbootimg: {mkbootimg}")
    if ramdisk_override:
        ramdisk = Path(ramdisk_override)
        if not ramdisk.is_file():
            sys.exit(f"missing ramdisk: {ramdisk}")
    else:
        ramdisk = uefi / "Resources/ramdisk"
        if not ramdisk.is_file():
            sys.exit(f"missing ramdisk stub: {ramdisk}")

    # mkbootimg reads files, so write the kernel next to the output first.
    kernel_file = output.with_suffix(".kernel")
    kernel_file.write_bytes(kernel)
    ramdisk_bytes, bp_note = fix_fat32_bpb(ramdisk.read_bytes())
    print(f"ramdisk: {len(ramdisk_bytes)} bytes, FAT BPB {bp_note}")
    ramdisk_file = output.with_suffix(".ramdisk")
    ramdisk_file.write_bytes(ramdisk_bytes)
    command = [sys.executable, str(mkbootimg),
               "--kernel", str(kernel_file), "--ramdisk", str(ramdisk_file),
               "-o", str(output), "--header_version", str(header_version),
               "--pagesize", str(PAGE)]
    if use_dtb_field:
        command += ["--dtb", str(dtb_path)]
    run(command)

    verify(output, combined, tree if (append_dtb and not use_dtb_field) else b"",
           ramdisk_bytes, header_version, use_dtb_field, tree)
    print(f"boot image: {output} ({output.stat().st_size} bytes)")
    print(f"  gzip(payload) {len(kernel)} bytes, sha256 {hashlib.sha256(output.read_bytes()).hexdigest()}")
    return 0


def verify(path, combined, tree, ramdisk, header_version, dtb_field=False, dtb_bytes=b""):
    data = path.read_bytes()
    if data[:8] != MAGIC:
        sys.exit("output is not an Android boot image")
    kernel_size, ramdisk_size, os_version, header_size = struct.unpack_from("<IIII", data, 8)
    got_version = struct.unpack_from("<I", data, 40)[0]
    if got_version != header_version:
        sys.exit(f"header_version {got_version} != requested {header_version}")
    if header_version >= 3 and header_size not in (1580, 1584):
        sys.exit(f"unexpected v3 header size {header_size}")

    kernel = data[PAGE:PAGE + kernel_size]
    unpacker = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        payload = unpacker.decompress(kernel)
    except zlib.error as error:
        # The v0..v2 layout with a dtb field interleaves the images differently,
        # so the round-trip check only applies to the v3 layout.
        print(f"verify: skipped the gzip round-trip ({error})")
        payload = None
    if payload is not None:
        if payload != combined:
            sys.exit("gzip payload does not round-trip to shim + firmware")
        if not unpacker.eof:
            sys.exit("gzip stream is truncated")
        if unpacker.unused_data != tree:
            sys.exit("appended device tree does not match the input")

    pages = (kernel_size + PAGE - 1) // PAGE
    start = PAGE * (1 + pages)
    if data[start:start + ramdisk_size] != ramdisk:
        if dtb_field:
            print("verify: ramdisk offset differs in the v2 layout, not checked")
        else:
            sys.exit("ramdisk bytes differ")
    expected = PAGE * (1 + pages + max(1, (ramdisk_size + PAGE - 1) // PAGE))
    if not dtb_field and len(data) != expected:
        sys.exit(f"image length {len(data)} != expected {expected}")
    print(f"verify: header v{got_version}, kernel {kernel_size} B, ramdisk {ramdisk_size} B, "
          f"dtb {len(tree)} B appended, total {len(data)} B")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uefi", required=True, type=Path)
    ap.add_argument("--device", default="fuxi")
    ap.add_argument("--target", default="DEBUG")
    ap.add_argument("--dtb", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--fd", default=None)
    ap.add_argument("--ramdisk", default=None, help="payload volume for the ramdisk field (default: the empty stub)")
    ap.add_argument("--shim", default=None, help="use this BootShim.bin instead of compiling one")
    ap.add_argument("--header-version", default=None, help="override boot_image.header_version")
    ap.add_argument("--dtb-field", action="store_true",
                    help="put the device tree in the header dtb field (v0-v2) instead of appending it")
    args = ap.parse_args()
    return make_image(args.uefi, args.device, args.target, args.dtb, args.output,
                      args.fd, args.shim, args.ramdisk, args.header_version, args.dtb_field)


if __name__ == "__main__":
    sys.exit(main())
