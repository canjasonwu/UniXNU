/*
 * PID 1 probe for the fuxi bring-up, third revision.
 *
 * The kernel prints "initproc failed to start ... (signal 0, exit status N)" when
 * init exits, so the exit status is a reporting channel that answers many questions
 * in one boot.
 *
 * Two calls are deliberately absent because they kill or hang the machine here:
 *   - open("/dev/console"): bsd/dev/arm/cons.c cnopen() dereferences km_tty[0],
 *     which no keyboard/mouse driver on this board has initialised.
 *   - reboot(2): the shutdown path walks PMGR, which is still a stub.
 *
 * Bits, low to high:
 *   0  getpid() == 1
 *   1  getuid() == 0
 *   2  open("/dev/null", O_RDONLY) >= 0
 *   3  open("/", O_RDONLY) >= 0
 *   4  write(1, ...) >= 0
 *   5  open("/dev/zero", O_RDONLY) >= 0
 *   6  close() of a valid fd returned 0
 *   7  open("/dev", O_RDONLY) >= 0
 *   8  geteuid() == 0
 *
 * Darwin arm64 syscall ABI: number in x16, arguments in x0..x7, svc #0x80.
 */

typedef unsigned int u32;

#define SYS_exit    1
#define SYS_write   4
#define SYS_open    5
#define SYS_close   6
#define SYS_getpid  20
#define SYS_getuid  24
#define SYS_geteuid 25

#define O_RDONLY 0

#define NO_FD 0xFFFFFFFFu

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

static const char null_path[] = "/dev/null";
static const char zero_path[] = "/dev/zero";
static const char root_path[] = "/";
static const char dev_path[] = "/dev";
static const char message[] = "fuxi: pid 1 probe\n";

static u32
open_path(const char *path)
{
	long fd = syscall3(SYS_open, (long)path, O_RDONLY, 0);
	return fd >= 0 ? (u32)fd : NO_FD;
}

void
_start(void)
{
	u32 result = 0;
	u32 null_fd;
	u32 zero_fd;
	u32 root_fd;
	u32 dev_fd;

	if (syscall1(SYS_getpid, 0) == 1) {
		result |= 1u << 0;
	}
	if (syscall1(SYS_getuid, 0) == 0) {
		result |= 1u << 1;
	}
	if (syscall1(SYS_geteuid, 0) == 0) {
		result |= 1u << 8;
	}

	null_fd = open_path(null_path);
	if (null_fd != NO_FD) {
		result |= 1u << 2;
	}
	root_fd = open_path(root_path);
	if (root_fd != NO_FD) {
		result |= 1u << 3;
	}
	zero_fd = open_path(zero_path);
	if (zero_fd != NO_FD) {
		result |= 1u << 5;
	}
	dev_fd = open_path(dev_path);
	if (dev_fd != NO_FD) {
		result |= 1u << 7;
	}

	if (syscall3(SYS_write, 1, (long)message, (long)(sizeof(message) - 1)) >= 0) {
		result |= 1u << 4;
	}
	if (dev_fd != NO_FD && syscall1(SYS_close, dev_fd) == 0) {
		result |= 1u << 6;
	}

	syscall1(SYS_exit, (long)result);
	for (;;) {
	}
}
