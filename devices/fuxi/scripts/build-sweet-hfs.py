#!/usr/bin/env python3
"""Compile Apple's open-source HFS into a kernel archive.

Rather than teaching the xnu config system about a new option, the HFS sources are built
the same way the fuxi platform-power stub is: take the kernel's own C++ compile record for
its flags, swap the source file, and archive the result. The kernel then links the archive
with -force_load, and bsd_init registers the filesystem before mountroot.

Every object is built with -include sweet_hfs_compat.h (the kext view of the headers) plus
the staged include directories, exactly like the upstream Makefile.template does.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path('/root/UniXNU/out/fuxi/tree')
RECORD = ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT/IOService.cpo.json'
HFS_ROOT = ROOT / '.cache/xnu-work/bsd/sweet_hfs'
OUT = Path('/root/UniXNU/build/sweet-hfs')
AR = '/opt/unixnu-linux/CommandLineTools/usr/bin/ar'

OBJECTS = [
    'BTree', 'BTreeAllocate', 'BTreeMiscOps', 'BTreeNodeOps', 'BTreeNodeReserve',
    'BTreeScanner', 'BTreeTreeOps', 'BTreeWrapper', 'CatalogUtilities',
    'FileExtentMapping', 'FileIDsServices', 'MacOSStubs', 'UnicodeWrappers',
    'VolumeAllocation', 'hfs_attrlist', 'hfs_btreeio', 'hfs_catalog', 'hfs_chash',
    'hfs_cnode', 'hfs_cprotect', 'hfs_endian', 'hfs_extents', 'hfs_fsinfo',
    'hfs_hotfiles', 'hfs_journal', 'hfs_link', 'hfs_lookup', 'hfs_notification',
    'hfs_quota', 'hfs_readwrite', 'hfs_resize', 'hfs_search', 'hfs_vfsops',
    'hfs_vfsutils', 'hfs_vnops', 'hfs_xattr', 'rangelist', 'hfs_encodinghint',
    'hfs_encodings', 'sweet_hfs_iokit',
]
# every source directory that holds one of the objects above
SEARCH_DIRS = [HFS_ROOT, HFS_ROOT / 'core', HFS_ROOT / 'hfs_encodings']

ENCODING_OBJECTS = {'hfs_encodinghint', 'hfs_encodings'}


# Switches that only make sense for the C++ record the flags are taken from.
CXX_ONLY_FLAGS = {
    '-xc++', '-std=gnu++2b', '-fapple-kext', '-fsized-deallocation',
    '-fc++-static-destructors=none', '--driver-mode=g++',
}
C_EXTRA = ['-xc', '-std=gnu11']


def strip_cxx_flags(arguments):
    out = []
    for value in arguments:
        if value in CXX_ONLY_FLAGS:
            continue
        out.append(value)
    return out


def command_for(source, destination, extra_cflags):
    record = json.loads(RECORD.read_text().strip().rstrip(','))
    arguments = strip_cxx_flags(record['arguments'])
    command = []
    index = 0
    while index < len(arguments):
        value = arguments[index]
        if value == record['file']:
            command.append(str(source))
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
    command += C_EXTRA + extra_cflags
    return command, record['directory']


def find_source(name):
    for directory in SEARCH_DIRS:
        for suffix in ('.c', '.cpp'):
            candidate = directory / (name + suffix)
            if candidate.is_file():
                return candidate
    return None


def main():
    if not RECORD.is_file():
        raise SystemExit('missing compile record: %s (run a kernel pass first)' % RECORD)
    compat = HFS_ROOT / 'sweet_hfs_compat.h'
    if not compat.is_file():
        raise SystemExit('HFS not staged: run stage-hfs.py then enable-hfs.py')
    OUT.mkdir(parents=True, exist_ok=True)

    failures = 0
    built = []
    log = OUT / 'build.log'
    log.write_text('')
    for name in OBJECTS:
        source = find_source(name)
        if source is None:
            print('%-22s source not found' % name)
            failures += 1
            continue
        destination = OUT / (name + '.o')
        compile_source = source
        if name == 'hfs_encodings':
            # include the converter interface before the implementation, with the iOS guard
            # cleared, because the header body is macOS-only
            wrapper = OUT / 'hfs_encodings_wrapper.c'
            wrapper.write_text(
                '#undef TARGET_OS_IPHONE\n'
                '#include <IOKit/IOLib.h>\n'
                '#include "hfs_encodings.h"\n'
                '#include "hfs_encodings_internal.h"\n'
                '#include "hfs_encodings.c"\n')
            compile_source = wrapper
        extra = ['-include', str(compat), '-I', str(HFS_ROOT), '-I', str(HFS_ROOT / 'core'),
                 '-I', str(HFS_ROOT / 'hfs_encodings'),
                 '-I', str(ROOT / '.cache/xnu-work/bsd'),
                 '-I', str(ROOT / '.cache/xnu-work/libkern'),
                 '-Wno-everything', '-Wno-xnu-typed-allocators', '-Wno-error', '-ferror-limit=200']
        if name in ENCODING_OBJECTS:
            extra.append('-Dhfs_allocated=hfs_encodings_allocated')
        if name == 'hfs_encodings':
            # the kext build gets these declarations from its prefix header
            extra += ['-D__APPLE_API_UNSTABLE=1',
                      '-I', str(HFS_ROOT / 'hfs_encodings')]
        command, directory = command_for(compile_source, destination, extra)
        result = subprocess.run(command, cwd=directory, capture_output=True, text=True,
                                env={'PATH': '/opt/unixnu-linux/bin:/usr/bin:/bin', 'HOME': '/root'})
        with log.open('a') as handle:
            handle.write('### %s\n%s\n%s\n\n' % (name, ' '.join(command), result.stdout + result.stderr))
        errors = [line for line in (result.stdout + result.stderr).splitlines() if 'error:' in line]
        if result.returncode != 0:
            failures += 1
            print('%-22s FAILED (%d errors)  %s' % (name, len(errors), errors[0][:120] if errors else ''))
        else:
            built.append(destination)
            print('%-22s ok' % name)

    if built:
        archive = OUT / 'libSweetHFS.a'
        archive.unlink(missing_ok=True)
        subprocess.run([AR, 'rcs', str(archive)] + [str(path) for path in built], check=True)
        print('archive: %s (%d objects)' % (archive, len(built)))
    print('failed: %d, built: %d, log: %s' % (failures, len(built), log))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
