#!/usr/bin/env python3
"""Append a read-only window reader to the reference NativeUfs source.

The driver knows how to bring the host up, parse the root manifest and read 4K blocks, but its root
publication path goes through IOStorageFamily (which this kernel only stubs). For the RAM-root plan we
do not need block devices at all: we need the bytes of one window, verified, in a caller-provided
buffer. That is what this hook does, using the driver's own static helpers.

    extern "C" int native_ufs_read_window(const char *name, void *dst,
                                          unsigned long long maxBytes,
                                          unsigned long long *outBytes);

Return codes: 0 success, -1 host bring-up, -2 allocation, -3 GPT/manifest parse, -4 manifest rejected,
-5 window name not found, -6 size/alignment, -7 read failure, -8 sha256 mismatch.

Idempotent: refuses to patch a file that already contains the hook.
"""
import sys
from pathlib import Path

TARGET = Path('/root/refs/NativeUFS-fuxi/NativeUfs.cpp')
MARKER = 'native_ufs_read_window'

HOOK = r'''

// ---- fuxi read-only window reader --------------------------------------------------------------
// Appended by devices/fuxi/scripts/nativeufs-read-hook.py. Copies one root window into a caller
// buffer and verifies its sha256. Read-only: it never writes storage. Requires hostBringUp() and the
// manifest parser that this translation unit already defines.

extern "C" int
native_ufs_read_window (const char *wanted, void *dst, unsigned long long maxBytes,
    unsigned long long *outBytes)
{
  const char *why = "";
  if (!wanted || !dst) {
    return -6;
  }
  if (outBytes) {
    *outBytes = 0;
  }
  if (!hostBringUp (&why)) {
    IOLog ("NativeUfs: read-window bring-up failed: %s\n", why);
    return -1;
  }
  RootLayout *layout = static_cast<RootLayout *> (IOMalloc (sizeof (RootLayout)));
  if (!layout) {
    return -2;
  }
  if (!findRootLayout (layout, &why)) {
    IOLog ("NativeUfs: read-window layout failed: %s\n", why);
    IOFree (layout, sizeof (RootLayout));
    return -3;
  }
  if (layout->manifestStatus != 0) {
    IOFree (layout, sizeof (RootLayout));
    return -4;
  }
  int index = -1;
  for (unsigned i = 0; i < layout->windows; ++i) {
    if (!strcmp (layout->window[i].name, wanted)) {
      index = static_cast<int> (i);
      break;
    }
  }
  if (index < 0) {
    IOFree (layout, sizeof (RootLayout));
    return -5;
  }
  RootWindow &window = layout->window[index];
  if (window.size > maxBytes || (window.size & 4095) || window.size == 0) {
    IOFree (layout, sizeof (RootLayout));
    return -6;
  }

  uint8_t *out = static_cast<uint8_t *> (dst);
  uint64_t lba = layout->userdataFirst + window.offset / 4096;
  for (uint64_t done = 0; done < window.size; done += kDataMax) {
    uint64_t remaining = window.size - done;
    uint32_t blocks = static_cast<uint32_t> ((remaining < kDataMax ? remaining : kDataMax) / 4096);
    if (!blocks || hostRead4k (0, lba + done / 4096, blocks, out + done) != kIOReturnSuccess) {
      IOFree (layout, sizeof (RootLayout));
      return -7;
    }
  }

  SHA256_CTX context;
  uint8_t digest[32];
  SHA256_Init (&context);
  SHA256_Update (&context, out, static_cast<size_t> (window.size));
  SHA256_Final (digest, &context);
  int result = memcmp (digest, window.sha256, 32) == 0 ? 0 : -8;
  if (outBytes) {
    *outBytes = window.size;
  }
  IOFree (layout, sizeof (RootLayout));
  return result;
}
'''


def main():
    if not TARGET.is_file():
        print(f'missing source: {TARGET}', file=sys.stderr)
        return 1
    text = TARGET.read_text()
    if MARKER in text:
        print(f'already hooked: {TARGET}')
        return 0
    TARGET.write_text(text.rstrip() + '\n' + HOOK)
    print(f'hooked {TARGET} (+{len(HOOK)} bytes)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
