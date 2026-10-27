/*
 * PMGR integration: publish the ApplePMGRNub that AMFI waits for.
 *
 * AMFI does IOService::serviceMatching("ApplePMGRNub") + waitForMatchingService +
 * safeMetaCast to the ApplePMGRNub class. That class belongs to
 * com.apple.driver.ApplePMGR and nothing on this hardware creates an instance of it.
 * AMFI runs its early security initialiser from OSKext::start (see
 * kits/NativePrelink/patches/xnu-10063.141.1-security-init.patch), and
 * osfmk/kern/startup.c panics in trust_cache_runtime_init() when that initialiser did
 * not register the AMFI interface.
 *
 * patches/xnu/zzz-pmgr-publish-at-kext-start.patch calls this at the head of
 * OSKext::start, i.e. once the kext dependencies (ApplePMGR) are loaded and their
 * classes are registered, and before AMFI's initialiser runs.
 *
 * Confirmed on the phone: the class is visible there, and alloc/init/attach all
 * succeed, so the nub is published (a probe build reported status 7).
 */
#include <IOKit/IOService.h>
#include <IOKit/IOLib.h>
#include <libkern/c++/OSMetaClass.h>
#include <libkern/c++/OSSymbol.h>

static bool fuxi_pmgr_published = false;

/* Set to 1 to let an unsigned PID 1 exec. AMFI refuses an unsigned Mach-O with
 * EXEC_EXIT_REASON_SECURITY_POLICY (namespace 9, code 8) and kills init; this
 * switch records the refusal and continues instead (see
 * patches/xnu/zzz-unsigned-exec.patch). Bring-up only. */
int fuxi_allow_unsigned_exec = 1;

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
		return;                     /* class not loaded yet, retry next start */
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
	/* Attaching only wires the nub into the tree; AMFI finds services through the
	 * IORegistry match table, so the nub has to be registered before its
	 * serviceMatching("ApplePMGRNub") can succeed. */
	nub->registerService();
	IOLog("fuxi-pmgr: ApplePMGRNub published and registered before AMFI looked for it");
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
