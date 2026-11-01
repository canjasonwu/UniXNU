# Vendored IOKit storage include shim

The NativeUfs driver includes `<IOKit/storage/IOBlockStorageDevice.h>`. This base does not ship that
header (it forwards to a DriverKit header that is absent), so a shim is required for the driver to
compile. This directory is that shim, copied from `/root/refs/iostorage-inc` on 2026-11-01; it is the
same header the working images were built against.

`scripts/build-nativeufs.py` points `-I` here, so the archive no longer depends on a directory outside
the repository. The kernel tree's generated copy (`build/storage-abi/include`, produced by
`scripts/build-arm-driver.py`) is an equivalent alternative if the base later ships it.
