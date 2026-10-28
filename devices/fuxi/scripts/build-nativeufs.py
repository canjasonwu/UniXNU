#!/usr/bin/env python3
"""Compile the reference NativeUfs driver against our kernel's own compile flags.

The point is an honest, offline compatibility check: does the sheng-proven UFS driver
compile against this tree's IOKit headers and ABI, and what does it still need? The
flags come from a real C++ compile record of our kernel build, so include paths and
deployment target match exactly.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path('/root/UniXNU/out/fuxi/tree')
RECORD = ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT/IOService.cpo.json'
SOURCE_DIR = Path('/root/refs/NativeUFS-fuxi')
SOURCE = SOURCE_DIR / 'NativeUfs.cpp'
OUT = Path('/root/refs/NativeUFS-fuxi/build')
OUT.mkdir(parents=True, exist_ok=True)
destination = OUT / 'NativeUfs.o'

if not RECORD.is_file():
    raise SystemExit('missing compile record: %s' % RECORD)
if not SOURCE.is_file():
    raise SystemExit('missing source: %s' % SOURCE)

record = json.loads(RECORD.read_text().strip().rstrip(','))
arguments = record['arguments']
command = []
index = 0
while index < len(arguments):
    value = arguments[index]
    if value == record['file']:
        command.append(str(SOURCE))
    elif value == '-o':
        command.extend(['-o', str(destination)])
        index += 1
    elif value in ('-MF', '-MJ'):
        index += 1
    elif value in ('-MD', '-MP'):
        pass
    else:
        command.append(value)
    index += 1
command[0:0] = []
command = command + ['-I', str(SOURCE_DIR), '-I', '/root/refs/IOStorageFamily', '-I', '/root/refs/iostorage-inc', '-I', '/root/UniXNU/out/fuxi/tree/.cache/fakeroot/usr/include']

environment = {'PATH': '/opt/unixnu-linux/bin:/usr/local/bin:/usr/bin:/bin', 'HOME': '/root'}
result = subprocess.run(command, cwd=record['directory'], capture_output=True,
                        text=True, env=environment)
log = OUT / 'compile.log'
log.write_text(command and (' '.join(command) + '\n\n' + result.stdout + result.stderr))
print('command line written to', log)
if result.returncode:
    print('COMPILE FAILED')
    for line in (result.stdout + result.stderr).splitlines():
        if 'error' in line or 'fatal' in line:
            print(' ', line)
    sys.exit(1)
print('compiled:', destination)

# The kernel link expects the archive under the build tree (build-xnu-4k-baseline.sh:
# UFS_ARCHIVE=$ROOT/build/native-ufs/libNativeUfs.a), not next to the reference sources.
archive_dir = Path('/root/UniXNU/build/native-ufs')
archive_dir.mkdir(parents=True, exist_ok=True)
archive = archive_dir / 'libNativeUfs.a'
archive.unlink(missing_ok=True)
subprocess.run(['/opt/unixnu-linux/CommandLineTools/usr/bin/ar', 'rcs', str(archive), str(destination)],
               check=True)
print('archive:', archive, archive.stat().st_size, 'bytes')
