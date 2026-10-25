/*
 * Two hooks that live in the kernel.
 *
 * fuxi_panic_reset / fuxi_panic_reset_delayed: this board has no debugger and the
 * platform reboot call does not bring it back, so every panic used to leave the
 * phone dead to USB until someone held the power button. patches/xnu/zzz-panic-reset.patch
 * arms these from panic_trap_to_debugger and calls the delayed form at the end of
 * paniclog_flush(), i.e. after the panic text is on screen.
 *
 * sheng_pmgr_run(): the PMGR service AMFI wants is an ApplePMGRNub created by the
 * ApplePMGR kext, which never matches on this hardware. The abort is currently
 * patched out of the kernelcache copy (devices/fuxi/scripts/patch-kernelcache.py).
 */
#include <IOKit/IOLib.h>

static inline uint64_t
fuxi_timebase(void)
{
	uint64_t value;
	asm volatile("isb; mrs %0, cntpct_el0" : "=r"(value));
	return value;
}

extern "C" void
fuxi_panic_reset(void)
{
	register uint64_t x0 asm("x0") = 0x84000009ULL;   /* PSCI SYSTEM_RESET */
	register uint64_t x1 asm("x1") = 0;
	asm volatile("smc #0" : "+r"(x0) : "r"(x1) : "memory");
}

extern "C" void
fuxi_panic_reset_delayed(void)
{
	/* 15 s at the 19.2 MHz board timebase, with a spin bound so a missing counter
	 * cannot turn this into another hang. */
	const uint64_t limit = 15ULL * 19200000ULL;
	uint64_t start = fuxi_timebase();
	for (uint64_t spins = 0; spins < 400000000ULL; spins++) {
		if (fuxi_timebase() - start >= limit) {
			break;
		}
		asm volatile("yield");
	}
	fuxi_panic_reset();
}

extern "C" void
sheng_pmgr_run(void)
{
	IOLog("fuxi-pmgr: no in-kernel PMGR provider, AMFI check is patched out");
}
