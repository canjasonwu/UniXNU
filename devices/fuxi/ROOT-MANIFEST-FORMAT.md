# userdata 根部镜像的 manifest 格式

本文件描述 `NativeUfs` 在 userdata 分区第一个 4096 字节块里读取的根镜像清单（manifest）。
**唯一事实来源是代码**：`devices/fuxi/drivers/nativeufs/RootLayout.h` 的 `findRootLayout()`。
生成器是 `devices/fuxi/tools/root-manifest.py`；一致性检查是
`bash devices/fuxi/scripts/check-root-manifest.sh`（不需要设备）。

## 1. 内核怎么用它

1. 读 LUN 0 的 GPT（4096 字节扇区），按名字找到 `userdata` 分区（`RootLayout.h` "GPT entries" 段）。
2. 读该分区**第一个块**作为 manifest（"root manifest" 段）。
3. manifest 通过校验（`manifestStatus == 0`）后，`layout->window[]` 就是若干根窗口
   （sheng 上是 `OS` 与 `Cryptex1,SystemOS`），每个窗口带 offset/size/sha256。
4. 任何一项不符时 `manifestStatus` 是 1..7 的具体编号，屏幕上的 `NATIVE_UFS root check`
   会把它打出来（B12 镜像实测是 `manifest status=1`，即"没有 magic"）。

## 2. 字节布局（小端）

manifest 块（4096 字节）：

| 偏移 | 类型 | 含义 |
|---|---|---|
| 0 | char[8] | magic `"SHNGROOT"`（`u32 0x474e4853, 0x544f4f52`） |
| 8 | u32 | version，必须为 1 |
| 12 | u32 | 窗口数 n，1..4 |
| 16 | u64 | `imageBytes`：整个根镜像大小，4096 对齐、≥4096、≤分区大小 |
| 24 | entry[4] | n 个 128 字节窗口条目 |
| 4064 | u8[32] | 对 `[0, 4064)` 的 SHA-256 |

每个 128 字节条目：

| 偏移 | 类型 | 约束 |
|---|---|---|
| 0 | char[32] | 名字：可打印 ASCII（0x20..0x7e）、32 字节内 NUL 结尾、非空、不重复 |
| 32 | u64 | `offset`：相对 userdata 分区起点，4096 对齐，≥4096 |
| 40 | u64 | `size`：非零、512 对齐 |
| 48 | u32 | 必须 512 |
| 52 | u32 | 必须 1 |
| 56 | u8[32] | 该窗口**恰好 `size` 字节**内容的 SHA-256 |

额外规则（与驱动逐条对应）：

- 窗口数据不能重叠，**按 4 KiB 向上取整**后也不能重叠（内核按 4K 物理读）。
- `align4k(size) <= imageBytes - offset`（最后一个窗口也要留够补齐）。
- 窗口顺序任意，但驱动按条目顺序解析，所以生成器按 offset 升序写。

## 3. 状态码（`manifestStatus`）

| 值 | 含义 |
|---|---|
| 0 | ok |
| 1 | 没有 magic（Android 的 userdata 就是这一种） |
| 2 | version 或窗口数不合法 |
| 3 | 4064 处的 trailer SHA-256 不符 |
| 4 | 窗口边界 / imageBytes 不合法 |
| 5 | 名字不合法或重名 |
| 6 | 窗口（含 4 KiB 补齐）重叠 |
| 7 | 属性字段不是 512/1 |

## 4. 工具

```bash
# 自测（22 项断言：构建、校验、每个拒绝状态）
python3 devices/fuxi/tools/root-manifest.py selftest

# 生成一个双窗口镜像
python3 devices/fuxi/tools/root-manifest.py build --out /tmp/userdata.img \
    --window OS=/path/to/os.bin --window SystemOS=/path/to/systemos.bin

# 校验已有镜像
python3 devices/fuxi/tools/root-manifest.py verify --image /tmp/userdata.img

# 生成器 ↔ 生产解析器一致性（编译 tests/RootLayout.cpp，用它解析生成的镜像）
bash devices/fuxi/scripts/check-root-manifest.sh
```

`check-root-manifest.sh` 的最后一步会打印
`PASS: existing root image's 4096-byte manifest, OS=4194304 SystemOS=1048576`，
说明生成器与内核里的解析器对同一份字节达成一致。

## 5. 真机验证路线

1. 用真实根内容生成 userdata 镜像（OS + SystemOS 窗口；内容来自 IPSW 提取，见
   `docs/IPSW-EXTRACT-REPORT.md`）。
2. `fastboot flash userdata <image>`（回滚：`fastboot erase userdata`）。
3. 刷 `artifacts-UFS-ROOTCHECK2/boot.img`（B12，带 `fuxi-ufs-start=1 native-ufs-probe=10`）。
4. 期望屏幕：`manifest status=0 windows=2`，并打出各窗口 offset/size。
5. 再刷 `probe=11` 的镜像校验窗口 GPT 与 sha256，最后才切 `native-ufs-root=1`。
