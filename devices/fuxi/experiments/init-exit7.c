/*
 * Minimal PID 1 for the HFS root: one instruction sequence, no syscall wrapper, no getpid.
 *
 * The previous file-open probe died with SIGBUS while running from HFS. This one separates the
 * two explanations: if it exits 7 normally, exec and page mapping from the HFS volume work and
 * the earlier SIGBUS came from a later call (open/read path); if it also dies with a signal,
 * exec or the mapping of the HFS-backed text pages is what fails.
 *
 * Layout: mov x0,#7 ; mov x16,#1 (SYS_exit) ; svc #0x80 ; then a spin in case exit returns.
 */

void
_start(void)
{
	__asm__ volatile(
	    "mov x0, #7\n"
	    "mov x16, #1\n"
	    "svc #0x80\n"
	    "1: b 1b\n"
	    ::: "x0", "x16", "memory");
}
