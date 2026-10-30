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
import shutil
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
      export DEBIAN_FRONTEND=noninteractive
      # The module package's postinst regenerates the initramfs, which takes tens of minutes under
      # emulation; disabling that hook makes the install quick and still runs depmod.
      chmod -x /etc/kernel/postinst.d/initramfs-tools 2>/dev/null || true
      if ! modprobe hfsplus 2>/dev/null; then
        apt-get update -qq || true
        apt-get install -y -qq "linux-modules-extra-$(uname -r)" || echo "MODULES INSTALL FAILED"
        modprobe hfsplus || echo "MODPROBE FAILED"
      fi
      grep hfsplus /proc/filesystems || true
      grep hfsplus /proc/filesystems || true
      mkdir -p /mnt/xfer /mnt/root
      mount -o ro /dev/vdc /mnt/xfer || { echo "XFER MOUNT FAILED"; poweroff -f; }
      mount -t hfsplus /dev/vdd /mnt/root || { echo "HFS MOUNT FAILED"; poweroff -f; }
      cp -a /mnt/xfer/. /mnt/root/ || { echo "COPY FAILED"; poweroff -f; }
      sync
      echo "=== HFS CONTENT ==="
      find /mnt/root -maxdepth 3 | head -50
      ls -l /mnt/root/sbin 2>/dev/null
      echo "=== HASH CHECK ==="
      cd /mnt/root && find . -type f -exec sha256sum {} + | sort || echo "HASH FAILED"
      echo "=== DIRS ==="
      cd /mnt/root && find . -type d | sort
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



# A prepared init binary (for example a real launchd from an iOS root image) can replace the probe:
#   python3 populate-hfs.py --launchd /path/to/launchd
# --dry-run builds the transfer ISO and stops before QEMU, which makes the plumbing testable without
# starting a guest.
LAUNCHD_OVERRIDE = None
# Extra files dropped into the volume: repeated --file SRC:DEST (DEST relative to the
# volume root), for dyld, shared caches, plists and other real root content.
EXTRA_FILES = []
DRY_RUN = False
_argv = sys.argv[1:]
_idx = 0
while _idx < len(_argv):
    if _argv[_idx] == '--launchd' and _idx + 1 < len(_argv):
        LAUNCHD_OVERRIDE = Path(_argv[_idx + 1])
        _idx += 2
        continue
    if _argv[_idx] == '--dry-run':
        DRY_RUN = True
        _idx += 1
        continue
    if _argv[_idx] == '--file' and _idx + 1 < len(_argv):
        spec = _argv[_idx + 1]
        source, _, destination = spec.partition(':')
        if not source or not destination:
            raise SystemExit(f'--file must be SRC:DEST: {spec}')
        EXTRA_FILES.append((Path(source), destination))
        _idx += 2
        continue
    _idx += 1


def make_transfer():
    # Rebuild the transfer tree from scratch so leftovers from an earlier run cannot end up in
    # the volume (which would also confuse the hash comparison below).
    if CODE_DIR.is_dir():
        shutil.rmtree(CODE_DIR)
    CODE_DIR.mkdir(parents=True, exist_ok=True)
    launchd = CODE_DIR / 'sbin' / 'launchd'
    launchd.parent.mkdir(parents=True, exist_ok=True)
    if LAUNCHD_OVERRIDE is not None:
        if not LAUNCHD_OVERRIDE.is_file():
            raise SystemExit(f'launchd override not found: {LAUNCHD_OVERRIDE}')
        shutil.copy2(LAUNCHD_OVERRIDE, launchd)
        print('launchd override installed:', launchd, launchd.stat().st_size, 'bytes')
    elif True:  # always rebuild: the probe source changes between rounds
        subprocess.run([
            '/opt/unixnu-linux/CommandLineTools/usr/bin/clang', '-target', 'arm64-apple-ios17.0',
            '-nostdlib', '-static', '--ld-path=/opt/unixnu-linux/CommandLineTools/usr/bin/ld',
            '-Wl,-e,__start', '-o', str(launchd),
            '/root/UniXNU/devices/fuxi/experiments/init-data.c'], check=True)
    # Root filesystem skeleton: a real launchd needs these paths to exist even before
    # anything is placed inside them.
    SKELETON_DIRS = [
        'sbin', 'usr/lib', 'usr/libexec', 'usr/bin', 'etc',
        'private/var', 'private/preboot', 'private/etc',
        'System/Library/Caches/com.apple.dyld', 'System/Library/LaunchDaemons',
    ]
    for directory in SKELETON_DIRS:
        (CODE_DIR / directory).mkdir(parents=True, exist_ok=True)
    for source, destination in EXTRA_FILES:
        if not source.is_file():
            raise SystemExit(f'--file source missing: {source}')
        target = CODE_DIR / destination.lstrip('/')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        print('dropped:', target, target.stat().st_size, 'bytes')
    (CODE_DIR / 'hello.txt').write_text('fuxi HFS root\n')
    subprocess.run(['genisoimage', '-quiet', '-o', str(XFER), '-R', '-J', str(CODE_DIR)], check=True)
    print('transfer ISO:', XFER.stat().st_size, 'bytes')


