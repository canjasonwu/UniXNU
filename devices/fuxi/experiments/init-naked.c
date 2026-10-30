/*
 * PID 1 probe that never touches the user stack.
 *
 * If the HFS-rooted init dies with SIGBUS (a protection fault per ux_exception.c: any
 * EXC_BAD_ACCESS code other than KERN_INVALID_ADDRESS becomes SIGBUS), the most likely first
 * faulting access is the compiler's stack frame write. This probe has no prologue, no stack use and
 * no globals: it exits immediately. If this one runs (no SIGBUS) while the C probes do not, the
 * initial user stack is the problem; if it also faults, the fault is elsewhere.
 */

__attribute__((naked, noreturn)) void
_start(void)
{
	__asm__ volatile(
	    "mov x0, #7\n"
	    "mov x16, #1\n"
	    "svc #0x80\n"
	    "1: yield\n"
	    "b 1b\n");
}
