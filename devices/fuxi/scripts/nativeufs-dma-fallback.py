#!/usr/bin/env python3
"""Add a low-address DMA fallback to the reference NativeUfs driver.

Why: hostBringUp refuses to continue when the DMA buffer sits above 4 GiB and the controller does not
advertise 64-bit addressing (CAP bit 24). A single kernel allocation can land anywhere, so the honest
fix is to retry a few allocations and keep the first buffer that satisfies the controller, then report
which of the two conditions failed.

This script is the reproducible record of that change to the third-party checkout. It is idempotent: a
file that already contains the marker is left alone.
"""
import sys
from pathlib import Path

TARGET = Path('/root/refs/NativeUFS-fuxi/NativeUfs.cpp')
MARKER = 'Bring-up: the controller may only support 32-bit DMA'
START = '  gHost.dma = IOBufferMemoryDescriptor::inTaskWithOptions ('
END = '    *why = "DMA address/contiguity";\n    return false;\n  }\n'

NEW = '''  /* Bring-up: the controller may only support 32-bit DMA (CAP bit 24 clear). Then the contiguous
   * buffer must lie below 4 GiB, and a single kernel allocation can land anywhere, so try a few
   * times and keep the first buffer that satisfies the controller. */
  const bool dma64 = (read32 (gHost.hc, 0) & 0x01000000) != 0;
  bool dmaRejectedForAddress = false;
  gHost.dma = NULL;
  gHost.virt = NULL;
  gHost.phys = 0;
  {
    IOByteCount chosenContiguous = 0;
    for (unsigned attempt = 0; attempt < 4 && gHost.dma == NULL; ++attempt) {
      IOBufferMemoryDescriptor *candidate = IOBufferMemoryDescriptor::inTaskWithOptions (
        kernel_task, static_cast<IOOptionBits> (kIODirectionInOut) |
        static_cast<IOOptionBits> (kIOMemoryPhysicallyContiguous) |
        static_cast<IOOptionBits> (kIOMemoryMapperNone), kDmaBytes, 4096);
      if (candidate == NULL) {
        break;
      }
      void *candidateVirt = candidate->getBytesNoCopy ();
      IOByteCount candidateContiguous = 0;
      uint64_t candidatePhys = candidate->getPhysicalSegment (0, &candidateContiguous, kIOMemoryMapperNone);
      bool usable = candidateVirt != NULL && candidatePhys != 0 && (candidatePhys & 4095) == 0 &&
          candidateContiguous >= kDmaBytes;
      if (usable && !dma64 && candidatePhys + kDmaBytes > 0x100000000ULL) {
        usable = false;
        dmaRejectedForAddress = true;
      }
      if (!usable) {
        IOLog ("NativeUfs: DMA attempt %u rejected: phys=%p contiguous=%u dma64=%u''' + chr(92) + '''n", attempt,
               (void *)(uintptr_t)candidatePhys, (unsigned)candidateContiguous, (unsigned)dma64);
        candidate->release ();
        continue;
      }
      gHost.dma = candidate;
      gHost.virt = static_cast<uint8_t *> (candidateVirt);
      gHost.phys = candidatePhys;
      chosenContiguous = candidateContiguous;
    }
    gRootDiagnostic.dma = gHost.phys;
    gRootDiagnostic.contiguous = chosenContiguous;
  }
  gHost.lock = IOLockAlloc ();
  if (!gHost.dma || !gHost.lock) {
    *why = dmaRejectedForAddress ? "DMA above 4 GiB with a 32-bit-only controller" : "dma buffer";
    return false;
  }
'''


def main():
    if not TARGET.is_file():
        print(f'missing source: {TARGET}', file=sys.stderr)
        return 1
    text = TARGET.read_text()
    if MARKER in text:
        print(f'DMA fallback already present: {TARGET}')
        return 0
    start = text.find(START)
    if start < 0:
        print('allocation block not found (upstream layout changed?)', file=sys.stderr)
        return 1
    end = text.find(END, start)
    if end < 0:
        print('validation block not found (upstream layout changed?)', file=sys.stderr)
        return 1
    end += len(END)
    TARGET.write_text(text[:start] + NEW + text[end:])
    print(f'DMA fallback installed in {TARGET}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
