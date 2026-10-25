/*
 * A stand-in for com.apple.driver.ApplePMGR.
 *
 * AMFI's ConfigurationSettings panics with "AMFI: No PMGR?" on hardware that has
 * no Apple power manager, which stops the kernel in bsd_init before anything
 * user visible runs. sheng solved this with a platform-power component that
 * publishes a PMGR service; this is the smallest version of that for fuxi: it
 * only has to satisfy the lookup, not to manage power domains.
 *
 * The C++ class has its own name: the real ApplePMGR class is already in the
 * prelinked kexts, and registering a second class with that name panics
 * OSMetaClass. IOService matching reads the IOClass property, not the C++ type,
 * so the instance still matches serviceMatching("ApplePMGR").
 *
 * bsd_init calls sheng_pmgr_run() early (patches/xnu/sheng-platform-power.patch)
 * with XNU_SHENG_PMGR=1, so the service exists before AMFI starts.
 */
#include <IOKit/IOService.h>
#include <IOKit/IOLib.h>

#define super IOService

class FuxiPMGR : public IOService
{
	OSDeclareDefaultStructors(FuxiPMGR);

public:
	bool start(IOService *provider) APPLE_KEXT_OVERRIDE;
};

OSDefineMetaClassAndStructors(FuxiPMGR, IOService)

bool
FuxiPMGR::start(IOService *provider)
{
	if (!super::start(provider)) {
		return false;
	}
	registerService();
	return true;
}

extern "C" void
sheng_pmgr_run(void)
{
	IOService *root = IOService::getServiceRoot();
	if (root == NULL) {
		IOLog("fuxi-pmgr: no service root yet, skipping\n");
		return;
	}

	FuxiPMGR *pmgr = new FuxiPMGR;
	if (pmgr == NULL) {
		return;
	}
	if (!pmgr->init()) {
		pmgr->release();
		return;
	}

	/* AMFI matches on the class name, these are for anything that looks at the
	 * registry entry instead. */
	pmgr->setProperty("IOClass", "ApplePMGR");
	pmgr->setProperty("CFBundleIdentifier", "com.apple.driver.ApplePMGR");
	pmgr->setProperty("IOProviderClass", "IOResources");

	if (!pmgr->attach(root)) {
		IOLog("fuxi-pmgr: attach failed\n");
		pmgr->release();
		return;
	}
	pmgr->start(NULL);
	IOLog("fuxi-pmgr: published ApplePMGR stub\n");
}
