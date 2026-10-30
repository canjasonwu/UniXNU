/*
 * Entry stub for the fuxi PID 1 probes.
 *
 * Why this exists: the kernel hands user space an sp that is only 8-byte aligned (the strings and
 * pointer arrays subtracted in exec_copyout_strings do not leave a 16-byte result), and the AArch64
 * ABI requires sp to be 16-byte aligned whenever C code runs. A probe compiled by clang therefore
 * starts with "sub sp, sp, #0x30; stp x29, x30, [sp, #0x20]", and the first store raises an SP
 * alignment fault, which the kernel turns into EXC_ARM_DA_ALIGN and then SIGBUS.
 *
 * This stub aligns sp first and then calls the probe body, which is compiled with
 * -D_start=probe_main so its C entry symbol becomes _probe_main.
 */
	.text
	.globl	__start
	.align	2
__start:
	add	x10, sp, #0
	mov	x9, #15
	bic	x10, x10, x9
	add	sp, x10, #0
	bl	_probe_main
	/* The probes exit through a syscall and never return; keep a sane epilogue anyway. */
	mov	x0, x0
	ret
