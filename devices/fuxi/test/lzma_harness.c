/* Host reproduction of MdeModulePkg/Library/LzmaCustomDecompressLib.
 *
 * The firmware does not compile the SDK as plain C: UefiLzma.h injects
 *
 *     typedef UINTN size_t;
 *     typedef int   ptrdiff_t;
 *     #define memcpy  CopyMem
 *     #define memmove CopyMem
 *     #define _LZMA_SIZE_OPT
 *
 * _LZMA_SIZE_OPT selects a completely different decoder implementation inside
 * Sdk/C/LzmaDec.c, and memcpy is redirected to EDK2's CopyMem. This harness
 * provides both so the exact firmware configuration can be reproduced on the
 * host.
 *
 * usage: harness payload.bin [scratch-bytes] [dst-offset]
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

#include "7zTypes.h"
#include "LzmaDec.h"

typedef struct {
  ISzAlloc       Functions;
  unsigned char  *Buffer;
  size_t          BufferSize;
} ISzAllocWithData;

/* EDK2 MdePkg/Library/BaseMemoryLib/CopyMem.c semantics, overlap safe. */
void *
CopyMem (
  void        *DestinationBuffer,
  const void  *SourceBuffer,
  size_t       Length
  )
{
  unsigned char        *d8;
  const unsigned char  *s8;

  if (DestinationBuffer == SourceBuffer || Length == 0) {
    return DestinationBuffer;
  }
  if ((const unsigned char *)SourceBuffer > (unsigned char *)DestinationBuffer) {
    d8 = (unsigned char *)DestinationBuffer;
    s8 = (const unsigned char *)SourceBuffer;
    while (Length-- != 0) {
      *d8++ = *s8++;
    }
  } else {
    d8 = (unsigned char *)DestinationBuffer + Length;
    s8 = (const unsigned char *)SourceBuffer + Length;
    while (Length-- != 0) {
      *--d8 = *--s8;
    }
  }
  return DestinationBuffer;
}

static void *
SzAlloc (const ISzAlloc *P, size_t Size)
{
  ISzAllocWithData *d = (ISzAllocWithData *)P;

  if (d->BufferSize >= Size) {
    void *a        = d->Buffer;
    d->Buffer      = (unsigned char *)a + Size;
    d->BufferSize -= Size;
    return a;
  }
  fprintf (stderr, "  SzAlloc FAILED: want %zu, have %zu\n", Size, d->BufferSize);
  return NULL;
}

static void
SzFree (const ISzAlloc *P, void *Address)
{
  (void)P;
  (void)Address;
}

int
main (int argc, char **argv)
{
  FILE              *f;
  long               size;
  unsigned char     *src;
  void              *dstRaw;
  unsigned char     *dst;
  unsigned char     *scr;
  unsigned long long decoded = 0;
  size_t             scratch = 0x10000;
  size_t             dstOff  = 0;
  int                i;
  ISzAllocWithData   a;
  ELzmaStatus        st = LZMA_STATUS_NOT_SPECIFIED;
  SizeT              outLen;
  SizeT              inLen;
  SRes               res;

  if (argc < 2) {
    fprintf (stderr, "usage: %s payload.bin [scratch-bytes] [dst-offset]\n", argv[0]);
    return 2;
  }
  if (argc > 2) scratch = (size_t)strtoull (argv[2], NULL, 0);
  if (argc > 3) dstOff  = (size_t)strtoull (argv[3], NULL, 0);

  f = fopen (argv[1], "rb");
  if (f == NULL) { perror ("fopen"); return 2; }
  fseek (f, 0, SEEK_END);
  size = ftell (f);
  fseek (f, 0, SEEK_SET);
  src = (unsigned char *)malloc ((size_t)size);
  if (src == NULL || fread (src, 1, (size_t)size, f) != (size_t)size) {
    fprintf (stderr, "read failed\n");
    return 2;
  }
  fclose (f);

  for (i = 12; i >= 5; i--) decoded = (decoded << 8) + src[i];

  if (posix_memalign (&dstRaw, 4096, (size_t)decoded + 8192) != 0) {
    fprintf (stderr, "out of memory\n");
    return 2;
  }
  dst = (unsigned char *)dstRaw + dstOff;
  scr = (unsigned char *)malloc (scratch);
  if (scr == NULL) { fprintf (stderr, "out of memory\n"); return 2; }
  memset (dst, 0xAA, (size_t)decoded);
  memset (scr, 0xAA, scratch);

  printf ("payload=%ld props=0x%02x dict=%u decoded=%llu scratch=%zu dstOff=%zu\n",
          size, src[0],
          (unsigned)(src[1] | (src[2] << 8) | (src[3] << 16) | ((unsigned)src[4] << 24)),
          decoded, scratch, dstOff);
#ifdef _LZMA_SIZE_OPT
  printf ("config: _LZMA_SIZE_OPT=on\n");
#else
  printf ("config: _LZMA_SIZE_OPT=off\n");
#endif

  a.Functions.Alloc = SzAlloc;
  a.Functions.Free  = SzFree;
  a.Buffer          = scr;
  a.BufferSize      = scratch;

  outLen = (SizeT)decoded;
  inLen  = (SizeT)(size - 13);
  res = LzmaDecode (dst, &outLen, src + 13, &inLen, src, 5,
                    LZMA_FINISH_END, &st, (const ISzAlloc *)&a.Functions);

  printf ("res=%d status=%d outLen=%llu inLen=%llu\n",
          res, st, (unsigned long long)outLen, (unsigned long long)inLen);

  if (res == 0) {
    printf ("DECODE OK  first4=%02x%02x%02x%02x  fvh_at_0x30=%c%c%c%c\n",
            dst[0], dst[1], dst[2], dst[3],
            dst[0x30], dst[0x31], dst[0x32], dst[0x33]);
    return 0;
  }
  printf ("DECODE FAILED\n");
  return 1;
}
