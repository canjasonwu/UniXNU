# Vendored NativeUfs driver (snapshot)

Provenance, so the kernel build no longer depends on a directory outside the repository:

- Source: `https://github.com/ljhhkx529/NativeUFS-fuxi.git`, commit `e9ae0f6`
  ("NativeUfs: standalone UFS storage driver for UniXNU fuxi").
- Snapshot taken 2026-11-01 from `/root/refs/NativeUFS-fuxi`, **including its uncommitted working-tree
  change** (175 insertions / 15 deletions in `NativeUfs.cpp`, the 32-bit-DMA retry and related code).
  That change is what images W/X and every archive since 2026-10-31 were actually built from.
- `SmmuTakeover.h` here is the refs version (7,129 bytes), which differs from the
  `UniXNU/NativeUfs` submodule copy (5,786 bytes) in `drivers/NativeUfs`.
- `NativeUfs.cpp.orig` and `build/` are deliberately not vendored.

Why not use the submodule: `drivers/NativeUfs` tracks `UniXNU/NativeUfs` @ `af785c1`, which is a
different repository from both this snapshot and sheng's `UniXNU/ShengUfs`. The archived object must
come from one source of truth; this directory is it. `scripts/build-nativeufs.py` compiles this copy by default and still accepts
`UNIXNU_NATIVEUFS_SOURCE` for experiments.

Note on hashes: the object hash printed by the build is stable for a given checkout path, but not
across paths - the compile uses `-g`, so the absolute source path is embedded in the object. Do not
compare object hashes across checkouts. The archive hash additionally varies per run because cctools
ar stores each member's mtime; use the object hashes for identity, and only within one checkout.
