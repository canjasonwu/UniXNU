/*
 * Which pointer can the kernel copy a path string from?
 *
 * Exit codes (all small enough for the seven-segment display):
 *   20 = getpid() != 1
 *   60 = open(__TEXT const literal path) ok
 *   61 = open(__TEXT const literal path) failed; code is 100 + errno
 *   62 = open(__DATA writable array path) ok
 *   63 = open(__DATA writable array path) failed; code is 100 + errno
 */
#define SYS_exit   1
#define SYS_open   5
#define SYS_close  6
#define SYS_getpid 20
#define O_RDONLY   0

static long
syscall3(long number, long a0, long a1, long a2)
{
	register long x16 __asm__("x16") = number;
	register long x0 __asm__("x0") = a0;
	register long x1 __asm__("x1") = a1;
	register long x2 __asm__("x2") = a2;
	__asm__ volatile("svc #0x80" : "+r"(x0) : "r"(x16), "r"(x1), "r"(x2) : "memory", "cc");
	return x0;
}

static const char path_text[] = "/hello.txt";
static char path_data[] = "/hello.txt";

static void
die(long code)
{
	syscall3(SYS_exit, code, 0, 0);
	for (;;) {
	}
}

void
_start(void)
{
	if (syscall3(SYS_getpid, 0, 0, 0) != 1) {
		die(20);
	}
	long fd = syscall3(SYS_open, (long)path_text, O_RDONLY, 0);
	if (fd < 0) {
		die(100 - fd);
	}
	syscall3(SYS_close, fd, 0, 0);
	fd = syscall3(SYS_open, (long)path_data, O_RDONLY, 0);
	if (fd < 0) {
		die(100 - fd);
	}
	syscall3(SYS_close, fd, 0, 0);
	die(61 - 1 + 0);
}
