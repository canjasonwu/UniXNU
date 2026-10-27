#!/usr/bin/env python3
"""Offline preflight of a fuxi payload image against DarwinBoot's decision path.

drivers/DarwinBoot is a submodule of this repository, so every reason the loader
can bounce straight back to its menu is computable before flashing. That failure
mode is the least informative one (no kernel output at all), which is why it has
to be caught here instead of on the phone.

Checks, each mirroring DarwinBoot.c:
  ParseKernel   Mach-O sanity, segment overlap, entry inside a segment, alignment,
                base/end within the window
  ReadFile      kernel and .adt exist and stay below 128 MiB / 16 MiB
  AdtNode       the tree must parse exactly, property names NUL terminated, no
                duplicate name property, counts consistent
  ramdisk       PackedRAMDisk requires a packed ramdisk, RAMDisk requires the
                loader to resolve an offset, a ramdisk needs a property and a
                property needs a ramdisk
  trustcache    file and TrustCache property must agree

Usage: validate-payload.py <payload.img>
Exit 0 when the loader would accept the image, 1 with the first reason.
"""
import os
import struct
import sys
from pathlib import Path

WINDOW_SIZE = 0x1C000000          # board.toml owned_memory_size, 448 MiB
SIZE_2MB = 0x200000
ADT_MAX = 0x10000
MAX_SEGMENTS = 32


def u32(data, at):
    return struct.unpack_from('<I', data, at)[0]


def u64(data, at):
    return struct.unpack_from('<Q', data, at)[0]


class Reject(Exception):
    pass


def parse_kernel(data):
    if len(data) < 32 or u32(data, 0) != 0xFEEDFACF:
        raise Reject('kernel is not a 64-bit Mach-O')
    ncmds = u32(data, 16)
    offset = 32
    segments = []
    entry = None
    for _ in range(ncmds):
        if offset + 8 > len(data):
            raise Reject('load command runs past the file')
        cmd, cmdsize = struct.unpack_from('<II', data, offset)
        if cmdsize < 8 or offset + cmdsize > len(data):
            raise Reject('bad load command size')
        if cmd == 0x19:
            vmaddr, vmsize, fileoff, filesize = struct.unpack_from('<QQQQ', data, offset + 24)
            if len(segments) >= MAX_SEGMENTS:
                raise Reject('too many segments')
            segments.append((vmaddr, vmsize, fileoff, filesize))
        elif cmd == 0x80000005:                            # LC_MAIN
            entry = u64(data, offset + 8)
        elif cmd == 0x5:                                   # LC_UNIXTHREAD
            entry = u64(data, offset + 272)
        offset += cmdsize
    if not segments:
        raise Reject('kernel has no segments')
    if entry is None:
        raise Reject('kernel has no entry point')
    base = min(s[0] for s in segments)
    end = max(s[0] + s[1] for s in segments)
    if not any(s[0] <= entry and entry - s[0] < s[3] for s in segments):
        raise Reject('entry point is outside every segment file data')
    for index, segment in enumerate(segments):
        for other in segments[index + 1:]:
            if segment[0] < other[0] + other[1] and other[0] < segment[0] + segment[1]:
                raise Reject('kernel segments overlap')
    if entry & 3 or base & 0xFFF:
        raise Reject('kernel is misaligned')
    if end - base > WINDOW_SIZE - SIZE_2MB:
        raise Reject('kernel does not fit the window (%.1f > %.1f MiB)'
                     % ((end - base) / 1048576, (WINDOW_SIZE - SIZE_2MB) / 1048576))
    return base, end, entry


