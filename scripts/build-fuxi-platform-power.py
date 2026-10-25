#!/usr/bin/env python3
"""Build the fuxi platform-power stub: an ApplePMGR service for AMFI.

AMFI's ConfigurationSettings panics with "AMFI: No PMGR?" when it cannot find a
power manager. sheng solved this with a platform-power component and calls it
from bsd_init before AMFI starts; this builds the fuxi equivalent, an archive of
one object that publishes a stub service.

The compile flags come from an existing C++ record of the kernel build, so the
object matches the kernel's ABI and deployment target exactly.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT/IOService.cpo.json'
SOURCE = ROOT / 'devices/fuxi/platform-power/ShengPlatformPower.cpp'
OUT = ROOT / 'build/sheng-platform-power'

if not RECORD.is_file():
    raise SystemExit('missing compile record: %s (run a kernel pass first)' % RECORD)
if not SOURCE.is_file():
    raise SystemExit('missing source: %s' % SOURCE)

record = json.loads(RECORD.read_text().strip().rstrip(','))
OUT.mkdir(parents=True, exist_ok=True)
destination = OUT / 'ShengPlatformPower.o'

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

result = subprocess.run(command, cwd=record['directory'], capture_output=True, text=True)
(OUT / 'compile.log').write_text(result.stdout + result.stderr)
if result.returncode:
    sys.stderr.write(result.stdout + result.stderr)
    raise SystemExit('ShengPlatformPower did not compile')

archive = OUT / 'libShengPlatformPower.a'
archive.unlink(missing_ok=True)
subprocess.run(['xcrun', 'ar', 'rcs', str(archive), str(destination)], check=True)

nm = '/opt/unixnu-linux/CommandLineTools/usr/bin/nm'
if not Path(nm).is_file():
    nm = subprocess.run(['xcrun', '--find', 'nm'], capture_output=True,
                        text=True, check=True).stdout.strip()
symbols = subprocess.run([nm, '-g', '-j', str(archive)],
                         capture_output=True, text=True, check=True).stdout
if 'sheng_pmgr_run' not in symbols:
    raise SystemExit('sheng_pmgr_run is not in the archive')
print('fuxi platform-power stub built: %s (sheng_pmgr_run exported)' % archive.relative_to(ROOT))
