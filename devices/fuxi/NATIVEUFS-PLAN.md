# NativeUFS 整合方案（离线评估结论）

> 参考实现：`ljhhkx529/NativeUFS-fuxi`（sheng 上已验证 root 读通路）。
> 本轮已完成：**用我们内核自己的编译记录把它编译通过**，并读清它的运行前提。

## 1. 已确认的可编译性（离线，可复现）

| 项 | 结果 |
|---|---|
| 编译方式 | 复用 `IOService.cpo.json` 的编译记录（与内核同 ABI/部署目标），只替换源文件 |
| 结果 | ✅ `build/NativeUfs.o` 编译成功，归档 `libNativeUfs.a`（487 KB） |
| 需要的额外 include | ① `IOStorageFamily` 头（`IOKit/storage/IOBlockStorageDevice.h` 不在 xnu 树内）② fakeroot SDK 的 `TargetConditionals.h` |
| 导出的内核符号依赖 | `IODelay` `IOFree` `IOLockAlloc` `IOLockFree` `IOLog` `IOMalloc_internal` `IOSleep` `KHEAP_DEFAULT` `OSObject_typed_operator_*` `PE_parse_boot_argn` `SHA256_*` —— 都是我们内核已有的 |

复现命令：`python3 /root/build-nativeufs.py`（脚本内容：读编译记录 → 换源文件 → 加三个 include）。

## 2. 运行前提（从源码读出）

### 2.1 匹配方式（两条 personality）

1. **普通匹配**：provider 的 `compatible` 含 `qcom,sm8550-ufshc` → 接在 UFS 控制器节点上，`IOProbeScore=1000`。
2. **root-device 匹配**：`IOPlatformExpertDevice` → `IOMatchCategory NativeUfsRoot`，**在 platform expert 发布 IOResources 之前、AMFI 启动之前**运行硬件探针。源码注释明确写了这样做的目的：「original AMFI (whose start panics without a PMGR)」——也就是说它同时是我们 PMGR 问题的一种替代解法。

### 2.2 物理窗口（来自 sheng 板级 DT）

| 窗口 | 地址 | 大小 | 用途 |
|---|---|---|---|
| GCC | `0x100000` | — | 时钟/电源域（GDSC）状态检查 |
| UFS host | `0x1d84000` | `0x3000` | UFSHCI 控制器 |

### 2.3 启动参数（均为 opt-in，默认不动 MMIO）

| 参数 | 作用 |
|---|---|
| `native-ufs-probe=1/2` | **只读**探针（2 更详细）；不写任何寄存器 → 符合我们「不写分区」的约束 |
| `native-ufs-root=1` | 走 sheng 的 root 读通路（需要签名卷 UUID） |
| `native-fuxi-ufs-root=1` | **fuxi 开发用传输**：不依赖签名 UUID，直接发布 SHNGROOT 窗口 |
| `native-ufs-diagnostics=1` | 诊断输出 |
| `native-ufs-stats-delay=N` | 延迟统计 |

## 3. 整合到我们内核的两条路径

### 路径 A：作为内核内建组件（与 platform-power 同法）

1. 新增 `devices/fuxi/scripts/build-nativeufs.py`（照抄 `build-fuxi-platform-power.py` 结构）+ `libNativeUfs.a` 进内核归档列表；
2. 新增 ADT 节点（`make-fuxi-adt.py`）：
   ```dts
   ufshc: ufshc@1d84000 { compatible = "qcom,sm8550-ufshc"; reg = <0x0 0x01d84000 0x0 0x3000>; };
   ```
   挂在 arm-io 下（注意子节点 reg 相对父窗口的规则，与 GIC 现在一致）；
3. 内核内建组件没有 kext 的 Info.plist 自动匹配，需要一段 bootstrap：在 `bsd_init` 早期（早于 AMFI）从 ADT 找到该节点 → `new NativeUfs` → `init/attach/start`。这正好复刻它的第二条 personality 的时序意图。

### 路径 B：作为载荷里的 kext

1. 放在 payload 的 `EFI/UniXNU/Payloads/` 下作为 kext bundle，由 DarwinBoot/prelink 加载；
2. 好处：Info.plist 匹配自动生效，改动最小；
3. 风险：kext 加载发生在 AMFI 之后，拿不到「AMFI 之前」的时序优势。

**建议**：先用**路径 A + `native-ufs-probe=2` 只读模式**验证控制器可见性（不写任何东西），
再决定是否采用 fuxi 传输做 root。

## 4. 下一步上机实验（加入实验清单）

**实验 D（只读 UFS 探针）**：
- 需要的改动：路径 A 的 1、2 步 + bootstrap 只做 `probe`（不 start root 通路）
- 引导参数：`native-ufs-probe=2 native-ufs-diagnostics=1`
- 判据：panic 屏/内核日志出现 `NativeUfs: start provider=... probe=2 ...` 且能报告
  GCC GDSC 已上电、AXI/AHB 分支运行；**任何情况下不写寄存器、不写分区**。
- 若探针成功，再评估 `native-fuxi-ufs-root=1` 读取 root 窗口（仍是只读）。

## 5. 与本轮其它结论的关系

- 它证明了「UFS 控制器访问可以在 AMFI 之前完成」这一时序事实，与我们通过
  `OSKext::start` 发布 PMGR nub 的做法是两条独立路径，可互为备份。
- 若最终采用 extent 方案（`hanzi123-bilp` 的 iosroot），则本驱动可作为
  「内核自己读 UFS」的备选，不冲突。
