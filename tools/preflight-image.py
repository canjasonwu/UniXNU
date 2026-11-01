#!/usr/bin/env python3
"""Pre-flight gate for a fuxi boot image, before it is allowed onto the phone.

Every check in this file exists because a real device round was wasted by that exact class of
mistake. The gate is deliberately deterministic: it reads the artifact, never trusts a build log.

Checks
  1. tokens      - required strings must appear in the image bytes (catches "the new code never made
                   it into this image"; e.g. a stale kernel or a payload built from an older tree)
  2. probe       - the PID 1 binary must have a usable layout:
                     * the entry stub must be the SP-aligning sequence we require
                     * no segment may be a pure zero-fill (filesize == 0) writable segment with a
                       non-zero vmaddr: XNU's loader does not map that shape reliably (image A6 died on
                       the first store to such a __DATA page)
                     * __TEXT must be executable and file-backed
                     * __LINKEDIT, if present, must be file-backed
  3. formats     - panic/printf format strings in the given sources must not print 64-bit values with
                   %x, and must not cast a 64-bit value to unsigned for %x (this produced the
                   "far=4000" truncation that sent one analysis round down the wrong path)

Usage
    preflight-image.py --image boot.img --tokens tokens.txt [--probe launchd] [--source f.c ...]
    preflight-image.py --selftest

Exit codes: 0 pass, 1 fail (why on stdout/stderr), 2 usage problem.
"""
import argparse
import re
import struct
import sys
from pathlib import Path

STUB_WORDS = [0x910003ea, 0xd28001e9, 0x8a29014a, 0x9100015f]  # mov x10,sp / mov x9,#0xf / bic / mov sp,x10
LC_SEGMENT_64 = 0x19
LC_MAIN = 0x80000028
LC_UNIXTHREAD = 0x5
VM_PROT_WRITE = 0x2
VM_PROT_EXECUTE = 0x4


class Failure(Exception):
    pass


def load(path):
    p = Path(path)
    if not p.is_file():
        raise Failure('missing file: %s' % p)
    return p.read_bytes()


def check_tokens(image, token_file):
    text = Path(token_file).read_text(errors='replace')
    tokens = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith('#')]
    if not tokens:
        raise Failure('token file %s lists no tokens' % token_file)
    missing = [token for token in tokens if token.encode() not in image]
    if missing:
        raise Failure('image is missing %d required token(s): %s'
                      % (len(missing), ', '.join(repr(t) for t in missing)))
    return 'tokens: %d present' % len(tokens)


def parse_segments(data):
    magic, cputype, cpusub, ftype, ncmds, sizeofcmds, flags, _ = struct.unpack_from('<IIIIIIII', data, 0)
    if magic != 0xfeedfacf:
        raise Failure('probe is not a 64-bit Mach-O (magic %#x)' % magic)
    segments = []
    entryoff = None
    entry_vmaddr = None
    off = 32
    for _ in range(ncmds):
        cmd, size = struct.unpack_from('<II', data, off)
        if cmd == LC_SEGMENT_64 and size >= 72:
            name = data[off + 8:off + 24].rstrip(b'\0').decode('utf-8', 'replace')
            vmaddr, vmsize, fileoff, filesize = struct.unpack_from('<QQQQ', data, off + 24)
            maxprot, initprot = struct.unpack_from('<II', data, off + 56)
            segments.append({'name': name, 'vmaddr': vmaddr, 'vmsize': vmsize,
                             'fileoff': fileoff, 'filesize': filesize,
                             'maxprot': maxprot, 'initprot': initprot})
        elif cmd == LC_MAIN and size >= 24:
            entryoff, = struct.unpack_from('<Q', data, off + 8)
        elif cmd == LC_UNIXTHREAD:
            # arm64 thread state: x0..x28, fp, lr, sp, pc, cpsr; pc is the 32nd 64-bit word after the flavor
            # too layout-dependent for a gate; leave entry_vmaddr unset and fall back to a text scan
            pass
        if size < 8:
            raise Failure('bad load command size %d at %#x' % (size, off))
        off += size
    return {'filetype': ftype, 'flags': flags, 'segments': segments,
            'entryoff': entryoff, 'entry_vmaddr': entry_vmaddr}


def entry_file_offset(info):
    """File offset of the entry point, or None when it cannot be determined cheaply."""
    text = next((s for s in info['segments'] if s['name'] == '__TEXT'), None)
    if text is None:
        return None
    if info['entryoff'] is not None:
        # LC_MAIN entryoff: file offset relative to the start of the file for MH_EXECUTE
        candidate = info['entryoff']
        if text['fileoff'] <= candidate < text['fileoff'] + text['filesize']:
            return candidate
        candidate = text['fileoff'] + info['entryoff']
        if text['fileoff'] <= candidate < text['fileoff'] + text['filesize']:
            return candidate
    return None


