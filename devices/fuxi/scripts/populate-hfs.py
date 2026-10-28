#!/usr/bin/env python3
"""Populate an HFS+ image on Linux by using a throwaway Linux guest that can mount hfsplus.

Linux on the host cannot mount HFS+: the WSL kernel has no hfsplus module, and the old hfsplus
userspace tools refuse volumes made by current mkfs.hfsplus. This script instead boots an Ubuntu
arm64 cloud image under qemu-system-aarch64 (AAVMF firmware, already installed) with cloud-init
doing the work:

  /dev/vdb  iso9660 transfer medium, holds the files to install
  /dev/vdc  the HFS+ image (raw), mounted read-write inside the guest
  /dev/vdd  seed ISO carrying this cloud-init user-data

The guest mounts the ISO, mounts the HFS+ volume, copies the tree, syncs, lists it (so the serial
log is the evidence), unmounts and powers off. Nothing is downloaded at runtime.
"""
import subprocess
import sys
from pathlib import Path

WORK = Path('/root/hfs-populate')
CLOUD_IMG = WORK / 'noble-arm64.img'
OVERLAY = WORK / 'overlay.qcow2'
VARS = WORK / 'vars.fd'
SEED = WORK / 'seed.iso'
XFER = WORK / 'xfer.iso'
HFS_DISK = WORK / 'root.hfs'
SERIAL = WORK / 'serial.log'
CODE_DIR = WORK / 'xfer'

AAVMF_CODE = Path('/usr/share/AAVMF/AAVMF_CODE.fd')
AAVMF_VARS = Path('/usr/share/AAVMF/AAVMF_VARS.fd')

USER_DATA = '''#cloud-config
write_files:
  - path: /usr/local/bin/populate-hfs.sh
    permissions: '0755'
    content: |
      #!/bin/bash
      set -x
      modprobe hfsplus || true
      grep hfsplus /proc/filesystems || true
      mkdir -p /mnt/xfer /mnt/root
      mount -o ro /dev/vdb /mnt/xfer || { echo "XFER MOUNT FAILED"; poweroff -f; }
      mount -t hfsplus /dev/vdc /mnt/root || { echo "HFS MOUNT FAILED"; poweroff -f; }
      cp -a /mnt/xfer/. /mnt/root/ || { echo "COPY FAILED"; poweroff -f; }
      sync
      echo "=== HFS CONTENT ==="
      find /mnt/root -maxdepth 3 | head -50
      ls -l /mnt/root/sbin 2>/dev/null
      umount /mnt/root || echo "UMOUNT ROOT FAILED"
      umount /mnt/xfer || true
      echo "POPULATE DONE"
      poweroff -f
runcmd:
  - [ /usr/local/bin/populate-hfs.sh ]
power_state:
  mode: poweroff
  timeout: 120
  condition: true
'''


def make_hfs(size_mib=64):
    if HFS_DISK.is_file():
        print('hfs image already exists:', HFS_DISK.stat().st_size)
        return
    with HFS_DISK.open('wb') as handle:
        handle.truncate(size_mib * 1024 * 1024)
    subprocess.run(['mkfs.hfsplus', '-v', 'FuxiRoot', str(HFS_DISK)], check=True)
    print('hfs image created:', HFS_DISK.stat().st_size, 'bytes')


def make_transfer():
    CODE_DIR.mkdir(parents=True, exist_ok=True)
    launchd = CODE_DIR / 'sbin' / 'launchd'
    launchd.parent.mkdir(parents=True, exist_ok=True)
    if not launchd.is_file():
        subprocess.run([
            '/opt/unixnu-linux/CommandLineTools/usr/bin/clang', '-target', 'arm64-apple-ios17.0',
            '-nostdlib', '-static', '--ld-path=/opt/unixnu-linux/CommandLineTools/usr/bin/ld',
            '-Wl,-e,__start', '-o', str(launchd),
            '/root/UniXNU/devices/fuxi/experiments/init-a.c'], check=True)
    (CODE_DIR / 'hello.txt').write_text('fuxi HFS root\n')
    subprocess.run(['genisoimage', '-quiet', '-o', str(XFER), '-R', '-J', str(CODE_DIR)], check=True)
    print('transfer ISO:', XFER.stat().st_size, 'bytes')


def make_seed():
    seed_dir = WORK / 'seed'
    seed_dir.mkdir(exist_ok=True)
    (seed_dir / 'meta-data').write_text('instance-id: fuxi-hfs-populate\nlocal-hostname: hfs-pop\n')
    (seed_dir / 'user-data').write_text(USER_DATA)
    subprocess.run(['genisoimage', '-quiet', '-o', str(SEED), '-V', 'cidata', '-R', '-J',
                    str(seed_dir)], check=True)
    print('seed ISO:', SEED.stat().st_size, 'bytes')


def qemu():
    if not CLOUD_IMG.is_file():
        raise SystemExit('cloud image missing: %s' % CLOUD_IMG)
    if not VARS.is_file():
        subprocess.run(['cp', str(AAVMF_VARS), str(VARS)], check=True)
    subprocess.run(['qemu-img', 'create', '-q', '-f', 'qcow2', '-F', 'qcow2',
                    '-b', str(CLOUD_IMG), str(OVERLAY)], check=True)
    command = [
        'qemu-system-aarch64', '-M', 'virt', '-cpu', 'cortex-a72', '-m', '2048', '-smp', '2',
        '-nographic',
        '-drive', 'if=pflash,format=raw,readonly=on,file=%s' % AAVMF_CODE,
        '-drive', 'if=pflash,format=raw,file=%s' % VARS,
        '-drive', 'file=%s,if=virtio,format=qcow2' % OVERLAY,
        '-drive', 'file=%s,if=virtio,format=raw,readonly=on' % SEED,
        '-drive', 'file=%s,if=virtio,format=raw,readonly=on' % XFER,
        '-drive', 'file=%s,if=virtio,format=raw' % HFS_DISK,
    ]
    print('+', ' '.join(command))
    with SERIAL.open('w') as out:
        result = subprocess.run(command, stdout=out, stderr=subprocess.STDOUT, timeout=1200)
    print('qemu exit:', result.returncode)
    text = SERIAL.read_text(errors='replace')
    for line in text.splitlines():
        if any(marker in line for marker in ('POPULATE DONE', 'HFS MOUNT FAILED', 'COPY FAILED',
                                             'HFS CONTENT', 'MOUNT FAILED', 'hfsplus')):
            print('  guest:', line.strip())
    return 0 if 'POPULATE DONE' in text else 1


if __name__ == '__main__':
    make_hfs()
    make_transfer()
    make_seed()
    sys.exit(qemu())
