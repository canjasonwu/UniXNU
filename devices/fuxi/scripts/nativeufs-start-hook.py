#!/usr/bin/env python3
"""Append a C entry point to the reference NativeUfs source so the kernel can start it directly.

Why: NativeUfs is written as an IOService matched from a kext personality, but this kernel links the
driver into the image and has no personality registry for it. Calling the class directly needs the
class definition, which only the driver's own translation unit has, so the smallest honest change is a
C function appended to that file:

    extern "C" int native_ufs_probe_start(void);

It finds the platform expert device, allocates NativeUfs, attaches and starts it with that provider.
The driver's own boot arguments then decide what it does (for example native-ufs-probe=10 performs the
read-only GPT/manifest check and panics with the layout it found).

The reference checkout is not ours, so this script is the reproducible record of the change: it is
idempotent, refuses to patch a file that already contains the hook, and prints what it did.
"""
import sys
from pathlib import Path

TARGET = Path('/root/refs/NativeUFS-fuxi/NativeUfs.cpp')
MARKER = 'native_ufs_probe_start'

HOOK = r'''

// ---- fuxi bring-up hook -----------------------------------------------------------------------
// Appended by devices/fuxi/scripts/nativeufs-start-hook.py. Finds the platform expert device through
// the registry, then allocates/attaches/starts this driver with it, exactly as a kext match would.
// Returns 1 on start, 0 on a start refusal (the driver logs why), negative stage codes otherwise.

static IOService *
fuxiFindPlatformExpertDevice (IORegistryEntry *entry, unsigned depth)
{
  if (!entry || depth > 32) {
    return nullptr;
  }
  if (IOService *service = OSDynamicCast (IOService, entry)) {
    if (OSDynamicCast (IOPlatformExpertDevice, service)) {
      return service;
    }
  }
  if (OSIterator *children = entry->getChildIterator (gIOServicePlane)) {
    while (IORegistryEntry *child = OSDynamicCast (IORegistryEntry, children->getNextObject ())) {
      if (IOService *found = fuxiFindPlatformExpertDevice (child, depth + 1)) {
        children->release ();
        return found;
      }
    }
    children->release ();
  }
  return nullptr;
}

extern "C" int
native_ufs_probe_start (void)
{
  IORegistryEntry *root = IORegistryEntry::getRegistryRoot ();
  if (!root) {
    return -1;
  }
  IOService *provider = fuxiFindPlatformExpertDevice (root, 0);
  if (!provider) {
    return -2;
  }
  NativeUfs *driver = new NativeUfs;
  if (!driver) {
    return -3;
  }
  if (!driver->init (nullptr)) {
    return -4;
  }
  if (!driver->attach (provider)) {
    return -5;
  }
  if (!driver->start (provider)) {
    return 0;
  }
  IOLog ("NativeUfs: fuxi hook started the driver with provider %s\n",
         provider->getMetaClass ()->getClassName ());
  return 1;
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
    backup = TARGET.with_suffix('.cpp.orig')
    if not backup.exists():
        backup.write_text(text)
    TARGET.write_text(text.rstrip() + '\n' + HOOK)
    print(f'hooked {TARGET} (+{len(HOOK)} bytes); original kept at {backup}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
