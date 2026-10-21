# fuxi - Xiaomi 13 (SM8550) board description

Draft device description for a UniXNU port to the Xiaomi 13, codename `fuxi`,
model 2211133C, SoC SM8550 (`ro.board.platform=kalama`, DT model
"Qualcomm Technologies, Inc. 8550 fuxi").

Everything here was either measured on the device on 2026-10-21 or copied from
UniXNU-sheng because the value is device independent. Values that could not be
measured are marked TODO in `board.toml` together with the command that
produces them.

## Files

| File | What it is |
| --- | --- |
| `board.toml` | Board description read by the `unixnu` build driver |
| `configs/fuxi_defconfig` | Kernel configuration draft, sheng's SHENG-only switches removed |
| `uefi-config/fuxi.toml` | Corrected `Resources/Configs/fuxi.toml` for the uefi-fuxi checkout |
| `fuxi-live.dtb` | Device tree pulled from `/sys/firmware/fdt`; input for `unixnu bootimg` |
| `uefiplatLA.cfg.txt` | The device's own UEFI memory map, extracted from `xbl_config_a` |
| `read-fdt.py` | Reader that reproduces the device tree facts above |

`fuxi-live.dtb`: 815057 bytes, magic `0xd00dfeed`, version 17, totalsize matches
the file length, sha256 `aa4310cf3359b109150e364dc17eb599728d0760a62024f687a6973d78956fef`.
`bootimg.py` re-checks magic and totalsize, so this file passes its guard.

## Measured facts

| Item | Value | Source |
| --- | --- | --- |
| userdata geometry | first `0x2f7840`, last `0x3afc3fa`, 4096-byte LBA, 224.1 GiB | `/sys/block/sda/sda32/{start,size}` |
| UEFI FD window | `0xA7000000` + `0x400000` | `uefiplatLA.cfg`, row "UEFI FD" |
| UEFI stack | `0xA760D000` + `0x40000` | `uefiplatLA.cfg`, row "UEFI Stack" |
| splash / display reserved | `0xB8000000` + `0x2B00000` | `uefiplatLA.cfg` and `/reserved-memory/splash_region` |
| DRAM | 9 ranges, 12,543,721,472 bytes (11.68 GiB); low window `0x80000000`+`0x80000000` | `/memory` of `fuxi-live.dtb` |
| panel | `qcom,mdss_dsi_m3_38_0c_0a_dsc_cmd`, 1080x2400 @120 Hz, DSI clk 1.1 GHz | `/proc/cmdline`, `dmesg` |
| touch | `fts` (FocalTech) on SPI; sheng uses Novatek NT36532E | `dumpsys input`, `/proc/bus/input/devices` |
| console | `ttyMSM0,115200n8` | `/proc/cmdline` |

Do not substitute the sheng userdata geometry (`0x2f3840` / `0x3d0abfa`): it
starts 16384 LBA = 64 MiB earlier. A raw UFS driver using it would write outside
the userdata partition.

## What works today

`unixnu defconfig`, `unixnu status`, and `unixnu build firmware` are reachable.
The workflow in `.github/workflows/build-fuxi-firmware.yml` builds the firmware
volume and checks that it is exactly 4194304 bytes.

## What does not work yet

| Step | Blocker |
| --- | --- |
| `build kernel` | five archive builders exist only in the sheng repo; no fuxi platform-power, panel or touch code |
| `build payload` | `make-fuxi-adt.py` does not exist; trust cache bundle and platform seed are device inputs |
| `build userdata` | needs macOS `hdiutil` and an IPSW download |

The panel init sequence lives in `fuxi-live.dtb` at
`/soc/qcom,mdss_mdp@ae00000/qcom,mdss_dsi_m3_38_0c_0a_dsc_cmd`, with three
timings under `qcom,mdss-dsi-display-timings`. That node, not the comment in
`fuxi.dsc`, is the source for a future panel driver.

## Not committed

The raw `xbl_config_a` partition image (524288 bytes) is a vendor partition dump
and is deliberately left out; `uefiplatLA.cfg.txt` carries the extracted text.
