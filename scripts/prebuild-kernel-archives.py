#!/usr/bin/env python3
"""Pre-build the kernel archives the legacy kernel script checks with test -f.

`unixnu build kernel` runs scripts/build-xnu-4k-baseline.sh once as a bootstrap
pass to produce the XNU compile records, and only afterwards runs the board's
[kernel] archives builders. That order deadlocks on a clean checkout: the script
stops at `test -f <archive>` before it compiles anything, so no compile record is
ever produced and the step aborts.

This runs the same builders beforehand so the bootstrap pass can get through the
existence checks. Builders that genuinely need the compile records still fail
here and are retried by unixnu after the records exist; a failure here is only
reported, never fatal.
"""
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFCONFIG = ROOT / 'devices/fuxi/configs/fuxi_defconfig'
BOARD = ROOT / 'devices/fuxi/board.toml'


def config_env():
    """CONFIG_XNU_* of the defconfig become XNU_* environment variables."""
    env = {}
    for line in DEFCONFIG.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        key = key.strip()
        if not key.startswith('CONFIG_XNU_'):
            continue
        name = key[len('CONFIG_'):]
        value = value.strip()
        if value in ('y', 'Y'):
            value = '1'
        elif value in ('n', 'N'):
            value = '0'
        elif len(value) >= 2 and value[0] == '"' and value[-1] == '"':
            value = value[1:-1]
        env[name] = value.replace('{tree}', str(ROOT))
    return env


def archive_entries():
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib
    board = tomllib.loads(BOARD.read_text(encoding='utf-8'))
    return board.get('kernel', {}).get('archives', [])


def toolchain_path():
    parts = [d for d in ('/opt/homebrew/opt/llvm/bin', '/opt/homebrew/opt/lld/bin',
                         '/usr/local/opt/llvm/bin', '/usr/local/opt/lld/bin')
             if Path(d).is_dir()]
    parts.append('/usr/bin')
    parts.append('/bin')
    return ':'.join(parts)


def main():
    env = dict(__import__('os').environ)
    env.update(config_env())
    env['PATH'] = toolchain_path() + ':' + env.get('PATH', '')

    entries = archive_entries()
    print('prebuild: %d archive builders' % len(entries), flush=True)
    failures = 0
    for index, entry in enumerate(entries, 1):
        parts = shlex.split(entry)
        script = ROOT / 'scripts' / parts[0]
        if not script.is_file():
            print('prebuild %d/%d: %s missing, skipped' % (index, len(entries), parts[0]), flush=True)
            continue
        print('prebuild %d/%d: %s' % (index, len(entries), entry), flush=True)
        result = subprocess.run([sys.executable, str(script), *parts[1:]],
                                cwd=str(ROOT), env=env, capture_output=True, text=True)
        if result.returncode != 0:
            failures += 1
            tail = (result.stderr or result.stdout or '').strip().splitlines()[-4:]
            print('prebuild %d: failed rc=%d' % (index, result.returncode), flush=True)
            for line in tail:
                print('    ' + line[:200], flush=True)
        else:
            print('prebuild %d: ok' % index, flush=True)

    print('prebuild: %d of %d builders failed (expected for those needing compile records)'
          % (failures, len(entries)), flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
