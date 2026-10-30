#!/usr/bin/env python3
"""Check an extracted iOS root directory before it is packed into the fuxi HFS volume.

The remaining step toward a real launchd is content: launchd itself, dyld, the dyld shared cache and
the /System skeleton have to go into the volume. This tool answers, offline and in seconds:

  * are the paths launchd needs actually present?
  * is launchd / dyld really an arm64 Mach-O, and which dylinker/dylibs does launchd reference?
  * how large is the intake, and does it fit the RAM payload or must it go through userdata?
  * what is the exact populate command (--launchd / --file arguments)?

Usage:
    check-ios-root.py check --root /path/to/extracted/root [--print-command]
    check-ios-root.py selftest

Exit code 0 when every required file is present and valid, 1 otherwise.
"""
import argparse
import hashlib
import struct
import sys
import tempfile
from pathlib import Path

MH_MAGIC_64 = 0xFEEDFACF
CPU_TYPE_ARM64 = 0x0100000C
LC_LOAD_DYLINKER = 0x0E
LC_ID_DYLINKER = 0x0F
LC_LOAD_DYLIB = 0x0C

NUL = bytes([0])

REQUIRED = [
    ('launchd', 'sbin/launchd', 'the PID 1 the kernel execs'),
    ('dyld', 'usr/lib/dyld', 'the dynamic linker launchd is loaded through'),
    ('shared cache', 'System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64e',
     'libSystem and the system libraries launchd links against'),
]

SKELETON = [
    'sbin', 'usr/lib', 'usr/libexec', 'usr/bin', 'etc',
    'private/var', 'private/preboot', 'private/etc',
    'System/Library/Caches/com.apple.dyld', 'System/Library/LaunchDaemons',
]

# The payload carries the data ramdisk; 160 MiB is the configured payload size and a 256 MiB ramdisk
# already produced a 421 MB boot.img. Above this the userdata route is the only realistic one.
PAYLOAD_LIMIT = 160 * 1024 * 1024


def cstring(data, start):
    end = data.find(NUL, start)
    if end <= start:
        return None
    return data[start:end].decode('utf-8', 'replace')


def read_macho(path):
    """Return (summary, None) for a Mach-O file, or (None, reason)."""
    try:
        data = path.read_bytes()[:4096]
    except OSError as error:
        return None, str(error)
    if len(data) < 32:
        return None, 'shorter than a Mach-O header'
    magic, cputype, cpusubtype, filetype, ncmds, sizeofcmds, flags, _ = struct.unpack_from('<IIIIIIII', data, 0)
    if magic != MH_MAGIC_64:
        return None, 'not a 64-bit little-endian Mach-O (magic %#x)' % magic
    info = {
        'cputype': cputype,
        'cpusubtype': cpusubtype,
        'filetype': filetype,
        'ncmds': ncmds,
        'sizeofcmds': sizeofcmds,
        'flags': flags,
        'dylinker': None,
        'dylibs': [],
    }
    offset = 32
    for _ in range(ncmds):
        if offset + 8 > len(data):
            break
        cmd, cmdsize = struct.unpack_from('<II', data, offset)
        if cmdsize < 8:
            break
        if cmd in (LC_LOAD_DYLINKER, LC_ID_DYLINKER) and cmdsize >= 12:
            name_off, = struct.unpack_from('<I', data, offset + 8)
            name = cstring(data, offset + name_off)
            if name:
                info['dylinker'] = name
        elif cmd == LC_LOAD_DYLIB and cmdsize >= 12:
            name_off, = struct.unpack_from('<I', data, offset + 8)
            name = cstring(data, offset + name_off)
            if name:
                info['dylibs'].append(name)
        offset += cmdsize
    return info, None


def arch_name(cputype, cpusubtype):
    if cputype == CPU_TYPE_ARM64:
        return 'arm64e' if (cpusubtype & 0xFF) == 2 else 'arm64'
    return 'cputype %#x' % cputype


def filesize(path):
    try:
        return path.stat().st_size
    except OSError:
        return 0


DYLD_CACHE_MAGIC = b'dyld_v1'


