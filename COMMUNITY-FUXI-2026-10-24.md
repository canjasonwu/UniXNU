# Community fuxi progress — pulled 2026-10-24

Sources checked: the fuxi ports listed on the UniXNU documentation page plus the
upstream UniXNU repository. Files were pulled to
`C:\Users\Jason Wu\.dsh\profiles\desktop\community-fuxi\` on the QQ session
machine; this note records what they contain.

## Repositories with new fuxi work

| Repository | What is new | State |
| --- | --- | --- |
| https://github.com/Xyvern08371/unixnu-fuxi | real-hardware XNU boots to a panic print (M7 done); bare-metal shim route instead of UEFI; read-only UEFI UFS driver; QEMU harness; fuxi hardware facts | active 2026-10-24 |
| https://github.com/hanzi123-bilp/unixnu-fuxi | fuxi kernel stage 2: all 11 archives build, final link was one symbol short (2026-10-22); storage kept off until the storage ABI audit exists | last push 2026-10-22 |
| https://github.com/hanzi123-bilp/uefi-fuxi | iOS root image pipeline (iPhone X 16.7.16 IPSW -> 16 GB HFS+ image with cryptexes, keybag, SWEET additions); FIEMAP extents tool for a pinned root image; loader payload with userdata-root cmdline | CI green 2026-10-24 |
| https://github.com/UniXNU/UniXNU | kernel build on a Linux host merged (PR #100); fuxi community ports documented | merged 2026-10-24 |

## Directly usable

1. `Xyvern08371-unixnu-fuxi/loader/XnuUfs.c` — UEFI-side read-only UFS driver
   (UFSHCI 0x01d84000, JEDEC UESD223, SCSI read only). The fuxi Mu-Silicium
   firmware enumerates no block devices, so this is what would let UEFI read the
   kernel payload or a root image from UFS. Derived from dipa-studio/ios-sweet.
2. Xyvern's hardware data: debug UART 0x00a9c000 (qcom,geni-debug-uart,
   console=ttyMSM0); framebuffer visible at 0xB8000000 (GOP's 0xB0000000 is not
   reachable bare metal) with real stride 1080; GICR_PE_SIZE 0x40000,
   GICR_IGROUPR0 0x10080, virtual timer INTID 19; XNU entry needs EL1, MMU and
   D-cache off, daifset #0xf, DT in low memory.
3. Upstream `scripts/linux/` set plus the updated `scripts/unixnu/kernel.py`,
   `build-xnu-4k-baseline.sh`, `build-corecrypto-14.py` and
   `patches/xnu/offline-component-load-probes.patch`: enables the kernel step on
   a Linux host, removing the macOS/Xcode dependency from CI once merged.
4. `hanzi123-bilp-uefi-fuxi` iosroot workflows + `fiemap-tool/fiemap.c`: build an
   HFS+ iOS root image and enumerate its physical extents, so a loader can hand a
   root image to XNU without a UEFI filesystem stack.

## Known trap from Xyvern's notes

The build defines `ARM64_BOARD_CONFIG_FUXI`, never `FUXI`. Patches guarded with
`#if defined(FUXI)` are silently compiled out; they found 37 platform-code sites
that way and changed them to `defined(ARM64_BOARD_CONFIG_FUXI)`.
