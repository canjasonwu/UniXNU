/*
 * Publish the ApplePMGR instance AMFI looks for.
 *
 * AMFI's ConfigurationSettings panics with "AMFI: No PMGR?" when no power
 * manager service is published, and nothing publishes one on hardware without
 * Apple's PMC. The ApplePMGR class lives in a kext of the prelinked kernelcache,
 * and at the bsd_init call site that kext may not be initialised yet, so load it
 * explicitly and then publish an instance through the OSMetaClass registry.
 */
#include <IOKit/IOService.h>
#include <IOKit/IOLib.h>
#include <libkern/c++/OSMetaClass.h>

extern "C" kern_return_t OSKextLoadKextWithIdentifier(const char *identifier);

extern "C" void
sheng_pmgr_run(void)
{
	kern_return_t status = OSKextLoadKextWithIdentifier("com.apple.driver.ApplePMGR");
	IOLog("fuxi-pmgr: kext load returned 0x%x", status);

	OSObject *object = OSMetaClass::allocClassWithName("ApplePMGR");
	if (object == NULL) {
		IOLog("fuxi-pmgr: no ApplePMGR class after the load");
		return;
	}

	IOService *pmgr = OSDynamicCast(IOService, object);
	if (pmgr == NULL) {
		object->release();
		IOLog("fuxi-pmgr: ApplePMGR is not an IOService");
		return;
	}
	if (!pmgr->init()) {
		pmgr->release();
		IOLog("fuxi-pmgr: init failed");
		return;
	}
	IOService *root = IOService::getServiceRoot();
	if (root == NULL || !pmgr->attach(root)) {
		pmgr->release();
		IOLog("fuxi-pmgr: attach failed");
		return;
	}
	pmgr->start(NULL);
	IOLog("fuxi-pmgr: ApplePMGR published");
}
