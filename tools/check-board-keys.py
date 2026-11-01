#!/usr/bin/env python3
"""Reject board.toml keys this UniXNU base does not read.

Why this exists
---------------
A device repository copied from a newer base can carry sections that look authoritative and are
silently ignored here: the TOML parser accepts them, nothing reads them, and the image silently falls
back to the old behaviour. That failure class was real - the sheng repository's board.toml uses
[payload.install], [payload.adt].persistent_data and [os_overrides.*], while this base reads only the
keys listed below (derived from scripts/unixnu/*.py: config.py, ipsw.py, kernel.py, payload.py,
userdata.py, firmware.py, bootimg.py, tree.py, cli.py).

Two severities:
  * error   - the base never reads the key and it is not in the documented-only list: this is the
              silent-loss case; fix the board or upgrade the base.
  * note    - the key is accepted as documentation only (defconfig name, bootimg.dtb): printed but
              not fatal, because a board that names its defconfig is more readable than one that does
              not. Use --strict to turn notes into errors.

Usage
    check-board-keys.py devices/fuxi/board.toml [--strict]
Exit code 0 when every key is understood, 1 otherwise.
"""
import sys
import tomllib
import pathlib

# Top-level sections the base reads.
TOP_LEVEL = {'board', 'ipsw', 'userdata', 'kernel', 'firmware', 'payload', 'bootimg'}

# Keys the base actually consumes, per section (see the module docstring for the source files).
READ = {
    'board': {'name', 'kit', 'example'},
    'ipsw': {'url', 'fetch', 'sha256'},
    'userdata': {'first_lba', 'last_lba', 'lba_bytes', 'windows'},
    'kernel': {'archives', 'prelink'},
    'firmware': {'config', 'dsc', 'fd', 'preflight', 'submodules', 'patches', 'packages',
                 'defines'},
    'payload': {'volume', 'size_mib', 'loader', 'title', 'timeout', 'panic_screen', 'dram_size',
                'skip_setup_tool', 'trust_cache_sha256', 'kernel_args', 'adt'},
    'bootimg': {'image', 'dtb_node'},
}

# Keys accepted as documentation only: printed as a note, never fatal (unless --strict).
DOCUMENTED = {
    ('board', 'soc'), ('board', 'description'),
    ('ipsw', 'product'), ('ipsw', 'version'), ('ipsw', 'build'), ('ipsw', 'device_class'),
    ('ipsw', 'chip_id'), ('ipsw', 'board_id'), ('ipsw', 'local'), ('ipsw', 'path'),
    ('kernel', 'defconfig'),          # the CLI names the defconfig: ./unixnu defconfig NAME -D device
    ('bootimg', 'dtb'),               # the boot image takes the DTB from --dtb, not from board.toml
}

NESTED_READ = {
    ('kernel', 'prelink'): {'tool', 'kernel', 'kernelcache', 'kernelcache_sha256', 'slide',
                            'early_security_init', 'roots', 'extra_args'},
    ('payload', 'adt'): {'tool', 'chip_id', 'owned_memory_size', 'root_match_volume_uuid',
                         'cpu_regs', 'psci_method', 'packed_data_ramdisk', 'ipad_identity',
                         'unique_chip_id'},
}

NESTED_DOCUMENTED = {
    ('userdata', 'windows'),
    ('firmware', 'defines'),
}

# Keys known to belong to the newer schema; the message says what to do instead of just "unknown".
SCHEMA_V2 = {
    'persistent_data': 'needs ADT support in the fuxi ADT tool',
    'install': 'needs the data-reset kernel argument implemented first',
    'os_overrides': 'needs per-OS override support in the base',
    'components': 'needs the v2 component selection model',
    'device_tree': 'needs the v2 DTS pipeline',
    'config_fragments': 'needs the v2 config-fragment pipeline',
    'patch_series': 'needs the v2 patch-series pipeline',
    'schema_version': 'needs the v2 schema loader',
    'package': 'needs the v2 package model',
    'adt': 'needs the v2 ADT section model',
}


def describe(key):
    if key in SCHEMA_V2:
        return f'schema v2 ({SCHEMA_V2[key]}) and this base ignores it'
    return 'never read by this base'


def main(argv):
    args = [a for a in argv if not a.startswith('--')]
    strict = '--strict' in argv
    if len(args) != 1:
        print(__doc__)
        return 2
    path = pathlib.Path(args[0])
    if not path.is_file():
        print(f'board-keys: missing file {path}', file=sys.stderr)
        return 2
    data = tomllib.loads(path.read_text())
    errors = []
    notes = []

    for key in sorted(data):
        if key not in TOP_LEVEL:
            errors.append(f'[{key}] is {describe(key)}')
            continue
        if not isinstance(data[key], dict):
            errors.append(f'[{key}] must be a table')

    for section, fields in READ.items():
        block = data.get(section)
        if not isinstance(block, dict):
            continue
        for key in sorted(block):
            if key in fields:
                continue
            if (section, key) in DOCUMENTED:
                notes.append(f'[{section}].{key} is documentation only; the base reads it from the '
                             f'command line or ignores it')
                continue
            errors.append(f'[{section}].{key} is {describe(key)}')

    for (section, sub), fields in NESTED_READ.items():
        block = data.get(section, {}).get(sub) if isinstance(data.get(section), dict) else None
        if not isinstance(block, dict):
            continue
        for key in sorted(block):
            if key in fields:
                continue
            if (section, key) in DOCUMENTED or (section, sub) in NESTED_DOCUMENTED:
                notes.append(f'[{section}.{sub}].{key} is documentation only')
                continue
            errors.append(f'[{section}.{sub}].{key} is {describe(key)}')

    if notes:
        for note in notes:
            print('board-keys: note: ' + note, file=sys.stderr)
    if strict and notes:
        errors.extend('note promoted by --strict: ' + note for note in notes)
        notes = []
    if errors:
        for error in errors:
            print('board-keys: ' + error, file=sys.stderr)
        return 1
    print(f'board-keys: ok ({path})')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
