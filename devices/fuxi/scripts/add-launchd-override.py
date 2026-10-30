#!/usr/bin/env python3
"""Add a --launchd override and a --dry-run switch to populate-hfs.py.

The override lets a real init binary (for example launchd taken from an iOS root image) be placed in
the HFS volume instead of the compiled probe. --dry-run builds the transfer ISO and stops before
QEMU, so the plumbing can be checked without starting a second guest.
"""
import sys
from pathlib import Path

target = Path('/root/hfs-populate/populate-hfs.py')
text = target.read_text()

if 'LAUNCHD_OVERRIDE' in text:
    print('override already present')
    sys.exit(0)

if 'import shutil' not in text:
    text = text.replace('import subprocess', 'import shutil\nimport subprocess', 1)

anchor = 'def make_transfer():'
if anchor not in text:
    sys.exit('make_transfer not found')

setup = '''
# A prepared init binary (for example a real launchd from an iOS root image) can replace the probe:
#   python3 populate-hfs.py --launchd /path/to/launchd
# --dry-run builds the transfer ISO and stops before QEMU, which makes the plumbing testable without
# starting a guest.
LAUNCHD_OVERRIDE = None
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


'''
text = text.replace(anchor, setup + anchor, 1)

old_branch = '    if True:  # always rebuild: the probe source changes between rounds'
new_branch = ('    if LAUNCHD_OVERRIDE is not None:\n'
              '        if not LAUNCHD_OVERRIDE.is_file():\n'
              "            raise SystemExit(f'launchd override not found: {LAUNCHD_OVERRIDE}')\n"
              '        shutil.copy2(LAUNCHD_OVERRIDE, launchd)\n'
              "        print('launchd override installed:', launchd, launchd.stat().st_size, 'bytes')\n"
              '    elif True:  # always rebuild: the probe source changes between rounds')
if old_branch not in text:
    sys.exit('probe build branch not found')
text = text.replace(old_branch, new_branch, 1)

# stop before QEMU when --dry-run is set
qemu_anchor = '    subprocess.run([\n        \'qemu-system-aarch64\','
if 'DRY_RUN' in text and 'def main(' in text:
    main_start = text.index('def main(')
    body = text[main_start:]
    marker = 'qemu-system-aarch64'
    qpos = body.find(marker)
    if qpos < 0:
        sys.exit('qemu invocation not found inside main')
    line_start = body.rfind('\n', 0, qpos) + 1
    guard = ('    if DRY_RUN:\n'
             "        print('dry run: transfer ISO built, not starting QEMU')\n"
             "        print('xfer:', XFER.stat().st_size, 'bytes')\n"
             '        return\n')
    body = body[:line_start] + guard + body[line_start:]
    text = text[:main_start] + body

target.write_text(text)
print('populate-hfs.py: --launchd and --dry-run wired')
