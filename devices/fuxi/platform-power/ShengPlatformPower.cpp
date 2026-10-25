/*
 * PMGR integration hooks.
 *
 * AMFI waits for a service of class ApplePMGRNub (IOService::serviceMatching +
 * waitForMatchingService + safeMetaCast to ApplePMGRNub). Nothing creates that nub
 * on this hardware, because com.apple.driver.ApplePMGR only has the
 * AppleBringUpPMGR personality matching a pmgr1,bring-up device tree node and that
 * personality never starts here.
 *
 * sheng_pmgr_run() runs from bsd_init, too early: kext classes are not registered
 * yet (confirmed on the phone). sheng_pmgr_late_probe() runs from bsd_autoconf
 * right after IOKitBSDInit(), where the classes do exist, and publishes the nub,
 * reporting each step through panic() because there is no serial port.
 */
#include <IOKit/IOService.h>
#include <IOKit/IOLib.h>
#include <libkern/c++/OSMetaClass.h>
#include <libkern/c++/OSSymbol.h>

extern "C" void
sheng_pmgr_run(void)
{
	IOLog("fuxi-pmgr: bsd_init hook, classes not registered yet");
}

extern "C" void
sheng_pmgr_late_probe(void)
{
	const OSSymbol *symbol = OSSymbol::withCString("ApplePMGRNub");
	if (symbol == NULL) {
		panic("fuxi-probe: cannot make the class-name symbol");
	}
	const OSMetaClass *meta = OSMetaClass::getMetaClassWithName(symbol);
	symbol->release();
	if (meta == NULL) {
		panic("fuxi-probe: class ApplePMGRNub is not registered");
	}
	OSObject *object = meta->alloc();
	if (object == NULL) {
		panic("fuxi-probe: ApplePMGRNub alloc failed");
	}
	IOService *nub = OSDynamicCast(IOService, object);
	if (nub == NULL) {
		panic("fuxi-probe: ApplePMGRNub is not an IOService");
	}
	if (!nub->init()) {
		panic("fuxi-probe: ApplePMGRNub init failed");
	}
	IOService *root = IOService::getServiceRoot();
	if (root == NULL) {
		panic("fuxi-probe: no service root");
	}
	if (!nub->attach(root)) {
		panic("fuxi-probe: ApplePMGRNub attach failed");
	}
	nub->start(NULL);
	IOLog("fuxi-probe: ApplePMGRNub published");
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
	register uint64_t x0 asm("x0") = 0x84000009ULL;
	register uint64_t x1 asm("x1") = 0;
	asm volatile("smc #0" : "+r"(x0) : "r"(x1) : "memory");
}