def shared_cache_notes(base):
    """Return (notes, problems) for a dyld shared cache file and its numbered shards."""
    notes = []
    problems = []
    try:
        with base.open('rb') as handle:
            header = handle.read(16)
    except OSError as error:
        return notes, ['cannot read shared cache: %s' % error]
    if not header.startswith(DYLD_CACHE_MAGIC):
        problems.append('shared cache does not start with the dyld_v1 magic')
    size = filesize(base)
    if size % 4096:
        problems.append('shared cache size %d is not 4096-aligned' % size)
    parts = sorted(base.parent.glob(base.name + '.*'))
    expected = 1
    for part in parts:
        suffix = part.name[len(base.name) + 1:]
        if not suffix.isdigit():
            problems.append('unexpected shared cache file: %s' % part.name)
            continue
        if int(suffix) != expected:
            problems.append('shared cache shard gap: expected .%02d, found %s' % (expected, part.name))
        expected = int(suffix) + 1
        if filesize(part) % 4096:
            problems.append('shared cache shard %s is not 4096-aligned' % part.name)
    if parts:
        notes.append('%d shard(s) present, contiguous from .01' % len(parts))
    return notes, problems


def check(root):
    report = {'problems': [], 'warnings': [], 'files': {}}

    for label, relative, why in REQUIRED:
        path = root / relative
        if not path.is_file():
            report['problems'].append('missing %s (%s)' % (relative, why))
            continue
        entry = {
            'path': path,
            'size': filesize(path),
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'why': why,
        }
        if relative.endswith('launchd') or relative.endswith('dyld'):
            info, reason = read_macho(path)
            if info is None:
                report['problems'].append('%s: %s' % (relative, reason))
            else:
                entry['arch'] = arch_name(info['cputype'], info['cpusubtype'])
                entry['dylinker'] = info['dylinker']
                entry['dylibs'] = info['dylibs']
                if info['cputype'] != CPU_TYPE_ARM64:
                    report['problems'].append('%s: not arm64 (%s)'
                                              % (relative, arch_name(info['cputype'], info['cpusubtype'])))
        report['files'][label] = entry

    cache = report['files'].get('shared cache')
    if cache:
        base = cache['path']
        parts = sorted(base.parent.glob(base.name + '.*'))
        cache['parts'] = [(part.name, filesize(part)) for part in parts]
        cache['total'] = cache['size'] + sum(size for _name, size in cache['parts'])
        notes, cache_problems = shared_cache_notes(base)
        cache['notes'] = notes
        report['problems'].extend(cache_problems)

    launchd = report['files'].get('launchd')
    if launchd and launchd.get('dylinker'):
        if not launchd['dylinker'].startswith('/usr/lib/dyld'):
            report['warnings'].append('launchd references dylinker %s, not /usr/lib/dyld'
                                      % launchd['dylinker'])
        if not any('/usr/lib/libSystem' in name for name in launchd.get('dylibs', [])):
            report['warnings'].append('launchd does not list libSystem in its dylibs')

    for directory in SKELETON:
        if not (root / directory).is_dir():
            report['warnings'].append('skeleton directory missing (the populate script creates it): '
                                      + directory)

    intake = sum(entry['size'] for entry in report['files'].values())
    if cache:
        intake = intake - cache['size'] + cache.get('total', cache['size'])
    report['intake'] = intake
    return report


