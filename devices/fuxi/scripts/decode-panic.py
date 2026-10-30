#!/usr/bin/env python3
"""Decode a fuxi panic line: symbolicate kernel addresses and decode the ESR.

Given the panic text the device shows, this prints:

  * every kernel address in the line resolved to "symbol + offset" using the kernel Mach-O,
  * the ESR split into EC/IL/ISS plus the fault status code and its name,
  * the far/pc/sp relationships that separate an instruction fault from a data fault,
  * the page-in and abort counts recorded by the probes.

Usage:
    python3 decode-panic.py decode --text "fuxi-sigbus: caller=0xfffffff007a2ea40 ..."
    python3 decode-panic.py decode --stdin < panic.txt
    python3 decode-panic.py selftest

Kernel default: /root/UniXNU/out/fuxi/kernel/kernel.macho (override with --kernel).
"""
import argparse
import bisect
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_KERNEL = '/root/UniXNU/out/fuxi/kernel/kernel.macho'
NM = '/opt/unixnu-linux/CommandLineTools/usr/bin/nm'

EC_NAMES = {
    0x15: 'SVC (svc)',
    0x20: 'instruction abort from lower EL',
    0x21: 'instruction abort from same EL',
    0x22: 'PC alignment fault',
    0x24: 'data abort from lower EL',
    0x25: 'data abort from same EL',
    0x26: 'SP alignment fault',
    0x2C: 'trapped FP',
    0x30: 'breakpoint from lower EL',
    0x31: 'breakpoint from same EL',
    0x32: 'software step from lower EL',
    0x33: 'software step from same EL',
    0x34: 'watchpoint from lower EL',
    0x35: 'watchpoint from same EL',
}

FSC_NAMES = {
    0x00: 'address size fault, level 0',
    0x01: 'address size fault, level 1',
    0x02: 'address size fault, level 2',
    0x03: 'address size fault, level 3',
    0x04: 'translation fault, level 0',
    0x05: 'translation fault, level 1',
    0x06: 'translation fault, level 2',
    0x07: 'translation fault, level 3',
    0x08: 'access flag fault, level 0',
    0x09: 'access flag fault, level 1',
    0x0A: 'access flag fault, level 2',
    0x0B: 'access flag fault, level 3',
    0x0D: 'permission fault, level 1',
    0x0E: 'permission fault, level 2',
    0x0F: 'permission fault, level 3',
    0x10: 'synchronous external abort',
    0x11: 'asynchronous external abort',
    0x14: 'synchronous external abort on translation, level 0',
    0x15: 'synchronous external abort on translation, level 1',
    0x16: 'synchronous external abort on translation, level 2',
    0x17: 'synchronous external abort on translation, level 3',
    0x18: 'parity/ECC error',
    0x20: 'alignment fault',
    0x21: 'debug event',
}

ADDRESS_FIELDS = ['pc', 'far', 'sp', 'caller', 'a_pc', 'a_far', 'entry', 'fault_addr', 'addr', 'lr']
HEX_FIELDS = ['a_esr', 'a_code', 'a_type']
DECIMAL_FIELDS = ['pid', 'reads', 'aborts', 'last_off', 'last_resid', 'last_err']


def load_symbols(kernel):
    """Return (addresses, names, error) for the kernel's defined symbols, sorted by address."""
    if not Path(kernel).is_file():
        return None, None, 'missing kernel: %s' % kernel
    try:
        result = subprocess.run([NM, '-g', str(kernel)], capture_output=True, text=True, check=False)
    except OSError as error:
        return None, None, 'cannot run %s: %s' % (NM, error)
    addresses = []
    names = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) != 3:
            continue
        address, kind, name = parts
        # This kernel's Mach-O marks every symbol as a section symbol (S), so accept anything that
        # is defined and only drop undefined entries.
        if kind in ('U', 'u', 'w', 'v', '-'):
            continue
        try:
            addresses.append(int(address, 16))
        except ValueError:
            continue
        names.append(name)
    order = sorted(range(len(addresses)), key=lambda index: addresses[index])
    return [addresses[index] for index in order], [names[index] for index in order], None


def symbolize(addresses, names, value):
    if not addresses:
        return 'no symbols loaded'
    index = bisect.bisect_right(addresses, value) - 1
    if index < 0:
        return '%#x (user-space address, outside kernel text)' % value
    return '%s+%#x (%#x)' % (names[index], value - addresses[index], value)


def decode_esr(esr):
    ec = (esr >> 26) & 0x3F
    il = (esr >> 25) & 1
    iss = esr & 0x1FFFFFF
    lines = ['esr    : %#x  EC=%#04x (%s)  IL=%d' % (esr, ec, EC_NAMES.get(ec, 'unknown'), il)]
    if ec in (0x20, 0x21, 0x24, 0x25):
        fsc = iss & 0x3F
        lines.append('         FSC=%#04x (%s)' % (fsc, FSC_NAMES.get(fsc, 'unknown')))
        if ec in (0x24, 0x25):
            wnr = (iss >> 6) & 1
            lines.append('         WnR=%d (%s)' % (wnr, 'write' if wnr else 'read'))
            lines.append('         S1PTW=%d CM=%d EA=%d FnV=%d'
                         % ((iss >> 7) & 1, (iss >> 8) & 1, (iss >> 9) & 1, (iss >> 10) & 1))
    return lines