def walk_adt(data, offset, depth, parent_path, state, trail):
    size = len(data)
    if depth > 64:
        raise Reject('ADT nests deeper than 64 at %s' % trail)
    if size - offset < 8:
        raise Reject('ADT node header truncated at %s' % trail)
    properties = u32(data, offset)
    children = u32(data, offset + 4)
    offset += 8
    if properties > (size - offset) // 36:
        raise Reject('ADT property count exceeds the tree at %s' % trail)
    name = None
    for _ in range(properties):
        if size - offset < 36:
            raise Reject('ADT property truncated at %s' % trail)
        if data[offset:offset + 32].find(bytes([0])) < 0:
            raise Reject('ADT property name not NUL terminated at %s' % trail)
        key = data[offset:offset + 32].split(bytes([0]))[0].decode('utf-8', 'replace')
        length = u32(data, offset + 32)
        offset += 36
        if length > size - offset:
            raise Reject('ADT property overruns the tree at %s' % trail)
        value_at = offset
        if key == 'name':
            if name is not None:
                raise Reject('duplicate name property at %s' % trail)
            name = value_at
        if key in ('RAMDisk', 'PackedRAMDisk'):
            if key == 'PackedRAMDisk':
                if state['keep_packed']:
                    raise Reject('duplicate PackedRAMDisk at %s' % trail)
                state['keep_packed'] = True
            if state['ramdisk'] is not None or length != 16:
                raise Reject('bad ramdisk property at %s' % trail)
            state['ramdisk'] = value_at
        if key == 'TrustCache':
            if state['trust'] is not None or length != 16:
                raise Reject('bad TrustCache property at %s' % trail)
            state['trust'] = value_at
        if key == 'darwinboot-memory-size':
            if state['memory'] is not None or length != 8:
                raise Reject('bad darwinboot-memory-size at %s' % trail)
            state['memory'] = value_at
        offset += length
        offset = (offset + 3) & ~3
        if offset > size:
            raise Reject('ADT property alignment overruns the tree at %s' % trail)

    path = 3
    if depth == 0:
        path = 0
    if depth == 1 and parent_path == 0 and name == b'chosen':
        path = 1
    if depth == 2 and parent_path == 1 and name == b'memory-map':
        path = 2
    if state['memory'] is not None:
        if path != 1:
            raise Reject('darwinboot-memory-size is outside chosen')
        window = u64(data, state['memory'])
        if not (0x10000000 <= window <= 0x200000000) or window & 0x1FFFFF:
            raise Reject('darwinboot-memory-size 0x%x is not a valid window' % window)
    if children > (size - offset) // 8:
        raise Reject('ADT child count exceeds the tree at %s' % trail)
    for index in range(children):
        offset = walk_adt(data, offset, depth + 1, path, state, trail + '/%d' % index)
    return offset


def read_files(image_path):
    """Extract every file from the payload FAT image with mtools."""
    import subprocess
    import tempfile

    listing = subprocess.run(['mdir', '-/', '-b', '-i', str(image_path), '::'],
                             capture_output=True, text=True)
    if listing.returncode != 0:
        raise Reject('cannot list the payload image: %s' % listing.stderr.strip())
    files = {}
    workdir = Path(tempfile.mkdtemp(prefix='payload-files-'))
    for name in listing.stdout.splitlines():
        name = name.strip().replace('\\', '/').rstrip('/')
        if name.startswith('::'):
            name = name[2:]
        name = name.lstrip('/').rstrip('/')
        if not name:
            continue
        target = workdir / name
        got = subprocess.run(['mcopy', '-n', '-i', str(image_path), '::' + name, str(target)],
                             capture_output=True, text=True)
        if got.returncode != 0 or not target.is_file():
            continue
        files[name] = target.read_bytes()
    return files


def find(files, predicate):
    for name, content in files.items():
        if predicate(name.upper()):
            return name, content
    return None, None


def validate(image_path):
    image = Path(image_path).read_bytes()
    files = read_files(image_path)
    adt_name, adt = find(files, lambda n: n.endswith('ADT'))
    kernel_name, kernel = find(files, lambda n: 'KERNEL' in n and n.endswith('MAC'))
    ramdisk_name, _ = find(files, lambda n: 'SCRATC' in n)
    trust_name, _ = find(files, lambda n: 'TRU' in n and 'KERNEL' in n)
    if kernel is None:
        raise Reject('no kernel.macho in the payload image')
    if adt is None:
        raise Reject('no kernel.macho.adt in the payload image')
    print('  kernel: %s, %.1f MiB' % (kernel_name, len(kernel) / 1048576))
    print('  adt: %s, %d bytes' % (adt_name, len(adt)))
    print('  ramdisk: %s, trustcache: %s' % (ramdisk_name or 'none', trust_name or 'none'))
    parse_kernel(kernel)

    if len(adt) > ADT_MAX:
        raise Reject('ADT is larger than the loader accepts')
    state = {'keep_packed': False, 'ramdisk': None, 'trust': None, 'memory': None}
    end = walk_adt(adt, 0, 0, 0, state, '')
    if end != len(adt):
        raise Reject('ADT parse consumed 0x%x of 0x%x bytes; the loader rejects that'
                     % (end, len(adt)))

    if ramdisk_name:
        if state['ramdisk'] is None:
            raise Reject('a ramdisk is present but the ADT has no RAMDisk property')
        if state['keep_packed']:
            raise Reject('PackedRAMDisk property with a raw (not packed) ramdisk; '
                         'the loader returns EFI_COMPROMISED_DATA')
    elif state['ramdisk'] is not None:
        raise Reject('the ADT has a RAMDisk property but no ramdisk is supplied')
    if (trust_name is not None) != (state['trust'] is not None):
        raise Reject('trust cache file and TrustCache property disagree')
    print('  ADT parsed exactly; ramdisk property=%s packed=%s'
          % (state['ramdisk'] is not None, state['keep_packed']))
    return state


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    try:
        validate(sys.argv[1])
    except Reject as error:
        print('LOADER WOULD REJECT: %s' % error, file=sys.stderr)
        return 1
    print('loader preflight: OK, DarwinBoot would start the kernel')
    return 0


if __name__ == '__main__':
    sys.exit(main())
