/*
 * Experiment 3: does fork() return at all on this kernel?
 *
 * Everything else in the userland probe worked (getpid, write, timers), but a fork-based
 * matrix probe produced no output at all, which points at fork itself. This is the smallest
 * possible test:
 *
 *   exit status 1  fork returned > 0  (parent)  -> fork works
 *   exit status 2  fork returned 0    (child)   -> fork works
 *   exit status 3  fork returned < 0  (error)   -> fork refuses, but does not hang
 *   no text at all                              -> fork hangs the kernel
 */

#define SYS_exit 1
#define SYS_fork 2

/* fork returns its result in x0, so this helper must return x0 (the earlier probe's
 * helper dropped it, which is why that harness could not be trusted). */
static long
syscall_ret(long number)
{
	register long x16 asm("x16") = number;
	register long x0 asm("x0") = 0;
	asm volatile("svc #0x80" : "+r"(x0) : "r"(x16) : "memory", "cc");
	return x0;
}

static void
syscall_exit(long code)
{
	register long x16 asm("x16") = SYS_exit;
	register long x0 asm("x0") = code;
	asm volatile("svc #0x80" : : "r"(x16), "r"(x0) : "memory", "cc");
	for (;;) {
	}
}

void
_start(void)
{
	long pid = syscall_ret(SYS_fork);

	if (pid == 0) {
		syscall_exit(2);
	}
	if (pid > 0) {
		syscall_exit(1);
	}
	syscall_exit(3);
}
