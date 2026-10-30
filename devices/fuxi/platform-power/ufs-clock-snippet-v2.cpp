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

