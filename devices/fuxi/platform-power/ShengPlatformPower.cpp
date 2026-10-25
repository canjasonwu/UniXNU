/*
 * Placeholder for the PMGR integration.
 *
 * AMFI panics with "No PMGR?" when no ApplePMGR service is published. The
 * kernelcache's ApplePMGR kext has a single personality, AppleBringUpPMGR, which
 * matches a device tree node with IONameMatch "pmgr1,bring-up" under an
 * AppleARMIODevice provider. The payload ADT now publishes that node
 * (devices/fuxi/scripts/make-fuxi-adt.py), so the original driver should start and
 * publish the service by itself, with no code here.
 *
 * This file stays as the hook bsd_init calls (XNU_SHENG_PMGR=1). Keep it a no-op
 * so it cannot preempt the device tree path.
 */
#include <IOKit/IOLib.h>

extern "C" void
sheng_pmgr_run(void)
{
	IOLog("fuxi-pmgr: the pmgr1 device tree node provides the PMGR service");
}
