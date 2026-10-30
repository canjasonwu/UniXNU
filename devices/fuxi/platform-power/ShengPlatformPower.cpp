/* osfmk/console/video_console.c keeps the frame buffer geometry in this global, and its
 * v_baseaddr is a kernel virtual address (the panic screen reads the same structure).
 * PE_state.video holds the physical address and must not be written with the MMU on. */
struct fuxi_vc_info {
	unsigned int v_height;
	unsigned int v_width;
	unsigned int v_depth;
	unsigned int v_rowbytes;
	unsigned long v_baseaddr;
	unsigned int v_type;
	char v_name[32];
	unsigned long long v_physaddr;
	unsigned int v_rows;
	unsigned int v_columns;
	unsigned int v_rowscanbytes;
	unsigned int v_scale;
	unsigned int v_rotate;
	unsigned int v_reserved[3];
};
extern "C" struct fuxi_vc_info vinfo;

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


/* Stage counter for bring-up: one white square per stage, so a single photograph shows how
 * far the kernel got before it hung (there is no serial console). Row is below the
 * entry-probe bands; index selects the column. */




/* NativeUfs is written as an IOService matched from a kext personality. This kernel links the driver
 * into the image and has no personality registry for it, so the driver's own file carries a C hook
 * (devices/fuxi/scripts/nativeufs-start-hook.py) that allocates, attaches and starts it against the
 * platform expert device. The symbol only exists when the driver is linked, hence the weak reference.
 * Gated by the boot argument fuxi-ufs-start so ordinary images never touch the controller this way. */
/* The NativeUfs archive installs the real hook through a static constructor when the driver is
 * linked in (devices/fuxi/NativeUfsStartHook.cpp). Without the driver this stays null. */
extern "C" int (*fuxi_ufs_start_hook)(void) = NULL;

/*
 * UFS clock-branch enable for bring-up.
 *
 * SM8550 GCC offsets come from NativeUfs/GccUfs.h, transcribed there from the public Linux
 * gcc-sm8550.c clock driver. Only the clock branch enable bit (bit 0) is written. Reset lines,
 * RPMh/TCSR and the QMP PHY are deliberately untouched: their semantics are not qualified here.
 *
 * The GDSC power state is read and reported but not changed, because bringing a power domain up
 * needs RPMh, which is out of scope for this probe.
 *
 * Gated by the boot argument fuxi-ufs-clock=1 so ordinary images never touch the clock controller.
 */
/*
 * UFS clock-branch enable for bring-up, in two forms:
 *   fuxi_ufs_clock_enable()  enables the missing clock branch bits and records what changed
 *   fuxi_ufs_clock()        calls the above and panics with the register values (image W behaviour)
 *
 * SM8550 GCC offsets come from NativeUfs/GccUfs.h, transcribed there from the public Linux
 * gcc-sm8550.c clock driver. Only the clock branch enable bit (bit 0) is written. Reset lines,
 * RPMh/TCSR and the QMP PHY are deliberately untouched: their semantics are not qualified here.
 *
 * The GDSC power state is read and reported but not changed, because bringing a power domain up
 * needs RPMh, which is out of scope for this probe.
 *
 * Gated by the boot argument fuxi-ufs-clock=1 so ordinary images never touch the clock controller.
 */
#define FUXI_GCC_UFS_WINDOW  0x00177000ULL
#define FUXI_GCC_UFS_SIZE    0x1000ULL

static uint32_t fuxi_ufs_clock_before[8];
static uint32_t fuxi_ufs_clock_after[8];
static unsigned fuxi_ufs_clock_written;
static bool fuxi_ufs_clock_powered;
static bool fuxi_ufs_clock_done;

static uint32_t
fuxi_gcc_read(uintptr_t base, unsigned offset)
{
	return *(volatile uint32_t *)(base + offset);
}

static void
fuxi_gcc_write(uintptr_t base, unsigned offset, uint32_t value)
{
	*(volatile uint32_t *)(base + offset) = value;
	__asm__ volatile("dsb sy; isb" ::: "memory");
}

/* Enables the UFS clock branches that are off. Returns 0 when the GCC window cannot be mapped, 1
 * otherwise. Results are kept in the file-scope arrays for reporting by either caller. */
