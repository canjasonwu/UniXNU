#!/usr/bin/env bash
# Assemble the userdata root image from an HFS volume and print the flashing plan.
#
# The image is the userdata partition content: the kernel reads LUN 0's GPT to find the partition,
# then treats its first 4096-byte block as the SHNGROOT manifest and reads the windows from there.
# Flashing userdata loses Android's data, which is accepted here and reversible with
# "fastboot erase userdata".
#
# Usage:
#   bash devices/fuxi/scripts/build-userdata-root.sh \
#        --hfs /root/fuxi-root/root.hfs \
#        --out /tmp/userdata-root.img \
#        --pad-to 268435456
#
# Options:
#   --hfs PATH      existing HFS+ volume to embed as the OS window (required)
#   --out PATH      output image (required)
#   --pad-to BYTES  zero pad to this many bytes (usually the userdata partition size)
#   --window NAME   window name for the HFS volume (default OS)
#   --verify-only   verify the output image instead of building it
#
# Exit code 0 on success.

set -euo pipefail

HFS=""
OUT=""
PAD_TO=""
WINDOW="OS"
VERIFY_ONLY=0

while [ $# -gt 0 ]; do
	case "$1" in
		--hfs) HFS="$2"; shift 2 ;;
		--out) OUT="$2"; shift 2 ;;
		--pad-to) PAD_TO="$2"; shift 2 ;;
		--window) WINDOW="$2"; shift 2 ;;
		--verify-only) VERIFY_ONLY=1; shift ;;
		-h|--help) sed -n '2,25p' "$0"; exit 0 ;;
		*) echo "unknown option: $1" >&2; exit 2 ;;
	esac
done

if [ -z "$OUT" ]; then
	echo "missing --out" >&2
	exit 2
fi

TOOL="$(cd "$(dirname "$0")" && pwd)/root-manifest.py"
if [ ! -f "$TOOL" ]; then
	echo "missing tool: $TOOL" >&2
	exit 2
fi

if [ "$VERIFY_ONLY" = 0 ]; then
	if [ -z "$HFS" ]; then
		echo "missing --hfs" >&2
		exit 2
	fi
	if [ ! -f "$HFS" ]; then
		echo "missing HFS volume: $HFS" >&2
		exit 2
	fi

	# The volume must still be an HFS+ one; the kernel will mount it as the root filesystem.
	magic=$(od -An -tx1 -j1024 -N2 "$HFS" | tr -d ' \n')
	if [ "$magic" != "482b" ]; then
		echo "warning: $HFS does not start with the HFS+ signature 482b (got $magic)" >&2
	fi
	size=$(stat -c %s "$HFS")
	if [ $((size % 4096)) -ne 0 ]; then
		echo "warning: $HFS size $size is not a multiple of 4096" >&2
	fi

	args=(build --out "$OUT" --window "$WINDOW=$HFS")
	if [ -n "$PAD_TO" ]; then
		args+=(--pad-to "$PAD_TO")
	fi
	python3 "$TOOL" "${args[@]}"
else
	if [ ! -f "$OUT" ]; then
		echo "missing image: $OUT" >&2
		exit 2
	fi
fi

echo
python3 "$TOOL" verify --image "$OUT"
echo
python3 "$TOOL" flash-plan --image "$OUT"