def parse_panic(text):
    fields = {}
    for name in ADDRESS_FIELDS:
        # addresses may be printed with or without the 0x prefix
        match = re.search(r'\b%s=(0x[0-9a-fA-F]+|[0-9a-fA-F]{6,16})\b' % name, text)
        if match:
            fields[name] = int(match.group(1), 16)
    numbers = {}
    for name in DECIMAL_FIELDS:
        match = re.search(r'\b%s=(-?\d+|0x[0-9a-fA-F]+)\b' % name, text)
        if match:
            raw = match.group(1)
            numbers[name] = int(raw, 16) if raw.lower().startswith('0x') else int(raw, 10)
    # a_esr / a_code / a_type are printed with %x, so their digits are hex even without a prefix
    for name in HEX_FIELDS:
        match = re.search(r'\b%s=([0-9a-fA-F]+)\b' % name, text)
        if match:
            numbers[name] = int(match.group(1), 16)
    return fields, numbers


def report(text, addresses, names, kernel):
    fields, numbers = parse_panic(text)
    print('kernel : %s' % kernel)
    print('text   : %s' % text.strip())
    print()
    print('addresses:')
    found = False
    for name in ADDRESS_FIELDS:
        if name in fields:
            found = True
            print('  %-8s: %s' % (name, symbolize(addresses, names, fields[name])))
    if not found:
        print('  (none found in the text)')

    if fields.get('pc') and fields.get('far'):
        pc, far = fields['pc'], fields['far']
        if pc == far:
            relation = 'equal'
        elif abs(far - pc) < 0x1000:
            relation = 'far near pc (instruction fetch fault likely)'
        elif 0x16f000000 <= far <= 0x16fffffff:
            relation = 'far in the user stack range (stack mapping fault likely)'
        else:
            relation = 'far far from pc (data access to another mapping)'
        print()
        print('pc/far : %s' % relation)

    if 'a_esr' in numbers:
        print()
        for line in decode_esr(numbers['a_esr']):
            print(line)

    if any(name in numbers for name in ('a_code', 'a_type')):
        print()
        print('abort  : code=%s type=%s (vm_fault result and fault type as the handler saw them)'
              % (numbers.get('a_code', '?'), numbers.get('a_type', '?')))

    if any(name in numbers for name in ('reads', 'last_off', 'last_resid', 'last_err')):
        print()
        print('page-in: reads=%s last_off=%s last_resid=%s last_err=%s'
              % (numbers.get('reads', '?'), numbers.get('last_off', '?'),
                 numbers.get('last_resid', '?'), numbers.get('last_err', '?')))

    if 'aborts' in numbers:
        print('aborts : %s%s' % (numbers['aborts'],
                                 '  (no EL0 abort reached the handler)' if numbers['aborts'] == 0 else ''))


def command_decode(args):
    text = args.text if args.text else sys.stdin.read()
    if not text.strip():
        print('error: empty text', file=sys.stderr)
        return 1
    addresses, names, error = load_symbols(args.kernel)
    if error:
        print('error: %s' % error, file=sys.stderr)
        return 1
    report(text, addresses, names, args.kernel)
    return 0


def command_selftest(args):
    addresses, names, error = load_symbols(args.kernel)
    assert not error, error
    assert addresses, 'no symbols loaded'

    preferred = ('_panic', '_psignal', '_hfs_vnop_read', '_fuxi_trace_mark', '_vm_fault')
    target = None
    for wanted in preferred:
        for name, address in zip(names, addresses):
            if name == wanted:
                target = (name, address)
                break
        if target:
            break
    assert target, 'none of the expected symbols is present'
    name, address = target
    resolved = symbolize(addresses, names, address)
    assert name in resolved, (name, resolved)
    print('selftest: symbolized %s -> %s' % (name, resolved))

    joined = '\n'.join(decode_esr(0x96000045))
    assert 'EC=0x25' in joined, joined
    assert 'translation fault, level 1' in joined, joined
    assert 'WnR=1 (write)' in joined, joined
    print('selftest: ESR 0x96000045 -> level-1 translation fault on a write')

    joined = '\n'.join(decode_esr(0x9600000f))
    assert 'permission fault, level 3' in joined, joined
    print('selftest: ESR 0x9600000f -> level-3 permission data abort')

    line = 'fuxi-sigbus: caller=%#x pid=1 pc=0x1000004c8 far=0x100000fc8 sp=0x16fdfe000 reads=2 last_off=8192 last_resid=0 last_err=0 aborts=1 a_far=100000fc8 a_pc=1000004c8 a_esr=96000045 a_code=2 a_type=3' % address
    fields, numbers = parse_panic(line)
    assert fields['caller'] == address, fields
    assert fields['pc'] == 0x1000004c8, fields
    assert numbers['a_esr'] == 0x96000045, numbers
    assert numbers['aborts'] == 1, numbers
    print('selftest: panic fields parsed (hex a_esr without prefix handled)')
    print('selftest: ok')
    return 0


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest='command', required=True)

    decode = sub.add_parser('decode', help='decode a panic text')
    decode.add_argument('--text')
    decode.add_argument('--kernel', default=DEFAULT_KERNEL)
    decode.set_defaults(func=command_decode)

    self_cmd = sub.add_parser('selftest', help='run the built-in checks')
    self_cmd.add_argument('--kernel', default=DEFAULT_KERNEL)
    self_cmd.set_defaults(func=command_selftest)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
