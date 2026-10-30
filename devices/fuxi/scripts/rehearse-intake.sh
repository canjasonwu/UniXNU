#!/usr/bin/env bash
# Rehearse the IPSW intake chain with a synthetic root, without a device and without QEMU.
#
# The chain under test is the one a real IPSW root will go through:
#   1. check-ios-root.py checks the paths, the arm64 Mach-O headers and the shared cache shards
#   2. populate-hfs.py --dry-run builds the transfer tree (no guest) so the drop list is verified
#   3. build-userdata-root.sh packs a volume into a padded userdata image and prints the flash plan
#
# The synthetic root uses this project's own probe binary as both launchd and dyld, plus a page-aligned
# shared cache with the dyld_v1 magic, so every check exercises its real code path without needing an
# Apple firmware image.
#
# Usage: bash devices/fuxi/scripts/rehearse-intake.sh [--keep]
# Exit code 0 when every stage reports success.

set -euo pipefail

KEEP=0
if [ "${1:-}" = "--keep" ]; then
	KEEP=1
fi

ROOT=/root/UniXNU
WORK=/tmp/fuxi-intake-rehearsal
PROBE="$ROOT/devices/fuxi/experiments"
CLT=/opt/unixnu-linux/CommandLineTools
PY=python3

step() { echo; echo "=== $* ==="; }

rm -rf "$WORK"
mkdir -p "$WORK/root/sbin" "$WORK/root/usr/lib" \
         "$WORK/root/System/Library/Caches/com.apple.dyld" \
         "$WORK/root/System/Library/LaunchDaemons" \
         "$WORK/root/private/var" "$WORK/root/private/preboot" \
         "$WORK/root/usr/libexec" "$WORK/root/usr/bin" "$WORK/root/etc"

step "stage 1: build a synthetic root (launchd + dyld + cache)"
"$PY" "$ROOT/devices/fuxi/scripts/build-probe.py" "$PROBE/init-data.c" -o "$WORK/root/sbin/launchd"
cp "$WORK/root/sbin/launchd" "$WORK/root/usr/lib/dyld"
"$PY" - "$WORK/root/System/Library/Caches/com.apple.dyld" <<'PY'
import sys
from pathlib import Path
directory = Path(sys.argv[1])
header = b'dyld_v1   arm64e'
(directory / 'dyld_shared_cache_arm64e').write_bytes(header + bytes(4096 - len(header)))
(directory / 'dyld_shared_cache_arm64e.01').write_bytes(bytes(4096))
print('cache written: base 4096 bytes + one shard')
PY

step "stage 2: check the root with check-ios-root.py"
set +e
"$PY" "$ROOT/devices/fuxi/scripts/check-ios-root.py" check --root "$WORK/root" --print-command
STATUS=$?
set -e
if [ "$STATUS" -ne 0 ]; then
	echo "check-ios-root reported a problem (exit $STATUS)" >&2
	exit 1
fi

step "stage 3: populate dry run with the printed drop list"
cd /root/hfs-populate
"$PY" populate-hfs.py --dry-run \
	--launchd "$WORK/root/sbin/launchd" \
	--file "$WORK/root/usr/lib/dyld:/usr/lib/dyld" \
	--file "$WORK/root/System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64e:/System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64e" \
	--file "$WORK/root/System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64e.01:/System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64e.01" \
	2>&1 | tail -8
echo "-- transfer tree --"
find xfer -type f | sort | sed 's/^/   /'
find xfer -type d | wc -l | sed 's/^/   directories: /'

step "stage 4: pack a volume into a padded userdata image"
if [ -f /root/fuxi-root/root-skeleton.hfs ]; then
	bash "$ROOT/devices/fuxi/scripts/build-userdata-root.sh" \
		--hfs /root/fuxi-root/root-skeleton.hfs \
		--out "$WORK/userdata-intake.img" \
		--pad-to 268435456 2>&1 | tail -12
else
	echo "skipped: no /root/fuxi-root/root-skeleton.hfs to pack"
fi

step "result"
echo "synthetic root : $WORK/root"
echo "userdata image : $WORK/userdata-intake.img"
if [ "$KEEP" = 0 ]; then
	echo "(kept for inspection; delete $WORK when done)"
fi
echo "rehearsal: ok"
