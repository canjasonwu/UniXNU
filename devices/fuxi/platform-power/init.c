/*
 * Single-call probe: open("/").
 *
 * fork works and wait4 blocks, so the multi-call harness is not usable. Each remaining
 * candidate is therefore tested alone, exactly like the calls that already succeeded
 * (getpid and write(1)).
 *
 * Exit status: 1 = open("/") returned a descriptor, 2 = it returned an error, 3 = getpid
 * was not 1 (sanity check failed). No panic line means open("/") hung the kernel.
 */

#define SYS_exit  1
#define SYS_open  5
#define SYS_close 6
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

void
_start(void)
{
	if (syscall1(SYS_getpid, 0) != 1) {
		syscall1(SYS_exit, 3);
	}
	long fd = syscall3(SYS_open, (long)root_path, O_RDONLY, 0);
	if (fd >= 0) {
		syscall1(SYS_close, fd);
		syscall1(SYS_exit, 1);
	}
	syscall1(SYS_exit, 2);
	for (;;) {
	}
}
