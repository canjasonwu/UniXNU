/*
 * Experiment 2: does writing to file descriptor 1 kill the kernel?
 *
 * The previous probe did getpid + several opens + write(1) and hung silently, while
 * getpid + exit works (exit status 1, timer-irqs=2). The expected console device path in
 * bsd/dev/arm/cons.c dereferences km_tty[0], which no driver initialises on this board, so
 * a write to fd 1 (the inherited console) can fault the same way open("/dev/console") did.
 *
 * Exit status: 5 = the write returned (with its errno folded in as 100+errno), 6 = the
 * write returned -1 without an errno we can see. No exit status at all means the write
 * killed or hung the kernel, which is the answer we are testing for.
 */

#define SYS_exit    1
#define SYS_write   4
#define SYS_getpid  20

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

static const char marker[] = "F";

void
_start(void)
{
	long pid = syscall1(SYS_getpid, 0);
	long written;

	if (pid != 1) {
		syscall1(SYS_exit, 2);
	}
	written = syscall3(SYS_write, 1, (long)marker, 1);
	if (written >= 0) {
		syscall1(SYS_exit, 5);
	}
	/* -1: fold the errno in so the screen shows which errno the console gave us. */
	syscall1(SYS_exit, 100 - written);
	for (;;) {
	}
}
