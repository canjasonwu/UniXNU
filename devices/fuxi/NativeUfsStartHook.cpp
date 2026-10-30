/*
 * Registers the NativeUfs start hook with the platform-power stub.
 *
 * The hook cannot be a weak reference: Mach-O kernel links reject an undefined weak symbol, and the
 * kernel link has no dynamic lookup. Instead the direction is reversed. The platform-power stub
 * (always linked) owns a null function pointer; this file is compiled only into the NativeUfs archive,
 * so its static constructor runs only when the driver is linked, and it installs the real hook.
 *
 * The platform-power late probe calls the pointer when the boot argument fuxi-ufs-start=1 is present.
 */

extern "C" int (*fuxi_ufs_start_hook)(void);

extern "C" int native_ufs_probe_start(void);

extern "C" int (*fuxi_ufs_read_window_hook)(const char *name, void *dst,
    unsigned long long maxBytes, unsigned long long *outBytes);

extern "C" int native_ufs_read_window(const char *name, void *dst,
    unsigned long long maxBytes, unsigned long long *outBytes);

static int
fuxiNativeUfsStart(void)
{
	/* 1 = started, 0 = the driver refused, negative = an earlier stage failed. The driver itself
	 * panics with details when its own boot arguments ask for a check (native-ufs-probe=10); the
	 * caller reports this value together with the clock state. */
	return native_ufs_probe_start();
}

static int
fuxiNativeUfsReadWindow(const char *name, void *dst, unsigned long long maxBytes,
    unsigned long long *outBytes)
{
	return native_ufs_read_window(name, dst, maxBytes, outBytes);
}

__attribute__((constructor)) static void
fuxiRegisterNativeUfsStart(void)
{
	fuxi_ufs_start_hook = fuxiNativeUfsStart;
	fuxi_ufs_read_window_hook = fuxiNativeUfsReadWindow;
}
