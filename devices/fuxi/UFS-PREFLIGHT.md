# UFS bring-up 前置体检（离线判读手册）

> 目的：用**只读**探针先拿到硬件事实（电源/时钟/CAP 能力），再决定是继续调驱动还是先解决上电。
> 不含任何写入，最坏情况只是 panic 停在屏幕上。

## 1. 镜像 CAP（`artifacts-UFS-CAP/boot.img`）

- 内核：默认 `NATIVE_UFS=n`（**不链接驱动**，只有我们自己的只读 inspect）
- 内核参数：`fuxi-ufs-probe=1`
- 触发位置：`sheng_pmgr_late_probe()` → `fuxi_ufs_inspect()`

屏幕会打出（panic 文本）：

```
fuxi-ufs-probe: gdsc=… axi=… ahb=…
unipro=… txs=… rxs=… aux=…
powered=<0|1> busclocks=<0|1> hc_read=<0|1>
cap=… ver=… is=… hcs=… hce=…
```

## 2. 逐项判读

### 2.1 电源（GDSCR bit31）

| 值 | 含义 | 后续 |
|---|---|---|
| `powered=1` | UFS 电源域已开（大概率是 ABL 遗留） | 可以继续驱动路线 |
| `powered=0` | 电源域关闭 | 驱动 `hostBringUp` 必然失败；需要 RPMh 上电，或者确认 ABL 是否在启动后关电 |

### 2.2 总线时钟（CBCR）

`busclocks` 的判定（与驱动一致）：

```
busclocks = !(axi & 0x80000000) && (ahb & 1) && (axi & 1)
```

- `axi` 的 bit31 是 `CLK_OFF`（1=关），bit0 是使能
- `ahb` 的 bit31 在空闲时可能仍为 1（硬件门控），驱动只用 bit0

| 值 | 含义 |
|---|---|
| `busclocks=1` | 可以直接做寄存器访问 |
| `busclocks=0` | 用镜像 W/X 的时钟使能（只写 branch enable bit0）后再访问 |

### 2.3 CAP：决定 DMA 缓冲能不能放在 4 GiB 以上

驱动 `hostBringUp` 里的硬约束：

```c
if (!gHost.virt || !gHost.phys || (gHost.phys & 4095) || contiguous < kDmaBytes ||
    (!(read32 (gHost.hc, 0) & 0x01000000) && gHost.phys + kDmaBytes > 0x100000000ULL)) {
    *why = "DMA address/contiguity";
    return false;
}
```

也就是：**CAP 的 bit24**（`0x01000000`）为 0 时，DMA 缓冲必须整体位于 **4 GiB 以下**。

| `cap & 0x01000000` | 含义 | 风险 |
|---|---|---|
| 非 0 | 支持 64 位 DMA 地址 | 缓冲落在哪都行，风险低 |
| 0 | 只支持 32 位 DMA | 若 `IOBufferMemoryDescriptor` 分配到 4 GiB 以上 → `DMA address/contiguity` 失败 |

> 我们的 `owned_memory_size` 只有 448 MiB，但它在 DRAM 中的**位置由 DarwinBoot 决定**，完全可能高于 4 GiB。
> 这就是为什么先看 CAP bit24 再动驱动：如果它是 0，需要"低地址 DMA 分配"方案（见 §4）。

### 2.4 HCS / HCE

- `hce`（Host Controller Enable）为 0 表示控制器处于 disable 状态；驱动的 `hostBringUp` 会自己写使能
- 若 `hcs` 显示控制器卡在 fatal 状态，先断电重启再测

## 3. 建议的测试顺序（省轮次）

1. **CAP**（`-Image P`）：只读拿电源/时钟/CAP —— 不需要驱动
2. 若 `powered=1` 且 `busclocks=1` 且 `cap bit24≠0` → 直接测 **U**（驱动只读窗口读取）
3. 若 `powered=1` 但 `busclocks=0` → 测 **W**（时钟使能）或直接用 **X**（时钟+驱动）
4. 若 `powered=0` → 先解决上电，不要在驱动侧浪费时间

## 4. 如果 CAP bit24 = 0（只支持 32 位 DMA）的应对

**已实现兜底**（`devices/fuxi/scripts/nativeufs-dma-fallback.py`，驱动侧、可重复执行）：

`hostBringUp` 现在会：

1. 读 CAP bit24 判断是否支持 64 位 DMA
2. **最多尝试 4 次**连续内存分配，保留第一个"物理地址 + kDmaBytes ≤ 4 GiB"的缓冲
3. 每次拒绝都打日志：`NativeUfs: DMA attempt N rejected: phys=… contiguous=… dma64=…`
4. 全部失败时 `why` 明确区分两种情况：
   `DMA above 4 GiB with a 32-bit-only controller` 或 `dma buffer`

对应的带兜底镜像：`artifacts-UFSREAD2/boot.img`（字母 **L**，参数 clock+read=1，与 U 相同但含兜底）。

若仍失败，按优先级：

1. **进一步降低 DMA 需求**：只读单个 4 KiB 块（驱动已有 `hostRead4k` 单块路径），避免整窗 DMA
2. **确认 ABL 是否留下低 4 GiB 的连续区**：从 ADT 的内存映射里读出窗口基址
3. **换用低地址固定区**：用 `IODeviceMemory` 映射一段已确认在 4 GiB 以下的物理区（需要知道确切地址）

这些都**不需要真机迭代**就能写，只需 CAP 的 bit24 值与 DMA 日志来定方向。

## 5. 相关镜像一览

| 字母 | 镜像 | 参数 | 用途 |
|---|---|---|---|
| P | `artifacts-UFS-CAP` | `fuxi-ufs-probe=1` | 只读硬件体检（本手册） |
| W | `artifacts-UFS-CLOCK` | `fuxi-ufs-clock=1` | 时钟分支使能报告 |
| X | `artifacts-UFS-X` | clock+start+probe=10 | 时钟+驱动启动+只读检查 |
| U | `artifacts-UFSREAD` | clock+read=1 | 读 OS 窗口到 RAM 并注册 md（报告） |
| R | `artifacts-UFSREAD-MOUNT` | clock+read=2 | 读窗口并**切换根**到该 md |
| Y/Z | `artifacts-MDEV*` | mdev-test=2/1 | 用现有 RAMDisk 验证"注册 md + 切根"机制 |
