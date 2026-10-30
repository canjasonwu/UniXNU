/*
 * PID 1 for the HFS root that never exits, so the (currently unreliable) kernel panic path is
 * never entered.
 *
 * It does exactly two things and then spins, which the kernel reports with marks:
 *   1. getpid()                     - sanity: we are process 1
 *   2. open("/hello.txt")           - kernel marks hfs_vnop_open, proving userland can call into
 *                                     the HFS port and get a descriptor
 *   3. write(1, "x", 1) if the open succeeded - kernel marks the write path, proving that a
 *                                     second syscall works and that userland is really running
 *
 * The kernel display then shows the last mark index; no panic is involved.
 */

#define SYS_exit   1
#define SYS_write  4
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

static const char hello_path[] = "/hello.txt";
static const char marker[] = "x";

void
_start(void)
{
	if (syscall1(SYS_getpid, 0) != 1) {
		/* Not process 1: spin rather than exit, so no panic is triggered. */
		for (;;) {
			__asm__ volatile("yield");
		}
	}

	long fd = syscall3(SYS_open, (long)hello_path, O_RDONLY, 0);
	if (fd >= 0) {
		syscall3(SYS_write, 1, (long)marker, 1);
		syscall1(SYS_close, fd);
	}

	for (;;) {
		__asm__ volatile("yield");
	}
}
