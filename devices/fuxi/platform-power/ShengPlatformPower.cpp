/*
 * Two hooks that live in the kernel because nothing else is early or late enough.
 *
 * fuxi_panic_reset(): the panic screen has no USB function and this board has no
 * other reset path, so every failed boot used to need a human power press. PSCI
 * SYSTEM_RESET over SMC (the call the UEFI watchdog already uses) brings the phone
 * back to Android by itself. xnu calls it from panic_spin_forever() when built with
 * XNU_FUXI_PANIC_RESET, see patches/xnu/zzz-panic-reset.patch.
 *
 * sheng_pmgr_run(): ran during the AMFI investigation. The PMGR service now comes
 * from the device tree (a pmgr1,bring-up node that AppleBringUpPMGR matches), so
 * this is a no-op kept as the bsd_init hook point.
 */
#include <IOKit/IOLib.h>

extern "C" void
fuxi_panic_reset(void)
{
	register uint64_t x0 asm("x0") = 0x84000009ULL;   /* PSCI SYSTEM_RESET */
	register uint64_t x1 asm("x1") = 0;
	register uint64_t x2 asm("x2") = 0;
	register uint64_t x3 asm("x3") = 0;
	asm volatile("smc #0" : "+r"(x0) : "r"(x1), "r"(x2), "r"(x3) : "memory");
}

extern "C" void
sheng_pmgr_run(void)
{
	IOLog("fuxi-pmgr: the pmgr1,bring-up device tree node provides the PMGR service");
}
