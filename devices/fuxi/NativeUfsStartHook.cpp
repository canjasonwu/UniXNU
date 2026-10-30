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

extern "C" void (*fuxi_ufs_start_hook)(void);

extern "C" int native_ufs_probe_start(void);

static void
fuxiNativeUfsStart(void)
{
	int result = native_ufs_probe_start();
	/* 1 = started, 0 = the driver refused, negative = an earlier stage failed. The driver itself
	 * panics with details when its own boot arguments ask for a check (native-ufs-probe=10). */
	panic("fuxi-ufs-start: hook=%d (1 started, 0 refused, <0 stage)", result);
}

__attribute__((constructor)) static void
fuxiRegisterNativeUfsStart(void)
{
	fuxi_ufs_start_hook = fuxiNativeUfsStart;
}