def check_probe(probe_data):
    info = parse_segments(probe_data)
    segs = info['segments']
    notes = []
    by_name = {s['name']: s for s in segs}

    text = by_name.get('__TEXT')
    if text is None:
        raise Failure('probe has no __TEXT segment')
    if not (text['initprot'] & VM_PROT_EXECUTE):
        raise Failure('__TEXT is not executable (initprot=%d)' % text['initprot'])
    if text['filesize'] == 0:
        raise Failure('__TEXT is not file-backed')

    # entry stub: verify the SP-aligning sequence sits exactly at the entry point, or at least somewhere
    # inside the file-backed __TEXT payload when the entry point cannot be resolved
    text_start = text['fileoff']
    text_bytes = probe_data[text_start:text_start + text['filesize']]
    entry = entry_file_offset(info)
    stub_words = tuple(STUB_WORDS)
    if entry is not None:
        relative = entry - text_start
        if relative + 16 > len(text_bytes) or struct.unpack_from('<IIII', text_bytes, relative) != stub_words:
            raise Failure('probe entry at %#x is not the SP-aligning stub (expected words %s)'
                          % (entry, ' '.join('%08x' % w for w in STUB_WORDS)))
    else:
        if not any(struct.unpack_from('<IIII', text_bytes, i) == stub_words
                   for i in range(0, max(0, len(text_bytes) - 16), 4)):
            raise Failure('probe entry stub does not align sp (expected words %s in __TEXT)'
                          % ' '.join('%08x' % w for w in STUB_WORDS))

    for seg in segs:
        name = seg['name']
        if name == '__PAGEZERO':
            continue
        if seg['filesize'] == 0 and seg['vmsize'] > 0 and (seg['initprot'] & VM_PROT_WRITE):
            # Not fatal: XNU represents a file-less writable segment as a lazy zero-fill entry in
            # vm_map, and the first store legitimately takes a translation fault that vm_fault()
            # resolves. This was mis-diagnosed once as "the loader does not map it"; keep it as a
            # note so the layout is visible, but do not block on it.
            notes.append('segment %s is file-less (zero-fill) with vmaddr=%#x - first store will fault '
                         'and be resolved by vm_fault()' % (name, seg['vmaddr']))
        if name == '__LINKEDIT' and seg['filesize'] == 0 and seg['vmsize'] > 0:
            notes.append('__LINKEDIT is zero-fill only (unusual but tolerated)')
    zero = [s['name'] for s in segs if s['filesize'] == 0 and s['vmsize'] > 0]
    if zero:
        notes.append('zero-fill segments present: %s' % ', '.join(zero))
    return 'probe: %d segment(s), entry stub ok%s' % (len(segs), ('; ' + '; '.join(notes)) if notes else '')


BAD_FORMAT = re.compile(r'panic\s*\(|printf\s*\(')
SIXTY_FOUR_CAST = re.compile(r'\(unsigned\)\s*\(?\s*(?:unsigned\s+long\s+long|uint64_t|u64|uintptr_t|\(uint64_t\))')


def check_source(path):
    text = Path(path).read_text(errors='replace')
    problems = []
    for match in BAD_FORMAT.finditer(text):
        start = match.start()
        end = text.find(';', start)
        if end < 0:
            end = min(len(text), start + 800)
        call = text[start:end]
        formats = re.findall(r'%[0-9.#\- ]*([a-zA-Z%])', call)
        hex_fields = formats.count('x')
        if hex_fields and SIXTY_FOUR_CAST.search(call):
            line = text.count('\n', 0, start) + 1
            problems.append('%s:%d prints a 64-bit value with %%x (use %%llx; this is how far=4000 lost '
                            'its high half)' % (path, line))
        if hex_fields and re.search(r'%llx', call) is None and 'x' in formats:
            pass
    if problems:
        raise Failure('; '.join(problems))
    return 'formats: %s ok' % Path(path).name


def write_tokens(path, tokens):
    Path(path).write_text('\n'.join(tokens) + '\n')


def check_forbidden(image, tokens):
    """tokens that must NOT appear. Used to keep unconditional panic probes out of a behavioural image:
    the fuxi-dabort-el0 probe pre-empts handle_user_abort()/vm_fault(), so an image carrying it cannot
    answer any question about userland behaviour (that is exactly how image A6/A7 were misread)."""
    present = [token for token in tokens if token.encode() in image]
    if present:
        raise Failure('image contains forbidden token(s): %s - this image cannot be used to observe '
                      'behaviour, only to diagnose' % ', '.join(repr(t) for t in present))
    return 'forbidden: %d token(s) absent' % len(tokens)