def print_report(root, report, print_command):
    print('root      : %s' % root)
    print('intake    : %d bytes (%.1f MiB)' % (report['intake'], report['intake'] / (1024.0 * 1024.0)))
    if report['intake'] > PAYLOAD_LIMIT:
        print('note      : larger than the %d MiB payload limit; this content needs the userdata route'
              % (PAYLOAD_LIMIT // (1024 * 1024)))
    print()
    for label, entry in report['files'].items():
        print('[%s] %s' % (label, entry['path']))
        print('  size    : %d bytes' % entry['size'])
        print('  sha256  : %s' % entry['sha256'])
        if entry.get('arch'):
            print('  arch    : %s' % entry['arch'])
        if entry.get('dylinker'):
            print('  dylinker: %s' % entry['dylinker'])
        if entry.get('dylibs'):
            print('  dylibs  : %d entr(ies)' % len(entry['dylibs']))
            for name in entry['dylibs'][:8]:
                print('            %s' % name)
        if entry.get('parts'):
            print('  parts   : %d extra cache file(s), total %d bytes'
                  % (len(entry['parts']), entry['total']))
        for note in entry.get('notes', []):
            print('  note    : %s' % note)
    if report['problems']:
        print()
        print('problems:')
        for problem in report['problems']:
            print('  - %s' % problem)
    if report['warnings']:
        print()
        print('warnings:')
        for warning in report['warnings']:
            print('  - %s' % warning)

    if print_command and report['files'].get('launchd'):
        print()
        print('populate command:')
        lines = ['  --launchd %s' % report['files']['launchd']['path']]
        cache = report['files'].get('shared cache')
        if cache:
            lines.append('  --file %s:/System/Library/Caches/com.apple.dyld/%s'
                         % (cache['path'], cache['path'].name))
            for name, _size in cache.get('parts', []):
                lines.append('  --file %s:/System/Library/Caches/com.apple.dyld/%s'
                             % (cache['path'].parent / name, name))
        dyld = report['files'].get('dyld')
        if dyld:
            lines.append('  --file %s:/usr/lib/dyld' % dyld['path'])
        print('  cd /root/hfs-populate && python3 populate-hfs.py \\')
        print(' \\\n'.join(lines))
    print()
    print('result    : %s' % ('FAIL' if report['problems'] else 'ok'))


def command_check(args):
    root = Path(args.root)
    if not root.is_dir():
        print('error: not a directory: %s' % root, file=sys.stderr)
        return 1
    report = check(root)
    print_report(root, report, args.print_command)
    return 1 if report['problems'] else 0


def fake_macho(path, with_dylibs=True):
    """Write a minimal but correctly laid out 64-bit Mach-O with dylinker and dylib commands."""
    dylinker = b'/usr/lib/dyld' + NUL
    dylib = b'/usr/lib/libSystem.B.dylib' + NUL
    commands = []
    if with_dylibs:
        cmd = bytearray(12 + len(dylinker))
        struct.pack_into('<III', cmd, 0, LC_LOAD_DYLINKER, len(cmd), 12)
        cmd[12:12 + len(dylinker)] = dylinker
        commands.append(bytes(cmd))
        cmd2 = bytearray(24 + len(dylib))
        struct.pack_into('<IIIIII', cmd2, 0, LC_LOAD_DYLIB, len(cmd2), 24, 0, 0x10000, 0x10000)
        cmd2[24:24 + len(dylib)] = dylib
        commands.append(bytes(cmd2))
    total = sum(len(cmd) for cmd in commands)
    header = struct.pack('<IIIIIIII', MH_MAGIC_64, CPU_TYPE_ARM64, 2, 2, len(commands), total, 0, 0)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + b''.join(commands))


def command_selftest(_args):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        fake_macho(root / 'sbin/launchd')
        fake_macho(root / 'usr/lib/dyld', with_dylibs=False)
        cache = root / 'System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64e'
        cache.parent.mkdir(parents=True, exist_ok=True)
        # A cache has to carry the dyld_v1 magic and be page aligned, and its shards follow the same
        # rule; the selftest data must satisfy that or the new shard checks fire on purpose.
        header = b'dyld_v1   arm64e'
        cache.write_bytes(header + bytes(4096 - len(header)))
        (cache.parent / 'dyld_shared_cache_arm64e.01').write_bytes(bytes(4096))

        report = check(root)
        assert not report['problems'], report['problems']
        assert report['files']['launchd']['dylinker'] == '/usr/lib/dyld', report['files']['launchd']
        assert report['files']['shared cache']['total'] > report['files']['shared cache']['size']
        print('selftest: valid root accepted (intake %d bytes)' % report['intake'])

        (root / 'usr/lib/dyld').unlink()
        broken = check(root)
        assert broken['problems'], 'missing dyld was not reported'
        print('selftest: missing file rejected (%s)' % broken['problems'][0])

        (root / 'usr/lib/dyld').write_bytes(b'not a mach-o')
        broken2 = check(root)
        assert broken2['problems'], 'invalid dyld was not reported'
        print('selftest: invalid Mach-O rejected (%s)' % broken2['problems'][0])
    print('selftest: ok')
    return 0


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest='command', required=True)

    check_cmd = sub.add_parser('check', help='validate an extracted iOS root directory')
    check_cmd.add_argument('--root', required=True)
    check_cmd.add_argument('--print-command', action='store_true',
                           help='print the populate command for the files that were found')
    check_cmd.set_defaults(func=command_check)

    self_cmd = sub.add_parser('selftest', help='run the built-in checks')
    self_cmd.set_defaults(func=command_selftest)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
