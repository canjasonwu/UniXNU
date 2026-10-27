#!/usr/bin/env bash
# Read the fuxi kernel console from a QEMU run.
#
# The kernel stops in the debugger trap (debug=0x14e enables the kernel debugger), so it
# prints nothing further to the serial line. Its console text still goes to the msgbuf
# ring buffer, and QEMU's gdb stub can read that buffer out of guest memory. This script
# boots the staged ESP, attaches gdb, dumps msgbuf and prints it in order.
#
# Stage first: devices/fuxi/scripts/qemu-boot.sh (creates out/qemu-fuxi/esp).
# Then: WAIT=40 devices/fuxi/scripts/qemu-console.sh
#
# Observed on 2026-10-27: the QEMU boot reaches 8.887 s and stops right after
# "Security policy loaded: AppleImage4 hooks".
# Attach to the QEMU fuxi boot and dump the kernel's console log buffer.
#
# debug=0x14e enables the kernel debugger, so a panic stops in the debugger trap instead
# of printing. QEMU's gdb stub lets us read msgbuf, which is where the text really goes.
set -uo pipefail
ROOT=/root/UniXNU
OUT=$ROOT/out/qemu-fuxi
KERNEL=$ROOT/out/fuxi/tree/build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837.unstripped.noctf
NM=${NM:-/opt/unixnu-linux/CommandLineTools/usr/bin/nm}
GDB=${GDB:-gdb-multiarch}
WAIT=${WAIT:-35}

pkill -f 'qemu-system-aarch64' 2>/dev/null
sleep 1
rm -f "$OUT/serial.log" "$OUT/gdb.log" "$OUT/msgbuf.bin"

qemu-system-aarch64 -M virt -cpu cortex-a72 -m 2048 -smp 2 \
    -drive if=pflash,format=raw,readonly=on,file=/usr/share/AAVMF/AAVMF_CODE.fd \
    -drive if=pflash,format=raw,file="$OUT/vars.fd" \
    -drive if=none,id=esp,format=raw,readonly=on,file="fat:$OUT/esp" \
    -device virtio-blk-pci,drive=esp -device qemu-xhci -device usb-kbd \
    -nic none -display none -no-reboot -serial "file:$OUT/serial.log" \
    -gdb tcp::1234 >/dev/null 2>&1 &
QEMU_PID=$!
echo "qemu pid $QEMU_PID; waiting ${WAIT}s for the handoff"
sleep "$WAIT"

MSGBUFP=$("$NM" -n "$KERNEL" 2>/dev/null | awk '$3 == "_msgbufp" || $3 == "msgbufp" { print $1; exit }')
echo "msgbufp: ${MSGBUFP:-not found}"

{
    echo 'set pagination off'
    echo 'set confirm off'
    echo 'target remote :1234'
    echo 'printf "PC=%#lx SP=%#lx LR=%#lx\n", $pc, $sp, $x30'
    if [ -n "${MSGBUFP:-}" ]; then
        # msgbufp holds a pointer to struct msgbuf { int magic, size, bufx, bufr; char *bufc; }
        echo "set \$ptr = *(unsigned long*)$((16#$MSGBUFP))"
        echo 'set $bufx = *(int*)($ptr+8)'
        echo 'set $bufc = *(unsigned long*)($ptr+16)'
        echo 'printf "msgbuf at %#lx, bufx=%d, bufc=%#lx\n", $ptr, $bufx, $bufc'
        echo "dump binary memory $OUT/msgbuf.bin \$bufc \$bufc+8188"
    fi
    echo 'detach'
} > "$OUT/gdb.cmds"

timeout 120 "$GDB" -q -batch -x "$OUT/gdb.cmds" "$KERNEL" > "$OUT/gdb.log" 2>&1
echo "=== gdb ==="
grep -aE "PC=|msgbuf at|error|Remote" "$OUT/gdb.log" | head -8

echo "=== kernel console text ==="
if [ -f "$OUT/msgbuf.bin" ]; then
    python3 "$ROOT/devices/fuxi/scripts/show-msgbuf.py" "$OUT/msgbuf.bin"
else
    echo '(no msgbuf dump; see gdb.log)'
    tail -5 "$OUT/gdb.log"
fi

kill $QEMU_PID 2>/dev/null
