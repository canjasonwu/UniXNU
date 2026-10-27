/*
 * PID 1 open() matrix probe.
 *
 * The single-call experiments showed getpid and write(1) both work, so the silent hangs
 * come from one of the open() paths. Testing them one boot at a time is expensive, so this
 * probe forks a child per candidate, polls wait4(WNOHANG) and treats "child did not exit
 * within the budget" as a hang. The parent then exits with a bitmask, which the kernel
 * prints in the init-death panic:
 *
 *   bit 0  open("/")           returned
 *   bit 1  open("/dev")        returned
 *   bit 2  open("/dev/null")   returned
 *   bit 3  open("/dev/zero")   returned
 *   bit 4  open("/sbin/launchd") returned
 *   bit 5  fork() itself works (the harness ran at all)
 *   bit 6  close() of an opened fd returned 0
 *   bit 7  the child exited normally rather than being killed or lost
 *
 * A clear bit next to a set fork bit means that particular call hung. If the whole line is
 * missing, the parent itself never exited, i.e. fork or wait4 is the blocker.
 *
 * Darwin arm64 syscall ABI: number in x16, args in x0..x7, svc #0x80.
 */

#define SYS_exit    1
#define SYS_fork    2
#define SYS_open    5
#define SYS_close   6
#define SYS_wait4   7
#define SYS_getpid  20
#define SYS_kill    37

#define O_RDONLY 0
#define WNOHANG  0x1

static long
syscall0(long number)
{
	register long x16 asm("x16") = number;
	asm volatile("svc #0x80" : : "r"(x16) : "memory", "cc");
	return 0;
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

static long
try_open(const char *path)
{
	long fd = syscall3(SYS_open, (long)path, O_RDONLY, 0);
	if (fd >= 0) {
		syscall1(SYS_close, fd);
	}
	return fd;
}

#define POLL_ROUNDS 20000000L

void
_start(void)
{
	unsigned result = 0;
	int index;

	for (index = 0; index < (int)(sizeof(paths) / sizeof(paths[0])); index++) {
		long pid = syscall0(SYS_fork);
		if (pid < 0) {
			break;
		}
		if (pid == 0) {
			/* child: run exactly one probe, then exit */
			long fd = try_open(paths[index]);
			syscall1(SYS_exit, fd >= 0 ? 0 : 1);
			for (;;) {
			}
		}
		/* parent: poll for exit without blocking */
		for (long round = 0; round < POLL_ROUNDS; round++) {
			long status = 0;
			long waited = syscall4(SYS_wait4, pid, (long)&status, WNOHANG, 0);
			if (waited == pid) {
				result |= 1u << index;
				if ((status & 0x7f) == 0) {
					result |= 1u << 7;
				}
				break;
			}
			if (waited < 0) {
				break;
			}
		}
		syscall2(SYS_kill, pid, 9);
	}

	if (result != 0) {
		result |= 1u << 5;   /* the harness itself ran */
	}
	syscall1(SYS_exit, (long)result);
	for (;;) {
	}
}
