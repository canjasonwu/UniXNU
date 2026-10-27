/*
 * Experiment 4: fork + wait4, with a child that does nothing but exit.
 *
 * fork alone works (exit status 1 from the minimal experiment). The fork-based matrix hung,
 * so the difference is the wait4(WNOHANG) polling path. This image isolates it:
 *
 *   child : exit(0) immediately
 *   parent: wait4(pid, &status, WNOHANG, NULL) in a bounded loop, then exit
 *
 * Exit status: 4 = the child was reaped, 5 = wait4 returned an error, 6 = the poll budget
 * expired without the child being reaped. No panic line at all means wait4 blocked despite
 * WNOHANG, or the second fork never returned.
 */

#define SYS_exit  1
#define SYS_fork  2
#define SYS_wait4 7

#define WNOHANG 0x1
#define POLL_ROUNDS 3000000L

static long
syscall0r(long number)
{
	register long x16 asm("x16") = number;
	register long x0 asm("x0") = 0;
	asm volatile("svc #0x80" : "+r"(x0) : "r"(x16) : "memory", "cc");
	return x0;
}

static long
syscall1(long number, long a0)
{
	register long x16 asm("x16") = number;
	register long x0 asm("x0") = a0;
	asm volatile("svc #0x80" : "+r"(x0) : "r"(x16) : "memory", "cc");
	return x0;
}

static long
syscall4(long number, long a0, long a1, long a2, long a3)
{
	register long x16 asm("x16") = number;
	register long x0 asm("x0") = a0;
	register long x1 asm("x1") = a1;
	register long x2 asm("x2") = a2;
	register long x3 asm("x3") = a3;
	asm volatile("svc #0x80" : "+r"(x0) : "r"(x16), "r"(x1), "r"(x2), "r"(x3) : "memory", "cc");
	return x0;
}

void
_start(void)
{
	long status = 0;
	long pid = syscall0r(SYS_fork);

	if (pid < 0) {
		syscall1(SYS_exit, 3);
	}
	if (pid == 0) {
		syscall1(SYS_exit, 0);
		for (;;) {
		}
	}

	for (long round = 0; round < POLL_ROUNDS; round++) {
		long waited = syscall4(SYS_wait4, pid, (long)&status, WNOHANG, 0);
		if (waited == pid) {
			syscall1(SYS_exit, 4);
		}
		if (waited < 0) {
			syscall1(SYS_exit, 5);
		}
	}
	syscall1(SYS_exit, 6);
	for (;;) {
	}
}