static int
fuxi_ufs_clock_enable(void)
{
	OSSharedPtr<IOMemoryMap> gccKeep;
	uintptr_t gcc;

	if (fuxi_ufs_clock_done) {
		return 1;
	}
	gcc = fuxi_map_phys(FUXI_GCC_UFS_WINDOW, FUXI_GCC_UFS_SIZE, gccKeep);
	if (gcc == 0) {
		return 0;
	}

	/* Offsets are relative to 0x77000, which is where this window starts. Index order below is the
	 * order used in the reporting panic: gdsc, axi, ahb, unipro, tx, rx, aux, aggre. */
	unsigned const offset[8] = {0x004, 0x018, 0x024, 0x068, 0x028, 0x02c, 0x0b0, 0x0e4};

	for (unsigned index = 0; index < 8; index++) {
		fuxi_ufs_clock_before[index] = fuxi_gcc_read(gcc, offset[index]);
	}
	fuxi_ufs_clock_powered = (fuxi_ufs_clock_before[0] & 0x80000000U) != 0;

	/* Bit 0 is the branch enable; bit 31 is CLK_OFF and read-only. The AHB and aggregate branches
	 * are left clock-gated when idle on this hardware, which is expected and harmless to enable for
	 * the duration of a bring-up probe. GDSCR (index 0) is never written. */
	fuxi_ufs_clock_written = 0;
	for (unsigned index = 1; index < 8; index++) {
		if ((fuxi_ufs_clock_before[index] & 1U) == 0) {
			fuxi_gcc_write(gcc, offset[index], fuxi_ufs_clock_before[index] | 1U);
			fuxi_ufs_clock_written++;
		}
	}

	for (unsigned index = 0; index < 8; index++) {
		fuxi_ufs_clock_after[index] = fuxi_gcc_read(gcc, offset[index]);
	}
	fuxi_ufs_clock_done = true;
	return 1;
}

extern "C" void
fuxi_ufs_clock(void)
{
	uint32_t enabled = 0;
	if (!PE_parse_boot_argn("fuxi-ufs-clock", &enabled, sizeof(enabled)) || enabled == 0) {
		return;
	}
	if (!fuxi_ufs_clock_enable()) {
		panic("fuxi-ufs-clock: GCC map failed");
	}
	panic("fuxi-ufs-clock: powered=%u written=%u gdsc=%08x axi=%08x ahb=%08x unipro=%08x "
	    "tx=%08x rx=%08x aux=%08x aggre=%08x | after gdsc=%08x axi=%08x ahb=%08x unipro=%08x "
	    "tx=%08x rx=%08x aux=%08x aggre=%08x",
	    (unsigned)fuxi_ufs_clock_powered, fuxi_ufs_clock_written,
	    fuxi_ufs_clock_before[0], fuxi_ufs_clock_before[1], fuxi_ufs_clock_before[2],
	    fuxi_ufs_clock_before[3], fuxi_ufs_clock_before[4], fuxi_ufs_clock_before[5],
	    fuxi_ufs_clock_before[6], fuxi_ufs_clock_before[7],
	    fuxi_ufs_clock_after[0], fuxi_ufs_clock_after[1], fuxi_ufs_clock_after[2],
	    fuxi_ufs_clock_after[3], fuxi_ufs_clock_after[4], fuxi_ufs_clock_after[5],
	    fuxi_ufs_clock_after[6], fuxi_ufs_clock_after[7]);
}

extern "C" void
fuxi_ufs_start(void)
{
	uint32_t enabled = 0, clock = 0;
	if (!PE_parse_boot_argn("fuxi-ufs-start", &enabled, sizeof(enabled)) || enabled == 0) {
		return;
	}
	if (PE_parse_boot_argn("fuxi-ufs-clock", &clock, sizeof(clock)) && clock != 0) {
		if (!fuxi_ufs_clock_enable()) {
			panic("fuxi-ufs-start: clock enable failed (GCC map)");
		}
	}
	if (fuxi_ufs_start_hook == NULL) {
		panic("fuxi-ufs-start: driver not linked (clock powered=%u written=%u)",
		    (unsigned)fuxi_ufs_clock_powered, fuxi_ufs_clock_written);
	}
	int result = fuxi_ufs_start_hook();
	panic("fuxi-ufs-start: hook=%d clock_powered=%u clock_written=%u",
	    result, (unsigned)fuxi_ufs_clock_powered, fuxi_ufs_clock_written);
}

/* Defined further down; declared here because the late probe calls them. */
extern "C" void fuxi_mdev_test(void);
extern "C" int fuxi_rootdev_override(int current);
extern "C" void fuxi_ufs_clock(void);
extern "C" void fuxi_ufs_start(void);

