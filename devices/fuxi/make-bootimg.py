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


def make_image(uefi, device, target, dtb_path, output, fd_override, shim_override):
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
    if append_dtb:
        kernel += tree

    output.parent.mkdir(parents=True, exist_ok=True)
    mkbootimg = uefi / "Resources/Scripts/mkbootimg.py"
    if not mkbootimg.is_file():
        sys.exit(f"missing mkbootimg: {mkbootimg}")
    ramdisk = uefi / "Resources/ramdisk"
    if not ramdisk.is_file():
        sys.exit(f"missing ramdisk stub: {ramdisk}")

    # mkbootimg reads files, so write the kernel next to the output first.
    kernel_file = output.with_suffix(".kernel")
    kernel_file.write_bytes(kernel)
    run([sys.executable, str(mkbootimg),
         "--kernel", str(kernel_file), "--ramdisk", str(ramdisk),
         "-o", str(output), "--header_version", str(header_version)])

    verify(output, combined, tree if append_dtb else b"", ramdisk.read_bytes(), header_version)
    print(f"boot image: {output} ({output.stat().st_size} bytes)")
    print(f"  gzip(payload) {len(kernel)} bytes, sha256 {hashlib.sha256(output.read_bytes()).hexdigest()}")
    return 0


def verify(path, combined, tree, ramdisk, header_version):
    data = path.read_bytes()
    if data[:8] != MAGIC:
        sys.exit("output is not an Android boot image")
    kernel_size, ramdisk_size, os_version, header_size = struct.unpack_from("<IIII", data, 8)
    got_version = struct.unpack_from("<I", data, 40)[0]
    if got_version != header_version:
        sys.exit(f"header_version {got_version} != requested {header_version}")
    if header_size not in (1580, 1584):
        sys.exit(f"unexpected v3 header size {header_size}")

    kernel = data[PAGE:PAGE + kernel_size]
    unpacker = zlib.decompressobj(16 + zlib.MAX_WBITS)
    payload = unpacker.decompress(kernel)
    if payload != combined:
        sys.exit("gzip payload does not round-trip to shim + firmware")
    if not unpacker.eof:
        sys.exit("gzip stream is truncated")
    if unpacker.unused_data != tree:
        sys.exit("appended device tree does not match the input")

    pages = (kernel_size + PAGE - 1) // PAGE
    start = PAGE * (1 + pages)
    if data[start:start + ramdisk_size] != ramdisk:
        sys.exit("ramdisk bytes differ")
    expected = PAGE * (1 + pages + max(1, (ramdisk_size + PAGE - 1) // PAGE))
    if len(data) != expected:
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
    ap.add_argument("--shim", default=None, help="use this BootShim.bin instead of compiling one")
    args = ap.parse_args()
    return make_image(args.uefi, args.device, args.target, args.dtb, args.output,
                      args.fd, args.shim)


if __name__ == "__main__":
    sys.exit(main())
