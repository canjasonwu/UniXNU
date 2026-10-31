# Bring-up patches from the 2026-10-31 / 2026-11-01 device rounds

These are the exact kernel edits that produced the working images A16/A17/A18 (userland runs and
`open()` returns success). They are captured here so they cannot be lost with the build tree; they are
**not** added to the automatic patch list yet, because each one still needs a review decision:

1. `OSKext::updateLoadedKextSummaries` always takes the fresh-allocation path.
   Why: the reuse path calls `vm_map_protect(kernel_map, start, end, VM_PROT_DEFAULT)` and on this
   kernel that returns KERN_SUCCESS while leaving the last pages read-only (device evidence on image
   A15: ks_branch=2 ks_size=0x5000 ks_old=0x5000 ks_prot=0, and the bzero still took an EL1
   permission fault at dest+0x3000). The bypass avoids depending on that defect.
   Proper fix: understand why vm_map_protect/pmap_protect under-applies with the 4 KiB page ABI.

2. `sleh.c` bring-up diagnostics:
   - EL0 data-abort counter + `fuxi-fault-loop` panic (catches livelocks in the user-fault path).
   - EL1 data-abort counter + `fuxi-el1-abort-loop` panic printing esr/far/pc/x0/x2/lr plus the
     kext-summary accounting.
   - The EL1 diagnostic is skipped when `current_thread()->machine.expected_fault_handler != NULL`,
     so it never pre-empts a fault the kernel intends to recover (copyin/copyout).
   - `%x` -> `%llx` for 64-bit values in the abort panic strings (the old form truncated far to 32
     bits and sent one analysis round down the wrong path).
   These are diagnostics, not behaviour. They should become boot-arg gated or be deleted before a
   "clean" kernel, but they are what proved this week's root causes.

3. `mach_loader.c` load-bad condition: `size == 0` was removed (it is a legal value substituted from
   RLIMIT_STACK in kern_exec.c); the condition is `entry == 0 || sp == 0 || sp >= 0xfffffff000000000`.

The PAN fix is not in this patch: it is the defconfig switch `CONFIG_XNU_NATIVE_PAN=y` plus the
pre-existing `patches/xnu/native-arm-pan.patch`, which defines `__ARM_PAN_AVAILABLE__` in the board
header. Without it every copyin/copyout from user memory faults, because the hardware sets PAN=1 on
each EL0->EL1 transition and `user_access_enable()` compiles to a no-op.
