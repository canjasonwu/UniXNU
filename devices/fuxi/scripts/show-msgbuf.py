#!/usr/bin/env python3
"""Print an XNU msgbuf ring buffer as text.

The kernel console log is a ring: msg_bufx is the write cursor into a fixed buffer, so
the readable text is buffer[bufx:] followed by buffer[:bufx]. This script takes a raw
dump of that buffer and prints it in order.
"""
import sys
from pathlib import Path


def main():
    if len(sys.argv) not in (2, 3):
        raise SystemExit(__doc__ or 'usage: show-msgbuf.py DUMP [BUFX]')
    data = Path(sys.argv[1]).read_bytes()
    cursor = int(sys.argv[2]) if len(sys.argv) == 3 else 0
    if 0 < cursor <= len(data):
        ordered = data[cursor:] + data[:cursor]
    else:
        ordered = data
    text = ''.join(chr(b) if 32 <= b < 127 or b in (9, 10) else '.' for b in ordered)
    print(text.replace('\r', '\n')[:8000])


if __name__ == '__main__':
    main()
