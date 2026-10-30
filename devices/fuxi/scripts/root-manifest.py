#!/usr/bin/env python3
"""Build and verify the root-image manifest that NativeUfs expects at offset 0 of userdata.

Format (from drivers/NativeUfs RootLayout.h, which is the shared production parser):

    offset 0     magic "SHNGROOT" (u32 0x474e4853, u32 0x544f4f52)
    offset 8     version   u32, must be 1
    offset 12    window count u32, 1..4
    offset 16    image bytes u64, the size of the flashed root image
    offset 24    window entries, 128 bytes each
                   +0   name[32], printable ASCII, NUL terminated, unique
                   +32  offset u64, bytes, 4 KiB aligned, >= 4096
                   +40  size   u64, bytes, 512 aligned, non zero
                   +48  block size u32 == 512
                   +52  u32 == 1
                   +56  sha256[32] of the window bytes
    offset 4064  sha256[32] over bytes 0..4063

The GPT itself lives on the device's LUN 0 and is not part of this file: the kernel reads the device
GPT to find the "userdata" partition, then reads its first 4096-byte block as this manifest. So a
"userdata image" here is exactly the partition content, starting with the manifest block.

Usage:
    root-manifest.py build  --out image.bin [--window NAME=PATH]... [--image-bytes N]
    root-manifest.py verify --image image.bin
    root-manifest.py selftest

Exit code 0 on success, 1 on failure.
"""
import argparse
import hashlib
import struct
import sys
import tempfile
from pathlib import Path

BLOCK = 4096
WINDOW_ENTRY = 128
WINDOW_MAX = 4
NAME_MAX = 32


class LayoutError(Exception):
    pass


def crc32_ieee(data, zero_header_crc=False):
    import zlib
    if zero_header_crc:
        data = data[:16] + b"\0\0\0\0" + data[20:]
    return zlib.crc32(data) & 0xFFFFFFFF


def build_windows(specs):
    """specs: list of (name, path). Returns list of dicts sorted into ascending offset order."""
    windows = []
    for name, path in specs:
        raw = Path(path).read_bytes()
        encoded = name.encode("ascii")
        if not encoded or len(encoded) >= NAME_MAX:
            raise LayoutError(f"window name must be 1..{NAME_MAX - 1} printable bytes: {name!r}")
        if any(byte < 0x20 or byte > 0x7E for byte in encoded):
            raise LayoutError(f"window name must be printable ASCII: {name!r}")
        # The on-device parser requires size % 512 == 0 and hashes exactly `size` bytes as they are
        # stored, so the payload is zero padded to a 512-byte multiple before hashing.
        declared = (len(raw) + 511) // 512 * 512
        stored = raw + bytes(declared - len(raw))
        padded = (declared + BLOCK - 1) // BLOCK * BLOCK
        windows.append({
            "name": name,
            "data": stored,
            "size": declared,
            "size_padded": padded,
            "sha256": hashlib.sha256(stored).digest(),
        })
    if not windows:
        raise LayoutError("at least one window is required")
    if len(windows) > WINDOW_MAX:
        raise LayoutError(f"at most {WINDOW_MAX} windows are supported")
    names = [w["name"] for w in windows]
    if len(set(names)) != len(names):
        raise LayoutError("window names must be unique")

    offset = BLOCK          # the manifest itself occupies block 0
    for window in windows:
        window["offset"] = offset
        offset += window["size_padded"]
    return windows, offset


def build_image(specs, out_path, image_bytes=None, pad_to=None):
    windows, total = build_windows(specs)

    # A window may be listed in any order, but offsets must follow file order. Sort by offset.
    windows.sort(key=lambda w: w["offset"])

    manifest = bytearray(BLOCK)
    struct.pack_into("<II", manifest, 0, 0x474e4853, 0x544f4f52)
    struct.pack_into("<IIQ", manifest, 8, 1, len(windows), image_bytes or total)
    for index, window in enumerate(windows):
        base = 24 + WINDOW_ENTRY * index
        name = window["name"].encode("ascii") + b"\0"
        manifest[base:base + len(name)] = name
        struct.pack_into("<QQII", manifest, base + 32, window["offset"], window["size"], 512, 1)
        manifest[base + 56:base + 88] = window["sha256"]
    manifest[4064:4096] = hashlib.sha256(bytes(manifest[:4064])).digest()

    if pad_to is not None:
        if pad_to < total:
            raise LayoutError(f"--pad-to {pad_to} is smaller than the assembled image {total}")
        total = (pad_to + BLOCK - 1) // BLOCK * BLOCK
    image = bytearray(total)
    image[0:BLOCK] = manifest
    for window in windows:
        image[window["offset"]:window["offset"] + window["size"]] = window["data"]
    Path(out_path).write_bytes(image)
    return windows, total


