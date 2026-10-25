/*
 * Publish the ApplePMGR instance AMFI looks for.
 *
 * AMFI's ConfigurationSettings panics with "AMFI: No PMGR?" when no power
 * manager service is published, and on hardware without Apple's PMC nothing
 * ever publishes one. Rather than reimplementing the class (sheng subclassed
 * ApplePMGR with a generated 340 slot ABI), instantiate the ApplePMGR class
 * that the prelinked kernelcache already contains, through the OSMetaClass
 * registry, and publish it.
 *
 * bsd_init calls sheng_pmgr_run() early (patches/xnu/sheng-platform-power.patch,
 * XNU_SHENG_PMGR=1) so the service exists before AMFI starts.
 */
#include <IOKit/IOService.h>
#include <IOKit/IOLib.h>
#include <libkern/c++/OSMetaClass.h>

extern "C" void
sheng_pmgr_run(void)
{
	OSObject *object = OSMetaClass::allocClassWithName("ApplePMGR");
	if (object == NULL) {
		IOLog("fuxi-pmgr: no ApplePMGR class in the kernel
");
		return;
	}

	IOService *pmgr = OSDynamicCast(IOService, object);
	if (pmgr == NULL) {
		object->release();
		IOLog("fuxi-pmgr: ApplePMGR is not an IOService
");
		return;
	}
	if (!pmgr->init()) {
		pmgr->release();
		IOLog("fuxi-pmgr: ApplePMGR init failed
");
		return;
	}

	IOService *root = IOService::getServiceRoot();
	if (root == NULL || !pmgr->attach(root)) {
		pmgr->release();
		IOLog("fuxi-pmgr: attach failed
");
		return;
	}
	pmgr->start(NULL);
	IOLog("fuxi-pmgr: ApplePMGR published
");
}
