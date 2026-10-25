/*
 * Placeholder for the PMGR integration.
 *
 * AMFI's ConfigurationSettings looks up a service called ApplePMGRNub and casts it
 * to the ApplePMGRNub class (defined by com.apple.driver.ApplePMGR). Nothing on this
 * hardware creates that nub, so the cast fails and AMFI panics with "No PMGR?".
 * patches/xnu/zzz-* keep the options open; devices/fuxi/scripts/patch-kernelcache.py
 * currently removes the abort so the boot can continue, and the real nub comes later.
 */
#include <IOKit/IOLib.h>

extern "C" void
fuxi_panic_reset(void)
{
	register uint64_t x0 asm("x0") = 0x84000009ULL;   /* PSCI SYSTEM_RESET */
	register uint64_t x1 asm("x1") = 0;
	asm volatile("smc #0" : "+r"(x0) : "r"(x1) : "memory");
}

extern "C" void
sheng_pmgr_run(void)
{
	IOLog("fuxi-pmgr: no in-kernel PMGR provider, AMFI check is patched out");
}