def verify_image(path):
    raw = Path(path).read_bytes()
    if len(raw) < BLOCK or len(raw) % BLOCK:
        raise LayoutError(f"image size {len(raw)} is not a positive multiple of {BLOCK}")
    magic0, magic1, version, count = struct.unpack_from("<IIII", raw, 0)
    if (magic0, magic1) != (0x474e4853, 0x544f4f52):
        raise LayoutError("bad manifest magic (expected SHNGROOT)")
    if version != 1:
        raise LayoutError(f"manifest version {version} != 1")
    if not 1 <= count <= WINDOW_MAX:
        raise LayoutError(f"window count {count} out of range 1..{WINDOW_MAX}")
    image_bytes, = struct.unpack_from("<Q", raw, 16)
    if image_bytes < BLOCK or image_bytes % BLOCK or image_bytes > len(raw):
        raise LayoutError(f"image bytes {image_bytes} inconsistent with file size {len(raw)}")
    digest = hashlib.sha256(raw[:4064]).digest()
    if digest != raw[4064:4096]:
        raise LayoutError("manifest sha256 mismatch")

    windows = []
    for index in range(count):
        base = 24 + WINDOW_ENTRY * index
        name_field = raw[base:base + NAME_MAX]
        name = name_field.split(b"\0", 1)[0]
        if not name or len(name) == NAME_MAX or any(b < 0x20 or b > 0x7E for b in name):
            raise LayoutError(f"window {index}: bad name")
        offset, size, block_size, flag = struct.unpack_from("<QQII", raw, base + 32)
        if block_size != 512 or flag != 1:
            raise LayoutError(f"window {name.decode()}: block size/flag {block_size}/{flag} != 512/1")
        if offset % BLOCK or offset < BLOCK or size % 512 or size == 0:
            raise LayoutError(f"window {name.decode()}: bad offset/size {offset}/{size}")
        if offset >= image_bytes or size > image_bytes - offset:
            raise LayoutError(f"window {name.decode()}: outside image bytes")
        expected = raw[base + 56:base + 88]
        actual = hashlib.sha256(raw[offset:offset + size]).digest()
        if expected != actual:
            raise LayoutError(f"window {name.decode()}: sha256 mismatch")
        padded_end = offset + (size + BLOCK - 1) // BLOCK * BLOCK
        for other in windows:
            other_padded_end = other["offset"] + other["size_padded"]
            if offset < other_padded_end and other["offset"] < padded_end:
                raise LayoutError(f"window {name.decode()} overlaps {other['name']}")
        windows.append({"name": name.decode(), "offset": offset, "size": size,
                        "size_padded": padded_end - offset})
    return {"image_bytes": image_bytes, "windows": windows}


def command_build(args):
    specs = []
    for entry in args.window or []:
        name, _, path = entry.partition("=")
        if not name or not path:
            raise LayoutError(f"--window must be NAME=PATH: {entry!r}")
        specs.append((name, path))
    windows, total = build_image(specs, args.out, args.image_bytes, args.pad_to)
    print(f"built {args.out}: {total} bytes, {len(windows)} window(s)")
    for window in windows:
        print(f"  {window['name']:<12} offset {window['offset']:>10}  size {window['size']:>10}  "
              f"sha256 {window['sha256'].hex()[:16]}")
    return 0


def command_verify(args):
    info = verify_image(args.image)
    print(f"ok {args.image}: image bytes {info['image_bytes']}, {len(info['windows'])} window(s)")
    for window in info["windows"]:
        print(f"  {window['name']:<12} offset {window['offset']:>10}  size {window['size']:>10}")
    return 0


def command_selftest(_args):
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        payload_a = work / "a.bin"
        payload_b = work / "b.bin"
        payload_a.write_bytes(bytes(range(256)) * 64)          # 16 KiB
        payload_b.write_bytes(b"OS-volume" * 1000)             # 9 KiB
        image = work / "image.bin"
        windows, total = build_image([("SystemOS", payload_a), ("OS", payload_b)], image)
        info = verify_image(image)
        assert len(info["windows"]) == 2, info
        assert total % BLOCK == 0
        print(f"selftest: built and verified {total} bytes with 2 windows")

        corrupt = bytearray(image.read_bytes())
        os_window = next(w for w in windows if w["name"] == "OS")
        corrupt[os_window["offset"] + 5] ^= 0xFF
        bad = work / "bad.bin"
        bad.write_bytes(corrupt)
        try:
            verify_image(bad)
        except LayoutError as error:
            print(f"selftest: corrupted payload rejected as expected ({error})")
        else:
            raise AssertionError("corrupted payload was accepted")

        broken = bytearray(image.read_bytes())
        broken[4064] ^= 0xFF
        bad2 = work / "bad2.bin"
        bad2.write_bytes(broken)
        try:
            verify_image(bad2)
        except LayoutError as error:
            print(f"selftest: corrupted manifest rejected as expected ({error})")
        else:
            raise AssertionError("corrupted manifest was accepted")
    print("selftest: ok")
    return 0


