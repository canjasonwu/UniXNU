/*
 * Experiment A: minimal PID 1.
 *
 * One syscall that cannot block (getpid), then exit with a value that tells us what
 * happened: 1 = this really is process 1, 2 = it is not. No open(), no write(), no
 * reboot(): those are the calls that produced a silent hang in earlier attempts, and
 * this build separates "can a syscall run at all" from "does a blocking path hang".
 *
 * The kernel prints "initproc failed to start ... (signal 0, exit status N)" when init
 * exits, so the exit status is the answer channel.
 */

#define SYS_exit   1
#define SYS_getpid 20

static long
syscall1(long number, long a0)
{
	register long x16 asm("x16") = number;
	register long x0 asm("x0") = a0;
	asm volatile("svc #0x80" : "+r"(x0) : "r"(x16) : "memory", "cc");
	return x0;
}

void
_start(void)
{
	long pid = syscall1(SYS_getpid, 0);
	syscall1(SYS_exit, pid == 1 ? 1 : 2);
	for (;;) {
	}
}
