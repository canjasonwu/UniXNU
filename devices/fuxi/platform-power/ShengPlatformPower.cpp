/*
 * Publish the ApplePMGR instance AMFI looks for, and report what happened.
 *
 * AMFI's ConfigurationSettings panics with "AMFI: No PMGR?" when no power
 * manager service is published. There is no serial port on this board, so the
 * only printf available is panic(): every step reports itself that way until the
 * publish works, then it returns normally and AMFI's own behaviour is the signal.
 */
#include <IOKit/IOService.h>
#include <IOKit/IOLib.h>
#include <libkern/c++/OSMetaClass.h>

extern "C" kern_return_t OSKextLoadKextWithIdentifier(const char *identifier);

extern "C" void
sheng_pmgr_run(void)
{
	kern_return_t status = OSKextLoadKextWithIdentifier("com.apple.driver.ApplePMGR");
	if (status != 0) {
		panic("fuxi-pmgr: load kext failed 0x%x", status);
	}

	OSObject *object = OSMetaClass::allocClassWithName("ApplePMGR");
	if (object == NULL) {
		panic("fuxi-pmgr: kext loaded but class ApplePMGR is not registered");
	}

	IOService *pmgr = OSDynamicCast(IOService, object);
	if (pmgr == NULL) {
		panic("fuxi-pmgr: ApplePMGR is not an IOService");
	}
	if (!pmgr->init()) {
		panic("fuxi-pmgr: init failed");
	}

	IOService *root = IOService::getServiceRoot();
	if (root == NULL) {
		panic("fuxi-pmgr: no service root");
	}
	if (!pmgr->attach(root)) {
		panic("fuxi-pmgr: attach failed");
	}
	pmgr->start(NULL);
	IOLog("fuxi-pmgr: ApplePMGR published, returning to AMFI");
}
