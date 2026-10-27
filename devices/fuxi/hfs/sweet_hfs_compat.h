/*
 * Compile Apple's HFS kext sources inside this kernel the way the kext is built:
 * with the kext (KPI) view of the kernel headers, and with only HFS's own
 * kext-config.h deciding features. The kernel's -DCONFIG_* options are not visible
 * to a kext build, so the ones HFS tests are cleared first.
 *
 * Adapted from bangprovn/ios-fuxi's bsd/sweet_hfs/sweet_hfs_compat.h (same approach);
 * the SWEET-specific naming is replaced and content protection stays off because
 * AppleKeyStore is not built here either.
 */
#ifndef SWEET_HFS_COMPAT_H
#define SWEET_HFS_COMPAT_H

#undef BSD_KERNEL_PRIVATE
#undef XNU_KERNEL_PRIVATE
#undef CONFIG_PROTECT
#undef CONFIG_SEARCHFS
#undef CONFIG_HFS_DIRLINK
#undef CONFIG_SECLUDED_RENAME
#undef CONFIG_MACF
#undef CONFIG_HFS_RBTREE
#undef QUOTA
#undef NAMEDSTREAMS
#undef FIFO
#undef HFS_COMPRESSION

#include "core/kext-config.h"

/* kext-config.h turns content protection back on; this kernel has no
 * AppleKeyStore, so keep it off for every header that follows. */
#undef CONFIG_PROTECT


/* Built in, not a loadable kext (see fuxi_hfs_iokit.c). */
#define OSKextGetCurrentLoadTag sweet_hfs_current_load_tag

/*
 * This kernel is built without the namespace (searchfs) support that HFS assumes: it
 * calls resolve_nspace_item() for dataless files and nspace_snapshot_event() on metadata
 * changes. Neither exists here, and HFS+ volumes without dataless files never need them,
 * so the event becomes a no-op and the lookup reports "not a namespace item".
 */
static inline int
fuxi_resolve_nspace_item(void *vp, unsigned op)
{
	(void)vp;
	(void)op;
	return 0;
}
#define resolve_nspace_item(vp, op) fuxi_resolve_nspace_item((void *)(vp), (unsigned)(op))
#define nspace_snapshot_event(...) 0

#endif /* SWEET_HFS_COMPAT_H */