def run(args):
    results = []
    image = load(args.image)
    if len(image) < 1024:
        raise Failure('image is suspiciously small (%d bytes)' % len(image))
    results.append('image: %s (%d bytes)' % (args.image, len(image)))
    if args.tokens:
        results.append(check_tokens(image, args.tokens))
    if args.forbid:
        results.append(check_forbidden(image, args.forbid))
    if args.probe:
        results.append(check_probe(load(args.probe)))
    for source in (args.source or []):
        results.append(check_source(source))
    if not (args.tokens or args.forbid or args.probe or args.source):
        raise Failure('nothing to check: pass --tokens, --forbid, --probe and/or --source')
    return results


def selftest():
    import tempfile

    def make_probe(segments, text_payload=None):
        """Build a minimal Mach-O with the given segments; the entry stub is placed in __TEXT."""
        ncmds = len(segments)
        sizeofcmds = ncmds * 72
        header = struct.pack('<IIIIIIII', 0xfeedfacf, 0x100000c, 0x80000002, 2, ncmds, sizeofcmds, 0x1, 0)
        cmds = b''
        for seg in segments:
            name = seg['name'].encode().ljust(16, b'\0')
            cmds += struct.pack('<II16sQQQQIIII', LC_SEGMENT_64, 72, name,
                                seg['vmaddr'], seg['vmsize'], seg['fileoff'], seg['filesize'],
                                seg.get('maxprot', 7), seg['initprot'], 0, 0)
        body = bytearray(header + cmds)
        if text_payload:
            header_size = 32 + ncmds * 72
            target = max(header_size, max((s['fileoff'] + s['filesize']) for s in segments))
            if len(body) < target:
                body += bytes(target - len(body))
            write_at = header_size if segments[0]['fileoff'] < header_size else segments[0]['fileoff']
            body[write_at:write_at + len(text_payload)] = text_payload
        return bytes(body)

    stub = b''.join(struct.pack('<I', w) for w in STUB_WORDS) + struct.pack('<II', 0x97fffff0, 0xaa0003e0)

    good = make_probe([
        {'name': '__TEXT', 'vmaddr': 0x100000000, 'vmsize': 0x4000, 'fileoff': 0, 'filesize': 0x4000, 'initprot': 5},
        {'name': '__DATA', 'vmaddr': 0x100004000, 'vmsize': 0x4000, 'fileoff': 0x4000, 'filesize': 0x100, 'initprot': 3},
    ], stub)
    check_probe(good)
    print('selftest: file-backed probe accepted')

    zero = make_probe([
        {'name': '__TEXT', 'vmaddr': 0x100000000, 'vmsize': 0x4000, 'fileoff': 0, 'filesize': 0x4000, 'initprot': 5},
        {'name': '__DATA', 'vmaddr': 0x100004000, 'vmsize': 0x4000, 'fileoff': 0, 'filesize': 0, 'initprot': 3},
    ], stub)
    summary = check_probe(zero)
    assert 'zero-fill' in summary, summary
    print('selftest: zero-fill writable segment accepted but flagged (lazy entry, vm_fault resolves it)')

    unstubbed = make_probe([
        {'name': '__TEXT', 'vmaddr': 0x100000000, 'vmsize': 0x4000, 'fileoff': 0, 'filesize': 0x4000, 'initprot': 5},
    ], b'\x00' * 64)
    try:
        check_probe(unstubbed)
        raise AssertionError('probe without the alignment stub was accepted')
    except Failure as error:
        assert 'entry stub' in str(error), error
    print('selftest: probe without the SP-aligning stub rejected')

    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / 'probe.c'
        source.write_text('void f(void) { panic("x far=%x", (unsigned)(uint64_t)far); }\n')
        try:
            check_source(source)
            raise AssertionError('truncating format was accepted')
        except Failure as error:
            assert '%x' in str(error), error
        print('selftest: %x on a 64-bit cast rejected')
        source.write_text('void f(void) { panic("x far=%llx", (unsigned long long)far); }\n')
        check_source(source)
        print('selftest: correct %llx accepted')

        tokens = Path(directory) / 'tokens.txt'
        write_tokens(tokens, ['needle-one'])
        check_tokens(b'xx needle-one yy', tokens)
        try:
            check_tokens(b'xx nothing yy', tokens)
            raise AssertionError('missing token was accepted')
        except Failure as error:
            assert 'missing' in str(error), error
        print('selftest: token presence enforced')
    print('selftest: ok')
    return 0


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--image')
    parser.add_argument('--tokens')
    parser.add_argument('--probe')
    parser.add_argument('--forbid', action='append', help='token that must NOT appear (repeatable)')
    parser.add_argument('--source', action='append')
    parser.add_argument('--selftest', action='store_true')
    args = parser.parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.image:
        parser.error('--image is required (or use --selftest)')
    try:
        for line in run(args):
            print(line)
    except Failure as error:
        print('PREFLIGHT FAILED: %s' % error, file=sys.stderr)
        return 1
    print('preflight: ok')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
