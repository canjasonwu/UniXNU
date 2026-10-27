#!/usr/bin/env python3
"""Stage Apple's open-source HFS for a built-in filesystem in this XNU tree.

Mirrors the upstream approach (bangprovn/ios-fuxi, 0026-stage-hfs.sh): the sources are
fetched at build time from a pinned commit into bsd/sweet_hfs and never committed here,
so the Apple copyright headers stay intact and the tree does not carry third-party code.

Two source edits are applied, both mechanical:
  * hfs_iokit.h includes <AppleKeyStore/AppleKeyStoreFSServices.h> for key wrapping, which
    this kernel does not provide. The include and the hfs_unwrap_key block are guarded by
    CONFIG_PROTECT, matching how the kernel gates content protection.
  * TargetConditionals.h is copied in because the kernel builds with -nostdinc and HFS's
    kext-config.h needs the target macros.

Usage: stage-hfs.py [--tree PATH] [--commit SHA]
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

HFS_REPO = 'https://github.com/apple-oss-distributions/hfs'
# hfs-627.100.6: the release Apple pairs with xnu-8796.141.3. Pinned by commit, never a branch.
HFS_COMMIT = '31d5ec238dfab485e78a9a30b8b92d13e8914f03'
DEST = Path('bsd/sweet_hfs')
SOURCES = ('core', 'hfs_encodings')


def stage(tree, commit, work):
    target = tree / DEST
    if target.exists():
        print('hfs: %s already staged' % DEST)
        return 0
    if not (tree / 'bsd' / 'kern' / 'bsd_init.c').is_file():
        raise SystemExit('hfs: %s does not look like an xnu tree' % tree)

    work.mkdir(parents=True, exist_ok=True)
    subprocess.run(['git', 'init', '-q', str(work)], check=True)
    subprocess.run(['git', '-C', str(work), 'fetch', '-q', '--depth', '1', HFS_REPO, commit],
                   check=True)
    subprocess.run(['git', '-C', str(work), 'checkout', '-q', 'FETCH_HEAD'], check=True)
    head = subprocess.run(['git', '-C', str(work), 'rev-parse', 'HEAD'],
                          capture_output=True, text=True, check=True).stdout.strip()
    if head != commit:
        raise SystemExit('hfs: fetched %s, expected %s' % (head, commit))

    for name in SOURCES:
        shutil.copytree(work / name, target / name)

    # key-wrapping API is only reachable with content protection built in
    iokit_h = target / 'core' / 'hfs_iokit.h'
    text = iokit_h.read_text()
    include = '#include <AppleKeyStore/AppleKeyStoreFSServices.h>\n'
    first = 'int hfs_unwrap_key('
    end = '__END_DECLS'
    if text.count(include) != 1 or text.count(first) != 1 or text.count(end) != 1:
        raise SystemExit('hfs: %s no longer matches the expected layout' % iokit_h)
    text = text.replace(include, '#if CONFIG_PROTECT\n' + include + '#endif\n')
    i, j = text.index(first), text.index(end)
    text = text[:i] + '#if CONFIG_PROTECT\n' + text[i:j].rstrip('\n') + '\n#endif\n\n' + text[j:]
    iokit_h.write_text(text)

    # -nostdinc kernel builds still need the SDK's target macros
    candidates = [
        Path('/opt/unixnu-linux/CommandLineTools/SDKs/MacOSX.sdk/usr/include/TargetConditionals.h'),
        Path('/root/UniXNU/out/fuxi/tree/.cache/fakeroot/usr/include/TargetConditionals.h'),
    ]
    for candidate in candidates:
        if candidate.is_file():
            shutil.copy2(candidate, target / 'TargetConditionals.h')
            break
    else:
        raise SystemExit('hfs: no TargetConditionals.h found to stage')

    print('hfs: staged %s at %s' % (commit, DEST))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tree', type=Path, default=Path('/root/UniXNU/out/fuxi/tree/.cache/xnu-work'))
    parser.add_argument('--commit', default=HFS_COMMIT)
    parser.add_argument('--work', type=Path, default=Path('/root/refs/hfs-checkout'))
    args = parser.parse_args()
    return stage(args.tree, args.commit, args.work)


if __name__ == '__main__':
    sys.exit(main())
