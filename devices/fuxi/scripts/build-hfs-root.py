#!/usr/bin/env python3
"""Build a minimal HFS+ root image for the fuxi bring-up and use it as the data ramdisk.

The kernel has HFS built in, so the data ramdisk can carry a real volume instead of a bare
Mach-O executable: mockfs's Mach-O device check hands a non-executable device to HFS, which
then mounts it as the root.

Plain HFS+ (not the case-sensitive HFSX that mkfs.hfsplus -s creates): the hfsplus
userspace tools used to populate the image only understand "H+" volumes. The image holds the
probe at /sbin/launchd, the path bsd_init execs as process 1.
"""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path('/root/UniXNU')
WORK = Path('/root/fuxi-root')
CLT = Path('/opt/unixnu-linux/CommandLineTools')
NEWLINE = chr(10)


def run(command, **kwargs):
    print('+', ' '.join(str(part) for part in command))
    subprocess.run(command, check=True, **kwargs)


def build_init(source, destination):
    run([str(CLT / 'usr/bin/clang'), '-target', 'arm64-apple-ios17.0', '-nostdlib', '-static',
         '--ld-path=' + str(CLT / 'usr/bin/ld'), '-Wl,-e,__start',
         '-o', str(destination), str(source)])


def build_image(image, size_mib, init_binary):
    image.unlink(missing_ok=True)
    with image.open('wb') as handle:
        handle.truncate(size_mib * 1024 * 1024)
    run(['mkfs.hfsplus', '-v', 'FuxiRoot', str(image)])
    # hpmount asks whether to open the volume for writing; hpcopy/hpmkdir then act on it
    run(['hpmount', str(image)], input='y' + NEWLINE, text=True)
    try:
        run(['hpmkdir', ':sbin'], input='y' + NEWLINE, text=True)
        run(['hpcopy', str(init_binary), ':sbin/launchd'], input='y' + NEWLINE, text=True)
        listing = subprocess.run(['hpls', ':sbin'], capture_output=True, text=True)
        print('image listing:', listing.stdout.strip() or '(empty)')
    finally:
        subprocess.run(['hpumount'], capture_output=True)
    result = subprocess.run(['fsck.hfsplus', '-n', str(image)], capture_output=True, text=True)
    print(NEWLINE.join(result.stdout.splitlines()[-4:]))
    if 'appears to be OK' not in result.stdout:
        raise SystemExit('hfs image failed its own check')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--size-mib', type=int, default=16)
    parser.add_argument('--source', type=Path,
                        default=ROOT / 'devices/fuxi/experiments/init-rootfs-probe.c')
    parser.add_argument('--output', type=Path, default=WORK / 'root.hfs')
    args = parser.parse_args()

    WORK.mkdir(parents=True, exist_ok=True)
    binary = WORK / 'launchd'
    build_init(args.source, binary)
    print('init binary:', binary.stat().st_size, 'bytes')
    build_image(args.output, args.size_mib, binary)
    print('root image:', args.output, args.output.stat().st_size, 'bytes')
    return 0


if __name__ == '__main__':
    sys.exit(main())
