/*
 * Diagnostic PMGR probe: there is no serial port, so every step reports through
 * panic() and the screen tells us which one failed. AMFI panics with
 * "No PMGR?" when no ApplePMGR instance is published, and the device tree node
 * alone did not make the kext publish one.
 */
#include <IOKit/IOService.h>
#include <IOKit/IOLib.h>
#include <libkern/c++/OSMetaClass.h>

extern "C" kern_return_t OSKextLoadKextWithIdentifier(const char *identifier);

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
	kern_return_t status = OSKextLoadKextWithIdentifier("com.apple.driver.ApplePMGR");
	if (status != 0) {
		panic("fuxi-pmgr probe: kext load 0x%x", status);
	}
	OSObject *object = OSMetaClass::allocClassWithName("ApplePMGR");
	if (object == NULL) {
		panic("fuxi-pmgr probe: kext loaded, class ApplePMGR absent");
	}
	IOService *pmgr = OSDynamicCast(IOService, object);
	if (pmgr == NULL) {
		panic("fuxi-pmgr probe: ApplePMGR is not an IOService");
	}
	if (!pmgr->init()) {
		panic("fuxi-pmgr probe: init failed");
	}
	IOService *root = IOService::getServiceRoot();
	if (root == NULL) {
		panic("fuxi-pmgr probe: no service root");
	}
	if (!pmgr->attach(root)) {
		panic("fuxi-pmgr probe: attach failed");
	}
	pmgr->start(NULL);
	IOLog("fuxi-pmgr probe: ApplePMGR published");
}
