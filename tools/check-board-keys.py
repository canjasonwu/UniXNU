#!/usr/bin/env python3
"""Reject board.toml keys this UniXNU base does not read.

Why this exists
---------------
A device repository copied from a newer base can carry sections that look authoritative and are
silently ignored here: the TOML parser accepts them, nothing reads them, and the image silently falls
back to the old behaviour. That failure class was real - the sheng repository's board.toml uses
[payload.install], [payload.adt].persistent_data and [os_overrides.*], while this base reads only the
keys listed below.

The read set was derived by grepping the consumers, not by guessing:
  board     config.py (name, example), tree.py (kit)
  ipsw      ipsw.py:92 (url), cli.py:157 (fetch), ipsw.py:118 (build -> cache dir),
            ipsw.py:120-129 (_identity uses product/version/build/device_class/chip_id/board_id),
            plus sha256 for the member hashes
  userdata  userdata.py:55-67 (first_lba, last_lba, lba_bytes, windows[].name/component)
  kernel    kernel.py:80 (archives), kernel.py:155 + ipsw.py:202 (prelink.*)
  firmware  firmware.py:11,18 (config, defines, dsc, fd, packages, patches, preflight, submodules)
  payload   payload.py:90-119 (size_mib, loader, title, timeout, panic_screen, dram_size,
            skip_setup_tool), payload.py:130-145 (adt.*), kernel_args, volume
  bootimg   bootimg.py:62,66,90 (firmware pass-through, image, dtb_node)

Only keys that are truly inert are notes (board.soc/description, kernel.defconfig, bootimg.dtb,
ipsw.local/path); --strict promotes a note to an error.

Usage
    check-board-keys.py devices/fuxi/board.toml [--strict]
Exit code 0 when every key is understood, 1 otherwise, 2 on bad usage.
"""
import sys
import tomllib
import pathlib

TOP_LEVEL = {'board', 'ipsw', 'userdata', 'kernel', 'firmware', 'payload', 'bootimg'}

READ = {
    'board': {'name', 'kit', 'example'},
    'ipsw': {'url', 'fetch', 'sha256', 'product', 'version', 'build', 'device_class', 'chip_id',
             'board_id'},
    'userdata': {'first_lba', 'last_lba', 'lba_bytes', 'windows'},
    'kernel': {'archives', 'prelink'},
    'firmware': {'config', 'dsc', 'fd', 'preflight', 'submodules', 'patches', 'packages',
                 'defines'},
    'payload': {'volume', 'size_mib', 'loader', 'title', 'timeout', 'panic_screen', 'dram_size',
                'skip_setup_tool', 'trust_cache_sha256', 'kernel_args', 'adt'},
    'bootimg': {'image', 'dtb_node'},
}

# Truly inert keys kept for readability; these are the only notes the tool prints.
DOCUMENTED = {
    ('board', 'soc'), ('board', 'description'),
    ('ipsw', 'local'), ('ipsw', 'path'),
    ('kernel', 'defconfig'),     # the CLI names the defconfig: ./unixnu defconfig NAME -D device
    ('bootimg', 'dtb'),          # the boot image takes the DTB from --dtb, not from board.toml
}

NESTED_READ = {
    ('kernel', 'prelink'): {'tool', 'kernel', 'kernelcache', 'kernelcache_sha256', 'slide',
                            'early_security_init', 'roots', 'extra_args'},
    ('payload', 'adt'): {'tool', 'chip_id', 'owned_memory_size', 'root_match_volume_uuid',
                         'cpu_regs', 'psci_method', 'packed_data_ramdisk', 'ipad_identity',
                         'unique_chip_id'},
}

# Keys that belong to the newer schema; the message says what the key needs instead of "unknown".
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
    strict = False
    positional = []
    for argument in argv:
        if argument == '--strict':
            strict = True
        elif argument.startswith('-'):
            print(f'board-keys: unknown option {argument}', file=sys.stderr)
            return 2
        else:
            positional.append(argument)
    if len(positional) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    path = pathlib.Path(positional[0])
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
                notes.append(f'[{section}].{key} is documentation only; this base never reads it')
                continue
            errors.append(f'[{section}].{key} is {describe(key)}')

    for (section, sub), fields in NESTED_READ.items():
        block = data.get(section, {}).get(sub) if isinstance(data.get(section), dict) else None
        if not isinstance(block, dict):
            continue
        for key in sorted(block):
            if key not in fields:
                errors.append(f'[{section}.{sub}].{key} is {describe(key)}')

    for note in notes:
        print('board-keys: note: ' + note, file=sys.stderr)
    if strict:
        errors.extend('note promoted by --strict: ' + note for note in notes)
    if errors:
        for error in errors:
            print('board-keys: ' + error, file=sys.stderr)
        return 1
    print(f'board-keys: ok ({path})')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
