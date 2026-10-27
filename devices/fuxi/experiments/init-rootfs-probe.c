/*
 * Root-filesystem probe.
 *
 * Now that HFS is built into the kernel, the data ramdisk carries a real HFS+ volume
 * instead of a bare Mach-O. This probe reports whether the mount worked by opening the
 * paths that a real root must have:
 *
 *   bit 0  open("/")        succeeded
 *   bit 1  open("/sbin")    succeeded
 *   bit 2  open("/sbin/launchd") succeeded (the file this process was loaded from)
 *   bit 4  the probe ran at all (always set before exit)
 *
 * Exit status = mask. No panic line means the kernel hung before init exited.
 */

#define SYS_exit   1
#define SYS_open   5
#define SYS_close  6
#define SYS_getpid 20

#define O_RDONLY 0

static long
syscall1(long number, long a0)
{
	register long x16 asm("x16") = number;
	register long x0 asm("x0") = a0;
	asm volatile("svc #0x80" : "+r"(x0) : "r"(x16) : "memory", "cc");
	return x0;
}

static long
syscall3(long number, long a0, long a1, long a2)
{
	register long x16 asm("x16") = number;
	register long x0 asm("x0") = a0;
	register long x1 asm("x1") = a1;
	register long x2 asm("x2") = a2;
	asm volatile("svc #0x80" : "+r"(x0) : "r"(x16), "r"(x1), "r"(x2) : "memory", "cc");
	return x0;
}

static const char root_path[] = "/";
static const char sbin_path[] = "/sbin";
static const char launchd_path[] = "/sbin/launchd";

static int
try_open(const char *path)
{
	long fd = syscall3(SYS_open, (long)path, O_RDONLY, 0);
	if (fd >= 0) {
		syscall1(SYS_close, fd);
		return 1;
	}
	return 0;
}

void
_start(void)
{
	unsigned result = 1u << 4;

	if (syscall1(SYS_getpid, 0) != 1) {
		syscall1(SYS_exit, 3);
	}
	if (try_open(root_path)) {
		result |= 1u << 0;
	}
	if (try_open(sbin_path)) {
		result |= 1u << 1;
	}
	if (try_open(launchd_path)) {
		result |= 1u << 2;
	}
	syscall1(SYS_exit, (long)result);
	for (;;) {
	}
}