def parse_getvar(text):
    """Extract the userdata partition size in bytes from a captured "fastboot getvar" output.

    Accepted forms, as printed by fastboot on various versions:
        (bootloader) partition-size:userdata: 0x1d4c00000
        partition-size:userdata: 0x1d4c00000
        userdata-size: 0x1d4c00000
        userdata-size: 780140544
    """
    import re
    patterns = [
        r'partition[-_]size:\s*userdata\s*:\s*(0x[0-9a-fA-F]+|\d+)',
        r'userdata[-_]size\s*:\s*(0x[0-9a-fA-F]+|\d+)',
        r'partition[-_]size\s*:\s*userdata\s*:\s*(0x[0-9a-fA-F]+|\d+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            value = match.group(1)
            return int(value, 16) if value.lower().startswith('0x') else int(value, 10)
    return None


def command_flash_plan(args):
    """Print the exact flashing and recovery commands for an assembled userdata image.

    The image is the userdata partition content: fastboot writes it from offset 0 of that partition.
    Erasing userdata restores a clean Android data partition, so the flash is reversible without a
    backup - which is the point of keeping the root image there instead of in boot.img.
    """
    path = Path(args.image).resolve()
    if not path.is_file():
        raise LayoutError(f"missing image: {path}")
    raw = path.read_bytes()
    if len(raw) % BLOCK or len(raw) < BLOCK:
        raise LayoutError(f"image size {len(raw)} is not a positive multiple of {BLOCK}")
    info = verify_image(path)
    digest = hashlib.sha256(raw).hexdigest()
    partition = None
    if getattr(args, 'getvar', None):
        capture = Path(args.getvar)
        if not capture.is_file():
            raise LayoutError(f"missing getvar capture: {capture}")
        partition = parse_getvar(capture.read_text(errors='replace'))
        if partition is None:
            raise LayoutError(f"no userdata partition size found in {capture}")
    print(f"image      : {path}")
    print(f"bytes      : {len(raw)}")
    print(f"sha256     : {digest}")
    print(f"manifest   : ok, {len(info['windows'])} window(s), image bytes {info['image_bytes']}")
    if partition is not None:
        fits = len(raw) <= partition
        print(f"partition  : {partition} bytes (userdata)")
        print(f"fits       : {'yes' if fits else 'NO - the image is larger than the partition'}")
        if fits and len(raw) < partition:
            if partition <= 2 * 1024 * 1024 * 1024:
                print(f"pad-to     : {partition}   # keeps the flash aligned with the partition size")
            else:
                print(f"pad-to     : not needed   # the partition is {partition} bytes; padding the "
                      "flash to that size would only make it slower")
        if not fits:
            print("             rebuild with a smaller OS window or use a larger partition image")
    print()
    print("flash (data loss on userdata is expected and reversible):")
    print(f'  fastboot flash userdata "{path}"')
    print()
    print("verify the write landed (first 4 KiB of the partition):")
    print(f'  fastboot getvar userdata-size        # optional: partition size')
    print()
    print("restore a clean data partition:")
    print("  fastboot erase userdata")
    print("  # plus a normal Android boot to let it reformat as ext4/f2fs")
    print()
    print("then boot the kernel that reads it:")
    print("  pwsh -NoProfile -File \"C:/Users/Jason Wu/dsh/test-batch.ps1\" -Image U")


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="assemble a userdata root image")
    build.add_argument("--out", required=True)
    build.add_argument("--window", action="append", metavar="NAME=PATH")
    build.add_argument("--image-bytes", type=int, default=None,
                       help="override the recorded image byte count (defaults to the assembled size)")
    build.add_argument("--pad-to", type=int, default=None,
                       help="zero pad the file to at least this many bytes (a multiple of 4096); "
                            "use the userdata partition size so the flash matches the partition")
    build.set_defaults(func=command_build)

    verify = sub.add_parser("verify", help="validate an image against the on-device parser rules")
    verify.add_argument("--image", required=True)
    verify.set_defaults(func=command_verify)

    selftest = sub.add_parser("selftest", help="build, verify and reject corrupted images")
    selftest.set_defaults(func=command_selftest)

    flash = sub.add_parser("flash-plan", help="print the flashing and recovery commands for an image")
    flash.add_argument("--image", required=True)
    flash.add_argument("--getvar", help="captured output of fastboot getvar partition-size:userdata")
    flash.set_defaults(func=command_flash_plan)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except LayoutError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
