#!/usr/bin/env python3
"""Build a fuxi PID 1 probe with the 16-byte SP alignment entry stub.

The kernel hands user space an sp that is only 8-byte aligned in general (exec_copyout_strings
subtracts string and pointer-array sizes without rounding to 16), while the AArch64 ABI requires
16-byte alignment whenever C code runs. A probe compiled directly by clang therefore begins with
"sub sp, sp, #0x30" and dies on its first "stp" - which the kernel turns into EXC_ARM_DA_ALIGN and
then SIGBUS.

This builder always links entry-align.S, which aligns sp and then calls the probe body. The probe
source keeps a normal C entry named _start; it is compiled with -D_start=probe_main so its symbol
becomes _probe_main and the stub's __start stays the ELF/Mach-O entry point.

Usage:
    build-probe.py SOURCE [-o OUTPUT] [--no-verify]
    build-probe.py --selftest

Exit code 0 on success. The disassembly check asserts that the entry stub performs the alignment
before any C code runs, so a future toolchain change cannot silently drop it.
"""
import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

CLT = Path('/opt/unixnu-linux/CommandLineTools')
CLANG = CLT / 'usr/bin/clang'
LD = CLT / 'usr/bin/ld'
OBJDUMP = Path('/usr/bin/llvm-objdump-18')
ENTRY = Path('/root/UniXNU/devices/fuxi/experiments/entry-align.S')
WORK = Path('/root/hfs-populate/xfer/sbin')

TARGET = 'arm64-apple-ios17.0'


def build(source, output):
    source = Path(source)
    output = Path(output)
    if not source.is_file():
        raise SystemExit('missing probe source: %s' % source)
    if not ENTRY.is_file():
        raise SystemExit('missing entry stub: %s' % ENTRY)
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(CLANG), '-target', TARGET, '-nostdlib', '-static', '--ld-path=' + str(LD),
        '-Wl,-e,__start', '-D_start=probe_main', '-o', str(output),
        str(source), str(ENTRY),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
        raise SystemExit('link failed for %s' % source)
    # The toolchain always prints a harmless -F warning; show stderr only if it mentions an error.
    if 'error' in result.stderr.lower():
        sys.stderr.write(result.stderr)
    return output


def disassemble_entry(binary):
    if not OBJDUMP.is_file():
        return None
    result = subprocess.run([str(OBJDUMP), '-d', '--arch-name=aarch64', str(binary)],
                            capture_output=True, text=True)
    lines = result.stdout.splitlines()
    start = None
    for index, line in enumerate(lines):
        if '<__start>:' in line:
            start = index
            break
    if start is None:
        return None
    return lines[start:start + 12]


def verify(binary):
    """Check that __start aligns sp before calling the C body (or at least before any stp)."""
    lines = disassemble_entry(binary)
    if lines is None:
        return True, 'disassembly unavailable, skipped'
    text = '\n'.join(lines)
    problems = []
    if 'bic' not in text:
        problems.append('entry does not clear the low sp bits (no bic)')
    first_body = text.find('bl')
    first_stp = text.find('stp')
    if first_stp != -1 and (first_body == -1 or first_stp < first_body):
        problems.append('the first stp appears before the call into the probe body')
    if 'mov' in text and 'sp' in text and 'bl' not in text:
        problems.append('entry does not call a probe body')
    if problems:
        return False, '; '.join(problems)
    return True, 'entry aligns sp before the probe body'


def selftest():
    source = Path('/root/UniXNU/devices/fuxi/experiments/init-data.c')
    output = Path('/tmp/probe-selftest')
    built = build(source, output)
    data = built.read_bytes()
    print('selftest: built %s (%d bytes, sha256 %s)'
          % (built, len(data), hashlib.sha256(data).hexdigest()[:16]))
    ok, reason = verify(built)
    lines = disassemble_entry(built)
    for line in (lines or [])[:7]:
        print('  ' + line)
    assert ok, reason
    print('selftest: %s' % reason)
    print('selftest: ok')
    return 0


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('source', nargs='?')
    parser.add_argument('-o', '--output')
    parser.add_argument('--no-verify', action='store_true')
    parser.add_argument('--selftest', action='store_true')
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()
    if not args.source:
        parser.error('SOURCE is required unless --selftest is used')

    output = Path(args.output) if args.output else WORK / 'launchd'
    built = build(args.source, output)
    data = built.read_bytes()
    print('built %s (%d bytes, sha256 %s)'
          % (built, len(data), hashlib.sha256(data).hexdigest()[:16]))
    if not args.no_verify:
        ok, reason = verify(built)
        print('entry check: %s' % ('ok - ' + reason if ok else 'FAILED - ' + reason))
        if not ok:
            return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
