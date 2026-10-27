#!/usr/bin/env python3
"""Enable Apple's open-source HFS as a built-in filesystem in this XNU tree.

Does the six integration points the upstream port needs, adapted to this tree:

  1. stage the pinned hfs-627.100.6 sources into bsd/sweet_hfs (stage-hfs.py)
  2. install the compat header and the IOKit glue replacement from devices/fuxi/hfs
  3. add the 40 source entries to bsd/conf/files as "optional sweet_hfs"
  4. declare "options SWEET_HFS" so those entries are compiled
  5. call sweet_hfs_register() in bsd_init after vfsinit(), before mountroot
  6. make mockfs claim only Mach-O devices, so a device holding a real filesystem is
     left to HFS instead of being served as one big executable

Idempotent: re-running leaves an already enabled tree untouched.
"""
import subprocess
import sys
from pathlib import Path

TREE = Path('/root/UniXNU/out/fuxi/tree/.cache/xnu-work')
REPO = Path('/root/UniXNU')
COMPAT = REPO / 'devices/fuxi/hfs'
CONF_ENTRIES = REPO / 'devices/fuxi/hfs/conf-files.txt'


def read(path):
    return path.read_text()


def write(path, text):
    path.write_text(text)


def already(tree):
    return 'sweet_hfs' in read(tree / 'bsd/conf/files')


def add_conf_files(tree):
    path = tree / 'bsd/conf/files'
    text = read(path)
    if 'sweet_hfs' in text:
        print('conf/files: already present')
        return
    block = read(CONF_ENTRIES).strip('\n')
    lines = text.splitlines(keepends=True)
    insert_at = None
    for index, line in enumerate(lines):
        if 'mockfs_vnops.c' in line:
            insert_at = index + 1
            break
    if insert_at is None:
        raise SystemExit('conf/files: mockfs_vnops.c line not found')
    lines.insert(insert_at, '\n' + block + '\n')
    write(path, ''.join(lines))
    print('conf/files: %d hfs entries added' % block.count('\n'))


def add_config_option(tree):
    """Declare options SWEET_HFS next to options MOCKFS in config/MASTER.

    This tree enables mockfs through a plain options line in config/MASTER rather than
    through the FILESYS_* lists, so HFS follows the same pattern.
    """
    path = tree / 'config/MASTER'
    text = read(path)
    if 'SWEET_HFS' in text:
        print('config: option already present')
        return
    lines = text.splitlines(keepends=True)
    insert_at = None
    for index, line in enumerate(lines):
        if line.lstrip().startswith('options') and 'MOCKFS' in line:
            insert_at = index + 1
            break
    if insert_at is None:
        raise SystemExit('config: no MOCKFS option line found')
    lines.insert(insert_at, 'options\t\tSWEET_HFS\t# Apple OSS HFS built in\t# <sweet_hfs>\n')
    write(path, ''.join(lines))
    print('config: SWEET_HFS option added after MOCKFS')


def add_register_call(tree):
    path = tree / 'bsd/kern/bsd_init.c'
    text = read(path)
    if 'sweet_hfs_register' in text:
        print('bsd_init: already present')
        return
    anchor = 'vfsinit();\n'
    if anchor not in text:
        raise SystemExit('bsd_init: vfsinit anchor not found')
    call = (anchor +
            '#if SWEET_HFS\n'
            '\t/* HFS is built in: register it like its kext would, before mounting root. */\n'
            '\textern void sweet_hfs_register(void);\n'
            '\tsweet_hfs_register();\n'
            '#endif\n')
    write(path, text.replace(anchor, call, 1))
    print('bsd_init: sweet_hfs_register() added after vfsinit()')


def add_mockfs_macho_check(tree):
    path = tree / 'bsd/miscfs/mockfs/mockfs_vfsops.c'
    text = read(path)
    if 'mockfs_check_macho' in text:
        print('mockfs: already patched')
        return
    includes_anchor = '#include <sys/vnode_internal.h>\n'
    if includes_anchor not in text:
        raise SystemExit('mockfs: include anchor not found')
    text = text.replace(includes_anchor,
                        includes_anchor + '#include <sys/buf.h>\n#include <mach-o/loader.h>\n#include <mach-o/fat.h>\n', 1)

    helper = '''
/*
 * mockfs_check_macho:
 *   mockfs serves the whole root device as one executable, so it would claim any device.
 *   Accept only a Mach-O (thin or fat) image, so a device holding a real filesystem is
 *   left to the next filesystem in mountroot instead of being misread as an executable.
 *   Adapted from bangprovn/ios-fuxi's mockfs patch.
 */
static int
mockfs_check_macho(vnode_t rvp)
{
	buf_t bp = NULL;
	uint32_t magic = 0;
	int error;

	error = (int)buf_meta_bread(rvp, 0, DEV_BSIZE, NOCRED, &bp);
	if (!error) {
		memcpy(&magic, (const void *)buf_dataptr(bp), sizeof(magic));
		switch (magic) {
		case MH_MAGIC:
		case MH_CIGAM:
		case MH_MAGIC_64:
		case MH_CIGAM_64:
		case FAT_MAGIC:
		case FAT_CIGAM:
			break;
		default:
			error = EINVAL;
			break;
		}
	}
	if (bp) {
		buf_markinvalid(bp);
		buf_brelse(bp);
	}
	return error;
}
'''
    marker = 'int\nmockfs_mountroot(mount_t mp, vnode_t rvp, __unused vfs_context_t ctx)\n{'
    if marker not in text:
        raise SystemExit('mockfs: mountroot anchor not found')
    text = text.replace(marker, helper + '\n' + marker, 1)

    # call it at the top of mountroot, before the header read
    call_anchor = '	struct mockfs_test_header header = {0};'
    if call_anchor not in text:
        raise SystemExit('mockfs: header declaration anchor not found')
    text = text.replace(call_anchor,
                        call_anchor + '\n\n\t/* A device holding a filesystem is not an executable; let HFS claim it. */\n'
                        '\tif (mockfs_check_macho(rvp) != 0) {\n\t\treturn EINVAL;\n\t}\n', 1)
    write(path, text)
    print('mockfs: Mach-O device check added')


def install_compat(tree):
    target = tree / 'bsd/sweet_hfs'
    if not target.is_dir():
        raise SystemExit('hfs: sources not staged; run stage-hfs.py first')
    for name in ('sweet_hfs_compat.h', 'sweet_hfs_iokit.c'):
        source = COMPAT / name
        if not source.is_file():
            raise SystemExit('hfs: missing %s' % source)
        (target / name).write_text(source.read_text())
    print('compat: header and iokit glue installed')


def main():
    tree = TREE
    if not (tree / 'bsd/kern/bsd_init.c').is_file():
        raise SystemExit('not an xnu tree: %s' % tree)
    install_compat(tree)
    add_conf_files(tree)
    add_config_option(tree)
    add_register_call(tree)
    add_mockfs_macho_check(tree)
    print('enable-hfs: done')
    return 0


if __name__ == '__main__':
    sys.exit(main())
