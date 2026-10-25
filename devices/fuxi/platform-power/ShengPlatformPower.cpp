/*
 * Publish an ApplePMGR instance before AMFI looks for one.
 *
 * AMFI's ConfigurationSettings panics with "No PMGR?" when it cannot find a
 * PMGR instance. Loading the ApplePMGR kext is not enough at bsd_init time: the
 * kext reports success but its class is not registered yet (probe confirmed on
 * the phone), and nothing publishes a service before AMFI starts.
 *
 * So the class lives in the kernel instead, the way sheng does it: ApplePMGR
 * derives from IOPMGR, implements its seven pure virtual power hooks as no-ops,
 * and is published from bsd_init. The ApplePMGR kext is removed from the prelink
 * set, otherwise it would register a second class with the same name.
 */
#include <IOKit/IOPMGR.h>
#include <IOKit/IOLib.h>

class ApplePMGR : public IOPMGR
{
	OSDeclareDefaultStructors(ApplePMGR);
public:
	void disableCPUCore(unsigned int) APPLE_KEXT_OVERRIDE {}
	void enableCPUCluster(unsigned int) APPLE_KEXT_OVERRIDE {}
	void disableCPUCluster(unsigned int) APPLE_KEXT_OVERRIDE {}
	void initCPUIdle(ml_processor_info_t *) APPLE_KEXT_OVERRIDE {}
	void enterCPUIdle(UInt64 *) APPLE_KEXT_OVERRIDE {}
	void exitCPUIdle(UInt64 *) APPLE_KEXT_OVERRIDE {}
	void updateCPUIdle(UInt64 *) APPLE_KEXT_OVERRIDE {}
};

OSDefineMetaClassAndStructors(ApplePMGR, IOPMGR)

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
	ApplePMGR *pmgr = new ApplePMGR;
	if (pmgr == NULL) {
		panic("fuxi-pmgr: allocation failed");
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
	IOLog("fuxi-pmgr: ApplePMGR published from the kernel");
}
