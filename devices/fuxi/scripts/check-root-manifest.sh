#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-2-Clause-Patent
# Prove that the manifest generator and the production parser agree, offline and repeatably.
#
#   1. the generator's own selftest (22 checks: build, verify, every rejection status)
#   2. a two-window image built from real payload bytes
#   3. tests/RootLayout.cpp compiled against OpenSSL and run twice:
#        - its internal GPT/manifest fault suite
#        - with the generated image, which must parse as status 0 with two windows
#
# Run from anywhere: bash devices/fuxi/scripts/check-root-manifest.sh
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../../.." && pwd)
tests="$repo/devices/fuxi/drivers/nativeufs/tests"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

echo "== 1. generator selftest =="
python3 "$repo/devices/fuxi/tools/root-manifest.py" selftest

echo "== 2. build a two-window image =="
python3 - "$work" <<'PY'
import sys
from pathlib import Path
out = Path(sys.argv[1])
out.joinpath('os.bin').write_bytes(bytes((i * 7 + i // 4096) & 0xFF for i in range(4 * 1024 * 1024)))
out.joinpath('systemos.bin').write_bytes(bytes((i * 11) & 0xFF for i in range(1024 * 1024)))
PY
python3 "$repo/devices/fuxi/tools/root-manifest.py" build --out "$work/userdata.img" \
    --window OS="$work/os.bin" --window SystemOS="$work/systemos.bin"

echo "== 3. production parser =="
clang++ -std=c++17 -Wall -Wextra -Werror -O1 "$tests/RootLayout.cpp" -o "$work/rootlayout" -lcrypto
"$work/rootlayout"
"$work/rootlayout" "$work/userdata.img"
echo "check-root-manifest: PASS"
