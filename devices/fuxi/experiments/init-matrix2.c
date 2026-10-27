/*
 * open() matrix probe, second revision.
 *
 * fork works on this kernel (proven by the minimal fork experiment: exit status 1), so the
 * parent can fork one child per candidate path and collect the result with wait4(WNOHANG).
 * The earlier version was invalid because its syscall helper dropped x0 and therefore never
 * saw fork's return value.
 *
 * Two bits per path, low path first:
 *   bit 2i    the child returned (i.e. open did not hang)
 *   bit 2i+1  open succeeded (fd >= 0) rather than failing with an error
 * Paths: 0 "/", 1 "/dev", 2 "/dev/null", 3 "/dev/zero", 4 "/sbin/launchd".
 * bit 10: the harness itself completed fork/wait4 for every path.
 *
 * A clear bit 2i with bit 10 set means that path hung; a missing panic line at all means
 * fork/wait4 failed here, which the minimal experiment has already ruled out.
 */

#define SYS_exit    1
#define SYS_fork    2
#define SYS_open    5
#define SYS_close   6
#define SYS_wait4   7
#define SYS_kill    37

#define O_RDONLY 0
#define WNOHANG  0x1
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
syscall2(long number, long a0, long a1)
{
	register long x16 asm("x16") = number;
	register long x0 asm("x0") = a0;
	register long x1 asm("x1") = a1;
	asm volatile("svc #0x80" : "+r"(x0) : "r"(x16), "r"(x1) : "memory", "cc");
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

static const char *const paths[] = {
	"/",
	"/dev",
	"/dev/null",
	"/dev/zero",
	"/sbin/launchd",
};
#define PATHS (sizeof(paths) / sizeof(paths[0]))

void
_start(void)
{
	unsigned result = 0;
	unsigned completed = 0;

	for (unsigned index = 0; index < PATHS; index++) {
		long pid = syscall0r(SYS_fork);
		if (pid < 0) {
			break;
		}
		if (pid == 0) {
			long fd = syscall3(SYS_open, (long)paths[index], O_RDONLY, 0);
			if (fd >= 0) {
				syscall1(SYS_close, fd);
				syscall1(SYS_exit, 0);      /* opened */
			}
			syscall1(SYS_exit, 1);          /* returned an error */
			for (;;) {
			}
		}
		for (long round = 0; round < POLL_ROUNDS; round++) {
			long status = 0;
			long waited = syscall4(SYS_wait4, pid, (long)&status, WNOHANG, 0);
			if (waited == pid) {
				result |= 1u << (index * 2);           /* returned */
				if ((status & 0x7f) == 0 && ((status >> 8) & 0xff) == 0) {
					result |= 1u << (index * 2 + 1);   /* open succeeded */
				}
				break;
			}
			if (waited < 0) {
				break;
			}
		}
		syscall2(SYS_kill, pid, 9);
		completed++;
	}

	if (completed == PATHS) {
		result |= 1u << 10;
	}
	syscall1(SYS_exit, (long)result);
	for (;;) {
	}
}
