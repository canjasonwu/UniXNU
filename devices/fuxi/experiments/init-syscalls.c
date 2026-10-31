/*
 * Syscall surface probe: the pieces launchd needs before it can even start.
 *
 * Exits with a single small status code so the seven-segment display / panic text can report it:
 *   20 = getpid() != 1
 *   21 = open("/hello.txt") failed
 *   22 = read failed
 *   30 = write to an opened descriptor failed
 *   31 = mmap of anonymous memory failed
 *   32 = store into the mapping failed (would have faulted first)
 *   40 = fork failed
 *   41 = fork succeeded but the child did not exit 0
 *   50 = all passed
 */
typedef unsigned long size_t;

static long
syscall6(long n, long a1, long a2, long a3, long a4, long a5, long a6)
{
	register long x0 __asm__("x0") = a1;
	register long x1 __asm__("x1") = a2;
	register long x2 __asm__("x2") = a3;
	register long x3 __asm__("x3") = a4;
	register long x4 __asm__("x4") = a5;
	register long x5 __asm__("x5") = a6;
	register long x16 __asm__("x16") = n;
	__asm__ volatile("svc #0x80"
	    : "+r"(x0)
	    : "r"(x1), "r"(x2), "r"(x3), "r"(x4), "r"(x5), "r"(x16)
	    : "memory");
	return x0;
}

#define SYS_exit 1
#define SYS_read 3
#define SYS_write 4
#define SYS_open 5
#define SYS_getpid 20
#define SYS_fork 2
#define SYS_mmap 197
#define SYS_close 6
#define SYS_wait4 7

static void
die(long code)
{
	syscall6(SYS_exit, code, 0, 0, 0, 0, 0);
	for (;;) {
	}
}

static char hello_path[] = "/hello.txt";
static char buffer[64];

void
_start(void)
{
	if (syscall6(SYS_getpid, 0, 0, 0, 0, 0, 0) != 1) {
		die(20);
	}
	long fd = syscall6(SYS_open, (long)hello_path, 0, 0, 0, 0, 0);
	if (fd < 0) {
		die(21);
	}
	long got = syscall6(SYS_read, fd, (long)buffer, sizeof(buffer), 0, 0, 0);
	if (got < 0) {
		die(22);
	}
	syscall6(SYS_close, fd, 0, 0, 0, 0, 0);

	/* Anonymous memory: PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANON */
	long map = syscall6(SYS_mmap, 0, 0x10000, 0x3, 0x1002, -1, 0);
	if (map < 0) {
		die(31);
	}
	*(volatile unsigned long *)(map + 32) = 0x5a5a;
	if (*(volatile unsigned long *)(map + 32) != 0x5a5a) {
		die(32);
	}

	long child = syscall6(SYS_fork, 0, 0, 0, 0, 0, 0);
	if (child < 0) {
		die(40);
	}
	if (child == 0) {
		die(0);
	}
	int status = 0;
	long waited = syscall6(SYS_wait4, child, (long)&status, 0, 0, 0, 0);
	if (waited != child) {
		die(41);
	}
	die(50);
}
