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
#include <IOKit/IOMemoryDescriptor.h>

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

/*
 * Read-only UFS inspection, ported from the reference driver's NativeUfs::inspect().
 * The reference runs it before AMFI; here it runs from the late probe in bsd_autoconf,
 * which is late enough for IODeviceMemory and early enough to still panic with the data.
 *
 * No register is written. The UFS host controller is only read when GCC reports the PHY
 * powered and the AXI/AHB branch enables set, exactly as the reference does, because a
 * read on an unpowered controller can stall the bus.
 */
extern "C" int fuxi_ufs_probe_enabled = 0;

static uintptr_t
fuxi_map_phys(uint64_t physical, uint64_t length, OSSharedPtr<IOMemoryMap> &keep)
{
	IODeviceMemory *memory = IODeviceMemory::withRange(physical, length);
	if (memory == NULL) {
		return 0;
	}
	keep = OSSharedPtr<IOMemoryMap>(memory->map(kIOMapAnywhere | kIOMapInhibitCache), OSNoRetain);
	memory->release();
	return keep ? (uintptr_t)keep->getVirtualAddress() : 0;
}

static uint32_t
fuxi_read32(uintptr_t base, unsigned offset)
{
	return *(volatile uint32_t *)(base + offset);
}

#define FUXI_GCC_BASE      0x00100000ULL
#define FUXI_UFS_HC_BASE   0x01d84000ULL
#define FUXI_UFS_HC_SIZE   0x3000ULL

extern "C" void
fuxi_ufs_inspect(void)
{
	OSSharedPtr<IOMemoryMap> gccKeep, memPhyKeep, hcKeep;
	uint32_t pwrArg = 0, quick = 0;

	if (!PE_parse_boot_argn("fuxi-ufs-probe", &pwrArg, sizeof(pwrArg))) {
		return;
	}
	(void)PE_parse_boot_argn("fuxi-ufs-quick", &quick, sizeof(quick));

	uintptr_t gcc = fuxi_map_phys(FUXI_GCC_BASE + 0x77000, 0x1000, gccKeep);
	if (gcc == 0) {
		panic("fuxi-ufs-probe: GCC map failed");
	}
	uint32_t gdsc = fuxi_read32(gcc, 0x004);
	uint32_t axi = fuxi_read32(gcc, 0x018);
	uint32_t ahb = fuxi_read32(gcc, 0x024);
	uint32_t unipro = fuxi_read32(gcc, 0x068);
	uint32_t txs = fuxi_read32(gcc, 0x028);
	uint32_t rxs = fuxi_read32(gcc, 0x02c);
	uint32_t aux = fuxi_read32(gcc, 0x0b0);

	/* GDSCR bit 31 is PWR_ON; CBCR bit 31 is CLK_OFF, enable is bit 0. */
	bool powered = (gdsc & 0x80000000U) != 0;
	bool busClocks = !(axi & 0x80000000U) && (ahb & 0x1U) && (axi & 0x1U);

	uint32_t cap = 0, ver = 0, is = 0, hcs = 0, hce = 0;
	unsigned readHc = 0;
	if (quick == 0 && powered && busClocks) {
		uintptr_t hc = fuxi_map_phys(FUXI_UFS_HC_BASE, FUXI_UFS_HC_SIZE, hcKeep);
		if (hc != 0) {
			cap = fuxi_read32(hc, 0x00);
			ver = fuxi_read32(hc, 0x08);
			is = fuxi_read32(hc, 0x20);
			hcs = fuxi_read32(hc, 0x30);
			hce = fuxi_read32(hc, 0x34);
			readHc = 1;
		}
	}

	panic("fuxi-ufs-probe: gdsc=%08x axi=%08x ahb=%08x\n"
	    "unipro=%08x txs=%08x rxs=%08x aux=%08x\n"
	    "powered=%u busclocks=%u hc_read=%u\n"
	    "cap=%08x ver=%08x is=%08x hcs=%08x hce=%08x",
	    gdsc, axi, ahb, unipro, txs, rxs, aux,
	    (unsigned)powered, (unsigned)busClocks, readHc,
	    cap, ver, is, hcs, hce);
}

extern "C" void
sheng_pmgr_late_probe(void)
{
	sheng_pmgr_publish_nub();
	fuxi_ufs_inspect();
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
