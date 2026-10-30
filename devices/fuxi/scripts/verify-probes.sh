#!/usr/bin/env bash
# Verify that the bring-up probes are really in the built kernel and that the modified sources
# actually reference them.
#
# This exists because two failure modes cost real rounds:
#   1. code inserted into an unreachable branch (after a return) - the compiler drops it silently;
#   2. a source edit that the incremental build did not recompile - the old object stays.
# Both look identical from the device: the probe never fires. This script catches them offline.
#
# Usage: bash devices/fuxi/scripts/verify-probes.sh   (run from the repository root)
# Exit code 0 = everything found, 1 = at least one check failed.

set -u
ROOT=$(pwd)
KERNEL="$ROOT/out/fuxi/kernel/kernel.macho"
OBJDIR="$ROOT/out/fuxi/tree/build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837"
NM="${NM:-/opt/unixnu-linux/CommandLineTools/usr/bin/nm}"
fail=0

printf '%-28s %-8s %s\n' PROBE STATUS DETAIL
printf '%-28s %-8s %s\n' ----- ------ ------

check_string() {
	local name=$1 text=$2
	if [ ! -f "$KERNEL" ]; then
		printf '%-28s %-8s %s\n' "$name" MISSING "no kernel.macho at $KERNEL"
		fail=1
		return
	fi
	if strings -a "$KERNEL" 2>/dev/null | grep -qF -- "$text"; then
		printf '%-28s %-8s %s\n' "$name" ok "kernel contains \"$text\""
	else
		printf '%-28s %-8s %s\n' "$name" FAIL "kernel has no \"$text\""
		fail=1
	fi
}

check_ref() {
	local name=$1 obj=$2 symbol=$3
	if [ ! -f "$obj" ]; then
		printf '%-28s %-8s %s\n' "$name" MISSING "object absent: $(basename "$obj")"
		fail=1
		return
	fi
	if "$NM" -u "$obj" 2>/dev/null | grep -q -- "$symbol"; then
		printf '%-28s %-8s %s\n' "$name" ok "$(basename "$obj") references $symbol"
	else
		printf '%-28s %-8s %s\n' "$name" FAIL "$(basename "$obj") does NOT reference $symbol (stale object or dead code)"
		fail=1
	fi
}

check_string  el0-instr-abort   "fuxi-iabort-el0"
check_string  el0-data-abort    "fuxi-dabort-el0"
check_string  sigbus-source     "fuxi-sigbus"
check_string  vmfault-verdict   "fuxi-vmfault"
check_string  load-result-bad   "fuxi-load-bad"
check_string  decrypt-branch    "fuxi-decrypt"

check_ref     sleh-vmfault   "$OBJDIR/osfmk/DEVELOPMENT/sleh.o"          _fuxi_vmfault_panic
check_ref     mach-loader    "$OBJDIR/bsd/DEVELOPMENT/mach_loader.o"     _fuxi_load_bad
check_ref     kern-sig       "$OBJDIR/bsd/DEVELOPMENT/kern_sig.o"        _fuxi_trace_mark
check_ref     kern-exit      "$OBJDIR/bsd/DEVELOPMENT/kern_exit.o"       _fuxi_trace_wait
check_ref     kern-exec      "$OBJDIR/bsd/DEVELOPMENT/kern_exec.o"       _fuxi_trace_mark

echo
if [ "$fail" -ne 0 ]; then
	echo "RESULT: FAIL - rebuild with 'rm -f out/fuxi/kernel/kernel.macho' after touching the changed sources"
	exit 1
fi
echo "RESULT: ok - every probe is compiled into the kernel"
exit 0
