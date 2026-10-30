/*
 * UFS root image -> RAM device (M2 pipeline), gated by boot arguments.
 *
 * fuxi-ufs-read=1   read the "OS" window, register it as an md device, panic with the result
 * fuxi-ufs-read=2   same, but stay silent and let setconf() switch the root to that device
 * fuxi-ufs-read-mb  buffer size in MiB (default 96); the window must fit
 *
 * The window reader lives in the NativeUfs archive (devices/fuxi/scripts/nativeufs-read-hook.py) and is
 * installed through the same constructor-registration used for the start hook, so a kernel without the
 * driver simply has a null pointer here.
 */
extern "C" int (*fuxi_ufs_read_window_hook)(const char *name, void *dst,
    unsigned long long maxBytes, unsigned long long *outBytes);

extern "C" void
fuxi_ufs_read(void)
{
	uint32_t mode = 0, sizeMb = 0;
	if (!PE_parse_boot_argn("fuxi-ufs-read", &mode, sizeof(mode)) || mode == 0) {
		return;
	}
	if (fuxi_ufs_read_window_hook == NULL) {
		panic("fuxi-ufs-read: driver not linked");
	}
	if (!PE_parse_boot_argn("fuxi-ufs-read-mb", &sizeMb, sizeof(sizeMb)) || sizeMb == 0) {
		sizeMb = 96;
	}
	uint64_t bytes = (uint64_t)sizeMb << 20;

	IOBufferMemoryDescriptor *buffer = IOBufferMemoryDescriptor::inTaskWithOptions(kernel_task,
	    static_cast<IOOptionBits>(kIODirectionOut) | static_cast<IOOptionBits>(kIOMemoryPhysicallyContiguous),
	    bytes, 4096);
	if (buffer == NULL) {
		panic("fuxi-ufs-read: buffer allocation failed (%u MiB)", sizeMb);
	}
	void *virt = buffer->getBytesNoCopy();
	if (virt == NULL) {
		buffer->release();
		panic("fuxi-ufs-read: buffer has no kernel address");
	}

	unsigned long long got = 0;
	int result = fuxi_ufs_read_window_hook("OS", virt, bytes, &got);

	if (result != 0) {
		panic("fuxi-ufs-read: window read failed (%d), requested %u MiB", result, sizeMb);
	}
	int dev = mdevadd(-1, ((uint64_t)(uintptr_t)virt) >> 12, (unsigned int)(got >> 12), 0);
	if (dev < 0) {
		panic("fuxi-ufs-read: mdevadd failed (%d) bytes=%llu", dev, got);
	}
	fuxi_md_dev = dev;

	if (mode == 1) {
		panic("fuxi-ufs-read: ok dev=0x%x bytes=%llu (mode 1 stops before mountroot)",
		    (unsigned)dev, got);
	}
}