def make_seed():
    seed_dir = WORK / 'seed'
    seed_dir.mkdir(exist_ok=True)
    (seed_dir / 'meta-data').write_text('instance-id: fuxi-hfs-populate-17\nlocal-hostname: hfs-pop\n')
    (seed_dir / 'user-data').write_text(USER_DATA)
    subprocess.run(['genisoimage', '-quiet', '-o', str(SEED), '-V', 'cidata', '-R', '-J',
                    str(seed_dir)], check=True)
    print('seed ISO:', SEED.stat().st_size, 'bytes')


def verify_hashes(text):
    """Compare the guest's sha256 inventory with the files we prepared for the volume.

    The guest prints `sha256sum` lines for every file under /mnt/root, so a bad copy (short write,
    wrong offset, filesystem bug) shows up as a mismatch instead of silently producing a volume that
    only fails much later on the device.
    """
    import hashlib
    import re
    pattern = re.compile(r'^([0-9a-f]{64})\s+\./(.+)$')
    checked = 0
    problems = []
    for line in text.splitlines():
        # The serial log prefixes every guest line with "[time] process[pid]: "; keep only the payload.
        payload = line.split(']: ', 1)[-1].strip() if ']: ' in line else line.strip()
        match = pattern.match(payload)
        if not match:
            continue
        digest, relative = match.group(1), match.group(2)
        source = CODE_DIR / relative
        checked += 1
        if not source.is_file():
            problems.append(f'unexpected file in the volume: {relative}')
            continue
        actual = hashlib.sha256(source.read_bytes()).hexdigest()
        if actual != digest:
            problems.append(f'hash mismatch: {relative} (volume {digest[:16]}, source {actual[:16]})')
    prepared = [path for path in CODE_DIR.rglob('*') if path.is_file()] if CODE_DIR.is_dir() else []
    if checked and len(prepared) != checked:
        problems.append(f'volume has {checked} files but {len(prepared)} were prepared')
    if not checked:
        problems.append('no hash lines found in the guest log')
    return checked, problems


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
        result = subprocess.run(command, stdout=out, stderr=subprocess.STDOUT, timeout=2700)
    print('qemu exit:', result.returncode)
    text = SERIAL.read_text(errors='replace')
    for line in text.splitlines():
        if any(marker in line for marker in ('POPULATE DONE', 'HFS MOUNT FAILED', 'COPY FAILED',
                                             'HFS CONTENT', 'MOUNT FAILED', 'hfsplus')):
            print('  guest:', line.strip())
    if 'POPULATE DONE' not in text:
        return 1
    checked, problems = verify_hashes(text)
    print(f'volume hash check: {checked} file(s) compared')
    for problem in problems:
        print('  PROBLEM:', problem)
    if problems:
        return 1
    print('volume hash check: ok')
    return 0


if __name__ == '__main__':
    make_hfs()
    make_transfer()
    make_seed()
    if DRY_RUN:
        print('dry run: transfer ISO and seed built, not starting QEMU')
        print('xfer:', XFER.stat().st_size, 'bytes')
        sys.exit(0)
    sys.exit(qemu())
