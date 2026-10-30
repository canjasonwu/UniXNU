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
#define FUXI_GCC_UFS_WINDOW  0x00177000ULL
#define FUXI_GCC_UFS_SIZE    0x1000ULL

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

extern "C" void
fuxi_ufs_clock(void)
{
	uint32_t enabled = 0;
	if (!PE_parse_boot_argn("fuxi-ufs-clock", &enabled, sizeof(enabled)) || enabled == 0) {
		return;
	}

	OSSharedPtr<IOMemoryMap> gccKeep;
	uintptr_t gcc = fuxi_map_phys(FUXI_GCC_UFS_WINDOW, FUXI_GCC_UFS_SIZE, gccKeep);
	if (gcc == 0) {
		panic("fuxi-ufs-clock: GCC map failed");
	}

	/* Offsets are relative to 0x77000, which is where this window starts. */
	unsigned const offGdsc = 0x004;
	unsigned const offAxi = 0x018;
	unsigned const offAhb = 0x024;
	unsigned const offTx = 0x028;
	unsigned const offRx = 0x02c;
	unsigned const offUnipro = 0x068;
	unsigned const offIce = 0x074;
	unsigned const offAux = 0x0b0;
	unsigned const offRx1 = 0x0cc;
	unsigned const offAggre = 0x0e4;

	uint32_t gdsc0 = fuxi_gcc_read(gcc, offGdsc);
	uint32_t axi0 = fuxi_gcc_read(gcc, offAxi);
	uint32_t ahb0 = fuxi_gcc_read(gcc, offAhb);
	uint32_t tx0 = fuxi_gcc_read(gcc, offTx);
	uint32_t rx0 = fuxi_gcc_read(gcc, offRx);
	uint32_t unipro0 = fuxi_gcc_read(gcc, offUnipro);
	uint32_t ice0 = fuxi_gcc_read(gcc, offIce);
	uint32_t aux0 = fuxi_gcc_read(gcc, offAux);
	uint32_t rx1_0 = fuxi_gcc_read(gcc, offRx1);
	uint32_t aggre0 = fuxi_gcc_read(gcc, offAggre);

	bool powered = (gdsc0 & 0x80000000U) != 0;

	/* Enable only the branches that are off. Bit 0 is the branch enable; bit 31 is CLK_OFF and
	 * read-only. The AHB and aggregate branches are left clock-gated when idle on this hardware,
	 * which is expected and harmless to enable for the duration of a bring-up probe. */
	if ((axi0 & 1U) == 0) {
		fuxi_gcc_write(gcc, offAxi, axi0 | 1U);
	}
	if ((ahb0 & 1U) == 0) {
		fuxi_gcc_write(gcc, offAhb, ahb0 | 1U);
	}
	if ((aggre0 & 1U) == 0) {
		fuxi_gcc_write(gcc, offAggre, aggre0 | 1U);
	}
	if ((unipro0 & 1U) == 0) {
		fuxi_gcc_write(gcc, offUnipro, unipro0 | 1U);
	}
	if ((tx0 & 1U) == 0) {
		fuxi_gcc_write(gcc, offTx, tx0 | 1U);
	}
	if ((rx0 & 1U) == 0) {
		fuxi_gcc_write(gcc, offRx, rx0 | 1U);
	}
	if ((aux0 & 1U) == 0) {
		fuxi_gcc_write(gcc, offAux, aux0 | 1U);
	}

	panic("fuxi-ufs-clock: powered=%u gdsc=%08x axi=%08x ahb=%08x unipro=%08x tx=%08x rx=%08x "
	    "aux=%08x aggre=%08x rx1=%08x ice=%08x | after gdsc=%08x axi=%08x ahb=%08x unipro=%08x "
	    "tx=%08x rx=%08x aux=%08x aggre=%08x",
	    (unsigned)powered,
	    gdsc0, axi0, ahb0, unipro0, tx0, rx0, aux0, aggre0, rx1_0, ice0,
	    fuxi_gcc_read(gcc, offGdsc), fuxi_gcc_read(gcc, offAxi), fuxi_gcc_read(gcc, offAhb),
	    fuxi_gcc_read(gcc, offUnipro), fuxi_gcc_read(gcc, offTx), fuxi_gcc_read(gcc, offRx),
	    fuxi_gcc_read(gcc, offAux), fuxi_gcc_read(gcc, offAggre));
}

