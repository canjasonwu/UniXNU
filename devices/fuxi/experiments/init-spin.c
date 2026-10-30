/*
 * Minimal PID 1 probe: entry does nothing but spin.
 *
 * Purpose: separate "user code faults" from "the kernel kills init before/around entry".
 * This program has no stack use, no syscall, no data access beyond the inevitable instruction
 * fetch, so a SIGBUS here cannot come from user code touching memory.
 */

void
_start(void)
{
	for (;;) {
		__asm__ volatile("yield");
	}
}
