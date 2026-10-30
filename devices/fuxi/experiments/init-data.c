/*
 * PID 1 probe that exercises a __DATA segment.
 *
 * Every probe so far was TEXT + LINKEDIT only (no writable data). A real launchd has __DATA, and a
 * writable data segment is where a loader can get the protections wrong without anyone noticing.
 * This probe writes a global, reads it back, then exits with a code that encodes both results:
 *
 *   exit 21 = global write+read ok, AND open("/hello.txt") ok
 *   exit 22 = global write+read ok, open failed
 *   exit 20 = global write+read ok, but not processed as pid 1
 *   (a fault here shows up through the kernel's EL0 abort probe instead of an exit code)
 */

#define SYS_exit   1
#define SYS_open   5
#define SYS_close  6
#define SYS_getpid 20

#define O_RDONLY 0

static volatile int g_counter;      /* lives in __DATA */
static volatile int g_second;       /* another __DATA slot, to make the segment non-trivial */

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
	int code;

	/* Write and read back the data segment before anything else. */
	g_counter = 0x1234;
	g_second = g_counter + 1;
	code = (g_counter == 0x1234 && g_second == 0x1235) ? 20 : 23;

	if (code == 20 && syscall1(SYS_getpid, 0) != 1) {
		syscall1(SYS_exit, 20);
	}

	if (code == 20) {
		long fd = syscall3(SYS_open, (long)hello_path, O_RDONLY, 0);
		if (fd >= 0) {
			syscall1(SYS_close, fd);
			code = 21;
		} else {
			code = 22;
		}
	}

	syscall1(SYS_exit, code);
	for (;;) {
		__asm__ volatile("yield");
	}
}
