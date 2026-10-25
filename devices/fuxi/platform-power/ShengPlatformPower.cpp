/*
 * Publish the ApplePMGRNub service AMFI needs.
 *
 * AMFI does IOService::serviceMatching("ApplePMGRNub") + waitForMatchingService +
 * safeMetaCast to the ApplePMGRNub class, which com.apple.driver.ApplePMGR defines.
 * Nothing on this hardware creates that nub, and AMFI runs its early security
 * initialiser from OSKext::start, long before bsd_init.
 *
 * patches/xnu/zzz-pmgr-publish-at-kext-start.patch calls this at the head of
 * OSKext::start, where the kext dependencies (ApplePMGR) are already loaded, so the
 * class exists. The call is idempotent and quietly retries on later starts while the
 * class is still missing.
 */
#include <IOKit/IOService.h>
#include <IOKit/IOLib.h>
#include <libkern/c++/OSMetaClass.h>
#include <libkern/c++/OSSymbol.h>

static bool fuxi_pmgr_published = false;

extern "C" void
sheng_pmgr_publish_nub(void)
{
	if (fuxi_pmgr_published) {
		return;
	}
	const OSSymbol *symbol = OSSymbol::withCString("ApplePMGRNub");
	if (symbol == NULL) {
		return;
	}
	const OSMetaClass *meta = OSMetaClass::getMetaClassWithName(symbol);
	symbol->release();
	if (meta == NULL) {
		return;                     /* not loaded yet: try again on the next start */
	}
	OSObject *object = meta->alloc();
	if (object == NULL) {
		return;
	}
	IOService *nub = OSDynamicCast(IOService, object);
	if (nub == NULL) {
		object->release();
		return;
	}
	if (!nub->init()) {
		nub->release();
		return;
	}
	IOService *root = IOService::getServiceRoot();
	if (root == NULL || !nub->attach(root)) {
		nub->release();
		return;
	}
	fuxi_pmgr_published = true;
	nub->start(NULL);
	IOLog("fuxi-pmgr: ApplePMGRNub published before AMFI looked for it");
}

extern "C" void
sheng_pmgr_run(void)
{
	sheng_pmgr_publish_nub();
}

extern "C" void
sheng_pmgr_late_probe(void)
{
	sheng_pmgr_publish_nub();
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
