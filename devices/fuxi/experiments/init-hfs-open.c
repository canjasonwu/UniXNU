/*
 * PID 1 probe for the HFS root: proves that userland can read a file from the HFS+ volume.
 *
 * The kernel has no working panic renderer while HFS is registered, so the exit status is the
 * result channel. It is reported on screen by kern_exit: the stage ruler is filled up to slot
 * 34 + status before the panic.
 *
 *   /hello.txt opens   -> exit status 5
 *   it does not open   -> exit status 6
 *   getpid() != 1      -> exit status 3
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

static const char hello_path[] = "/hello.txt";

void
_start(void)
{
	if (syscall1(SYS_getpid, 0) != 1) {
		syscall1(SYS_exit, 3);
	}
	long fd = syscall3(SYS_open, (long)hello_path, O_RDONLY, 0);
	if (fd >= 0) {
		syscall1(SYS_close, fd);
		syscall1(SYS_exit, 5);
	}
	syscall1(SYS_exit, 6);
	for (;;) {
	}
}