extern "C" void
sheng_pmgr_late_probe(void)
{
	sheng_pmgr_publish_nub();
	fuxi_ufs_inspect();
	fuxi_ufs_clock();
	fuxi_mdev_test();
	fuxi_ufs_start();
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

/* Bring-up display: three seven-segment numbers, drawn large and re-drawn completely on every
 * update so no value is ever lost:
 *   white  = current stage index (2 digits, 0..63)
 *   cyan   = WTERMSIG of init (2 digits)
 *   yellow = WEXITSTATUS of init (1 digit)
 * Squares were abandoned: counting them was ambiguous and later marks cleared the area. */
static unsigned fuxi_stage;
static unsigned fuxi_signal;
static unsigned fuxi_status;

static void
fuxi_seg(unsigned long x, unsigned long y, unsigned long w, unsigned long h, int seg, uint32_t colour)
{
	struct fuxi_vc_info *v = &vinfo;
	/* segment order: 0 top, 1 top-right, 2 bottom-right, 3 bottom, 4 bottom-left, 5 top-left, 6 middle */
	unsigned long x0 = x, y0 = y, xs = w, ys = h;
	switch (seg) {
	case 0: y0 = y; xs = w; ys = h / 8; break;
	case 1: x0 = x + w - h / 8; y0 = y; xs = h / 8; ys = h / 2; break;
	case 2: x0 = x + w - h / 8; y0 = y + h / 2; xs = h / 8; ys = h / 2; break;
	case 3: y0 = y + h - h / 8; xs = w; ys = h / 8; break;
	case 4: x0 = x; y0 = y + h / 2; xs = h / 8; ys = h / 2; break;
	case 5: x0 = x; y0 = y; xs = h / 8; ys = h / 2; break;
	default: y0 = y + h / 2 - h / 16; xs = w; ys = h / 8; break;
	}
	unsigned long row = v->v_baseaddr + y0 * v->v_rowbytes + x0 * 4UL;
	for (unsigned long dy = 0; dy < ys; dy++) {
		volatile uint32_t *p = (volatile uint32_t *)(row + dy * v->v_rowbytes);
		for (unsigned long dx = 0; dx < xs; dx++) {
			p[dx] = colour;
		}
	}
}

static const unsigned char fuxi_digit_segments[10] = {
	0x3f, 0x06, 0x5b, 0x4f, 0x66, 0x6d, 0x7d, 0x07, 0x7f, 0x6f
};

static void
fuxi_digit(unsigned long x, unsigned long y, unsigned long w, unsigned long h, unsigned d, uint32_t colour)
{
	unsigned char mask = fuxi_digit_segments[d % 10];
	for (int seg = 0; seg < 7; seg++) {
		if (mask & (1 << seg)) {
			fuxi_seg(x, y, w, h, seg, colour);
		}
	}
}

static void
fuxi_clear(unsigned long x, unsigned long y, unsigned long w, unsigned long h)
{
	struct fuxi_vc_info *v = &vinfo;
	for (unsigned long dy = 0; dy < h; dy++) {
		volatile uint32_t *p = (volatile uint32_t *)(v->v_baseaddr + (y + dy) * v->v_rowbytes + x * 4UL);
		for (unsigned long dx = 0; dx < w; dx++) {
			p[dx] = 0xff000000u;
		}
	}
}

static void
fuxi_draw(void)
{
	struct fuxi_vc_info *v = &vinfo;
	if (v->v_depth != 32 || v->v_baseaddr == 0 || v->v_rowbytes == 0) {
		return;
	}
	unsigned long h = v->v_height / 12;
	unsigned long w = h / 2;
	unsigned long y = v->v_height * 68 / 100;
	unsigned long x = v->v_width / 12;
	if (y + h >= v->v_height || x + 8 * w >= v->v_width) {
		return;
	}
	fuxi_clear(x, y, 9 * w + w, h);
	fuxi_digit(x, y, w, h, (fuxi_stage / 10) % 10, 0xffffffffu);
	fuxi_digit(x + w + w / 2, y, w, h, fuxi_stage % 10, 0xffffffffu);
	fuxi_digit(x + 4 * w, y, w, h, (fuxi_signal / 10) % 10, 0xff00ffffu);
	fuxi_digit(x + 5 * w + w / 2, y, w, h, fuxi_signal % 10, 0xff00ffffu);
	fuxi_digit(x + 8 * w, y, w, h, fuxi_status % 10, 0xffffff00u);
}

extern "C" void
fuxi_trace_mark(int index)
{
	fuxi_stage = (unsigned)index;
	fuxi_draw();
}

extern "C" void
fuxi_trace_wait(int wait_status)
{
	fuxi_status = ((unsigned)wait_status >> 8) & 0xfu;
	fuxi_signal = (unsigned)wait_status & 0x7fu;
	fuxi_draw();
}

extern "C" void
fuxi_trace_abort(unsigned esr)
{
	/* Report the exception class and fault status code of a user abort. */
	fuxi_stage = (esr >> 26) & 0x3fu;
	fuxi_signal = esr & 0x3fu;
	fuxi_status = 9;
	fuxi_draw();
}

extern "C" void
fuxi_trace_page(unsigned page)
{
	/* Show the faulting kernel PC as a page index from the kernel __TEXT base:
	 * white = page/100, cyan = page%100, yellow = 1 (this is a PC report). */
	fuxi_stage = (page / 100u) % 100u;
	fuxi_signal = page % 100u;
	fuxi_status = 1;
	fuxi_draw();
}

extern "C" void
fuxi_exec_header_panic(unsigned magic, unsigned ncmds, unsigned sizeofcmds,
    long long macho_size, const uint32_t *cmd)
{
	panic("fuxi-exec: size=%llu magic=%x ncmds=%u cmds=%x cmd0=%x cs0=%x cmd1=%x cs1=%x",
	    (unsigned long long)macho_size, magic, ncmds, sizeofcmds, cmd[0], cmd[1], cmd[2], cmd[3]);
}

extern "C" void
fuxi_decrypt_panic(unsigned cmd, unsigned cmdsize, unsigned cryptid, unsigned cryptoff,
    unsigned cryptsize, unsigned offset)
{
	panic("fuxi-decrypt: cmd=%x cmdsize=%x id=%x off=%x size=%x at=%x",
	    cmd, cmdsize, cryptid, cryptoff, cryptsize, offset);
}

extern "C" void
fuxi_load_panic(unsigned long long entry, unsigned long long sp, unsigned long long stack,
    unsigned long long size)
{
	panic("fuxi-load: entry=%llx sp=%llx stack=%llx size=%llx", entry, sp, stack, size);
}

extern "C" void
fuxi_vmfault_panic(unsigned result, unsigned long long addr, unsigned prot, unsigned long long pc,
    unsigned long long sp)
{
	panic("fuxi-vmfault: ret=%x addr=%llx prot=%x pc=%llx sp=%llx",
	    result, addr, prot, pc, sp);
}

extern "C" void
fuxi_load_bad(unsigned long long entry, unsigned long long sp, unsigned long long size)
{
	panic("fuxi-load-bad: entry=%llx sp=%llx size=%llx", entry, sp, size);
}

/*
 * RAM root wiring (M2 prototype).
 *
 * Apple's IOFindBSDRoot turns the ADT RAMDisk into /dev/md0 through mdevadd(). The same call lets any
 * wired RAM buffer become an md device, which is the plan for a root image read from userdata.
 *
 * fuxi_mdev_register() registers such a buffer and remembers the device number.
 * fuxi_rootdev_override() returns that device instead of the discovered root, so vfs_mountroot mounts
 * the registered buffer. It is a no-op unless the buffer was registered.
 *
 * fuxi_mdev_test() exercises the whole path without any hardware access: with the boot argument
 * fuxi-mdev-test=1 it takes the RAMDisk the loader already placed in /chosen/memory-map, registers it
 * as a second md device and lets the override switch the root to it. Content is identical to md0, so a
 * successful boot proves the register-then-override mechanism works.
 */
extern "C" int mdevadd(int devid, uint64_t base, unsigned int size, int phys);
extern const IORegistryPlane *gIODTPlane;

static int fuxi_md_dev = -1;

extern "C" int
fuxi_rootdev_override(int current)
{
	/* Silent on purpose: the interesting evidence is whether the boot then mounts and runs from the
	 * registered device. fuxi-mdev-test=1 reports the registration itself and stops there. */
	if (fuxi_md_dev >= 0) {
		return fuxi_md_dev;
	}
	return current;
}

extern "C" void
fuxi_mdev_test(void)
{
	uint32_t enabled = 0;
	if (!PE_parse_boot_argn("fuxi-mdev-test", &enabled, sizeof(enabled)) || enabled == 0) {
		return;
	}

	IORegistryEntry *map = IORegistryEntry::fromPath("/chosen/memory-map", gIODTPlane);
	if (map == NULL) {
		panic("fuxi-mdev-test: /chosen/memory-map missing");
	}
	OSData *data = OSDynamicCast(OSData, map->getProperty("RAMDisk"));
	if (data == NULL || data->getLength() < 2 * sizeof(uintptr_t)) {
		panic("fuxi-mdev-test: RAMDisk property missing or short");
	}
	const uintptr_t *params = (const uintptr_t *)data->getBytesNoCopy();
	uint64_t base = params[0];
	uint64_t bytes = params[1];
	map->release();

	int dev = mdevadd(-1, base >> 12, (unsigned int)(bytes >> 12), 0);
	if (dev < 0) {
		panic("fuxi-mdev-test: mdevadd failed (%d) base=%llx bytes=%llx", dev,
		    (unsigned long long)base, (unsigned long long)bytes);
	}
	fuxi_md_dev = dev;
	if (enabled == 1) {
		/* Report-only mode: stop before mountroot so the values are visible on screen. */
		panic("fuxi-mdev-test: registered md dev 0x%x base=%llx bytes=%llx", (unsigned)dev,
		    (unsigned long long)base, (unsigned long long)bytes);
	}
}
