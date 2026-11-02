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
        if not raw or len(raw) % 512:
            raise LayoutError(f"window {name!r}: size {len(raw)} is not a non-zero multiple of 512")
        padded = (len(raw) + BLOCK - 1) // BLOCK * BLOCK
        windows.append({
            "name": name,
            "data": raw,
            "size": len(raw),
            "size_padded": padded,
            "sha256": hashlib.sha256(raw).digest(),
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


def build_image(specs, out_path, image_bytes=None):
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
        padded = (size + BLOCK - 1) // BLOCK * BLOCK
        if padded > image_bytes - offset:
            raise LayoutError(f"window {name.decode()}: padded size leaves no room inside image bytes")
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


def build_stream(window_files, out_path, image_bytes=None, partition_bytes=None):
    """Build an image from (name, path) pairs, streaming each window instead of buffering it."""
    if not window_files:
        raise LayoutError("at least one window is required")
    if len(window_files) > WINDOW_MAX:
        raise LayoutError(f"at most {WINDOW_MAX} windows are supported")
    layout, names, cursor = [], set(), BLOCK
    for name, path in window_files:
        encoded = name.encode("ascii")
        if not encoded or len(encoded) >= NAME_MAX or any(b < 0x20 or b > 0x7E for b in encoded):
            raise LayoutError(f"window name must be 1..{NAME_MAX - 1} printable bytes: {name!r}")
        if encoded in names:
            raise LayoutError(f"duplicate window name {name!r}")
        names.add(encoded)
        size = Path(path).stat().st_size
        if not size or size % 512:
            raise LayoutError(f"window {name!r}: size {size} is not a non-zero multiple of 512")
        layout.append({"name": name, "encoded": encoded, "path": Path(path), "size": size,
                       "offset": cursor, "size_padded": (size + BLOCK - 1) // BLOCK * BLOCK})
        cursor = (cursor + size + BLOCK - 1) // BLOCK * BLOCK
    total = (cursor + BLOCK - 1) // BLOCK * BLOCK
    if image_bytes is not None:
        if image_bytes < total or image_bytes % BLOCK:
            raise LayoutError("image_bytes must be a 4096 multiple and hold every window")
        total = image_bytes
    if partition_bytes is not None and total > partition_bytes:
        raise LayoutError("image exceeds the userdata partition")
    manifest = bytearray(BLOCK)
    struct.pack_into("<II", manifest, 0, 0x474e4853, 0x544f4f52)
    struct.pack_into("<IIQ", manifest, 8, 1, len(layout), total)
    with Path(out_path).open("wb") as out:
        out.truncate(total)
        for index, window in enumerate(layout):
            base = 24 + WINDOW_ENTRY * index
            manifest[base:base + len(window["encoded"]) + 1] = window["encoded"] + b"\0"
            struct.pack_into("<QQII", manifest, base + 32, window["offset"], window["size"], 512, 1)
            digest = hashlib.sha256()
            out.seek(window["offset"])
            remaining = window["size"]
            with window["path"].open("rb") as source:
                while remaining:
                    chunk = source.read(min(8 << 20, remaining))
                    if not chunk:
                        raise LayoutError(f"window {window['name']!r} is shorter than its size")
                    digest.update(chunk)
                    out.write(chunk)
                    remaining -= len(chunk)
            manifest[base + 56:base + 88] = digest.digest()
            window["sha256"] = digest.digest()
        manifest[4064:4096] = hashlib.sha256(bytes(manifest[:4064])).digest()
        out.seek(0)
        out.write(manifest)
    return layout, total


def verify_image_stream(path):
    """Verify a large image with bounded memory: parse the manifest, then re-hash each window by seek."""
    path = Path(path)
    size = path.stat().st_size
    if size < BLOCK or size % BLOCK:
        raise LayoutError(f"image size {size} is not a positive multiple of {BLOCK}")
    with path.open("rb") as handle:
        manifest = handle.read(BLOCK)
        image_bytes, = struct.unpack_from("<Q", manifest, 16)
        magic0, magic1 = struct.unpack_from("<II", manifest, 0)
        if (magic0, magic1) != (0x474e4853, 0x544f4f52):
            raise LayoutError("bad manifest magic (expected SHNGROOT)")
        version, count = struct.unpack_from("<II", manifest, 8)
        if version != 1 or not 1 <= count <= WINDOW_MAX:
            raise LayoutError(f"manifest version {version} / window count {count} invalid")
        if image_bytes < BLOCK or image_bytes % BLOCK or image_bytes > size:
            raise LayoutError(f"image bytes {image_bytes} inconsistent with file size {size}")
        if hashlib.sha256(manifest[:4064]).digest() != manifest[4064:4096]:
            raise LayoutError("manifest sha256 mismatch")
        windows = []
        for index in range(count):
            base = 24 + WINDOW_ENTRY * index
            name = manifest[base:base + NAME_MAX].split(b"\0", 1)[0]
            offset, wsize, block_size, flag = struct.unpack_from("<QQII", manifest, base + 32)
            if block_size != 512 or flag != 1:
                raise LayoutError(f"window {name!r}: block size/flag {block_size}/{flag} != 512/1")
            if offset % BLOCK or offset < BLOCK or wsize % 512 or not wsize or offset >= image_bytes or wsize > image_bytes - offset:
                raise LayoutError(f"window {name!r}: bad offset/size {offset}/{wsize}")
            handle.seek(offset)
            digest = hashlib.sha256()
            remaining = wsize
            while remaining:
                chunk = handle.read(min(8 << 20, remaining))
                if not chunk:
                    raise LayoutError(f"window {name!r} is shorter than its declared size")
                digest.update(chunk)
                remaining -= len(chunk)
            if digest.digest() != manifest[base + 56:base + 88]:
                raise LayoutError(f"window {name!r}: sha256 mismatch")
            windows.append({"name": name.decode(), "offset": offset, "size": wsize})
        return {"image_bytes": image_bytes, "windows": windows}


def command_build(args):
    specs = []
    for entry in (args.window or []):
        name, _, path = entry.partition("=")
        if not name or not path:
            raise LayoutError(f"--window must be NAME=PATH: {entry!r}")
        specs.append((name, path))
    for entry in getattr(args, "window_file", None) or []:
        name, _, path = entry.partition("=")
        if not name or not path:
            raise LayoutError(f"--window-file must be NAME=PATH: {entry!r}")
        specs.append((name, path))
    if getattr(args, "window_file", None):
        layout, total = build_stream(specs, args.out, args.image_bytes)
        print(f"built {args.out}: {total} bytes, {len(layout)} window(s) (streamed)")
        for window in layout:
            print(f"  {window['name']:<12} offset {window['offset']:>12}  size {window['size']:>12}  "
                  f"sha256 {window['sha256'].hex()[:16]}")
        return 0
    windows, total = build_image(specs, args.out, args.image_bytes)
    print(f"built {args.out}: {total} bytes, {len(windows)} window(s)")
    for window in windows:
        print(f"  {window['name']:<12} offset {window['offset']:>12}  size {window['size']:>12}  "
              f"sha256 {window['sha256'].hex()[:16]}")
    return 0


def command_verify(args):
    info = verify_image_stream(args.image)
    print(f"ok {args.image}: image bytes {info['image_bytes']}, {len(info['windows'])} window(s)")
    for window in info["windows"]:
        print(f"  {window['name']:<12} offset {window['offset']:>12}  size {window['size']:>12}")
    return 0


def _selftest_expect_reject(image, label, fixup=None, change=None):
    """Copy the image, apply a mutation, expect verify_image to reject it."""
    import tempfile
    data = bytearray(image)
    if change is not None:
        change(data)
    with tempfile.NamedTemporaryFile(suffix='.img', delete=False) as handle:
        path = Path(handle.name)
    try:
        path.write_bytes(bytes(data))
        try:
            verify_image(path)
        except LayoutError:
            return label
        raise AssertionError(f"selftest: {label} was accepted")
    finally:
        path.unlink(missing_ok=True)


def command_selftest(_args):
    import tempfile
    import zlib
    checks = 0
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        payload_a = work / "a.bin"
        payload_b = work / "b.bin"
        payload_a.write_bytes(bytes(range(256)) * 64)          # 16384 bytes, 512 aligned
        payload_b.write_bytes(bytes(range(256)) * 40)          # 10240 bytes, 512 aligned
        image_path = work / "image.bin"
        windows, total = build_image([("OS", payload_a), ("SystemOS", payload_b)], image_path)
        info = verify_image(image_path)
        assert len(info["windows"]) == 2, info
        assert total % BLOCK == 0
        assert info["windows"][0]["name"] == "OS"
        assert info["windows"][0]["offset"] == BLOCK
        assert info["windows"][1]["offset"] == BLOCK + len(payload_a.read_bytes())
        checks += 4
        print(f"selftest: built and verified {total} bytes with 2 windows")

        image = image_path.read_bytes()
        rejections = [
            ("payload corruption", bytearray(image) and None, lambda d: d.__setitem__(BLOCK + 5, d[BLOCK + 5] ^ 0xFF)),
            ("bad magic", None, lambda d: d.__setitem__(0, 0)),
            ("bad version", None, lambda d: struct.pack_into("<I", d, 8, 2)),
            ("bad window count", None, lambda d: struct.pack_into("<I", d, 12, 5)),
            ("bad trailer hash", None, lambda d: d.__setitem__(4064, d[4064] ^ 1)),
            ("imageBytes too large", None, lambda d: struct.pack_into("<Q", d, 16, len(d) + 4096)),
            ("window offset unaligned", None, lambda d: struct.pack_into("<Q", d, 24 + 32, 4097)),
            ("window offset zero", None, lambda d: struct.pack_into("<Q", d, 24 + 32, 0)),
            ("window size not 512 aligned", None, lambda d: struct.pack_into("<Q", d, 24 + 40, 513)),
            ("window outside imageBytes", None, lambda d: struct.pack_into("<Q", d, 24 + 40, len(d))),
            ("bad sector size", None, lambda d: struct.pack_into("<I", d, 24 + 48, 4096)),
            ("bad sector flag", None, lambda d: struct.pack_into("<I", d, 24 + 52, 0)),
            ("bad window name", None, lambda d: d.__setitem__(24, 0)),
            ("control byte in name", None, lambda d: d.__setitem__(24, 0x0A)),
            ("name without terminator", None, lambda d: d.__setitem__(slice(24, 56), b"A" * 32)),
            ("window overlap", None, lambda d: struct.pack_into("<Q", d, 24 + WINDOW_ENTRY + 32, BLOCK)),
        ]
        for label, _unused, change in rejections:
            _selftest_expect_reject(image, label, change=change)
            checks += 1
            print(f"selftest: rejected as expected - {label}")

        # builder-side rule: a window that is not 512 aligned must never reach verify_image
        broken = work / "broken.bin"
        broken.write_bytes(b"x" * 100)
        try:
            build_image([("OS", broken)], work / "never.img")
        except LayoutError:
            checks += 1
            print("selftest: rejected as expected - builder refuses a 100-byte window")
        else:
            raise AssertionError("selftest: builder accepted a 100-byte window")

        # a padded window that overruns imageBytes must be rejected (driver status 4)
        _selftest_expect_reject(
            image, "padded window overruns imageBytes",
            change=lambda d: struct.pack_into("<Q", d, 16, BLOCK + len(payload_a.read_bytes()) + 1024))
        checks += 1
        print("selftest: rejected as expected - padded window overruns imageBytes")
    print(f"selftest: {checks} checks passed")
    return 0


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="assemble a userdata root image")
    build.add_argument("--out", required=True)
    build.add_argument("--window", action="append", metavar="NAME=PATH")
    build.add_argument("--window-file", action="append", metavar="NAME=PATH",
                       help="stream the window from a file instead of buffering it (multi-GiB windows)")
    build.add_argument("--image-bytes", type=int, default=None,
                       help="override the recorded image byte count (defaults to the assembled size)")
    build.set_defaults(func=command_build)

    verify = sub.add_parser("verify", help="validate an image against the on-device parser rules")
    verify.add_argument("--image", required=True)
    verify.set_defaults(func=command_verify)

    selftest = sub.add_parser("selftest", help="build, verify and reject corrupted images")
    selftest.set_defaults(func=command_selftest)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except LayoutError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
