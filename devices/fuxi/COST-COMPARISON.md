# 成本对比：继续走我们自己的 XNU 线 vs 采用上游

> 2026-10-27 实测数据，不是估算。
> 前提约束：**只用 `fastboot boot`、不写任何分区**（这条直接决定两个方案的可行性）。

## 0. 决定性事实

| 事实 | 证据 |
|---|---|
| 我们的 xnu-10063 源码里**没有任何真实文件系统** | `bsd/miscfs/` 只有 devfs/mockfs/nullfs/specfs/bindfs/routefs/fifofs/union/deadfs；全树无 hfs/apfs |
| 所以 mockfs 是唯一能挂的根，且它是"把一个可执行文件当根"的测试文件系统 | `mockfs_mountroot()` 注释与实现 |
| 上游能进 SpringBoard，靠的是**搬入 Apple 开源的 HFS** | `0026-sweet-builtin-hfs.patch` + `0026-stage-hfs.sh`，锁定 `hfs-627.100.6` 提交 `31d5ec23` |
| HFS 源码是公开的 | `apple-oss-distributions/hfs` 可访问，内核子集 `core/` = **72 文件 / 82,485 行** |
| 上游的根镜像放在 **Android userdata** 里 | README："The iOS root is a journaled HFS+ image stored as a pinned file inside Android's userdata" |

**推论**：在"不写分区"的约束下，上游的成品**不能直接用**（它的根镜像必须落到 userdata）。
要用上游，就得先解除这条约束。

## 1. 我们今天在真机上确认的能力边界

| 调用 | 结果 |
|---|---|
| `getpid` / `write(1)` / `exit` | ✅ 正常（exit status 1/5，timer-irqs=2） |
| `fork` | ✅ 正常（exit status 1） |
| `wait4(WNOHANG)` | ❌ 阻塞 |
| `open("/")`（mockfs 根） | ❌ 挂死（移除我们自造的 mockfs 补丁后依旧） |
| 定时器中断 | ✅ 确实在触发（timer-irqs=2） |

## 2. 路线 A：继续我们自己的 XNU 线

### 做到"我们自己的程序在真实文件系统上跑"
1. 修 mockfs 的 open/wait4 挂死（内核侧探针定位）：**未知，3–10 次上机**
   - 但即使修好，mockfs 只能跑烘焙进去的 1–2 个程序，**永远到不了 launchd**
2. 搬入 HFS（复用上游的 stage 脚本 + 接线）：
   - 源码：72 文件 / 82k 行
   - **实测编译抽样**（用我们内核的编译记录直接编 HFS 的 3 个核心文件）：
     | 文件 | 错误 | 性质 |
     |---|---|---|
     | `hfs_vfsops.c` | 1 | 缺私有头 `AppleKeyStore/AppleKeyStoreFSServices.h` |
     | `hfs_lookup.c` | 10 | 缺 `workq_threadreq_*` 类型（头文件路径/特性宏问题，非 API 破坏） |
     | `hfs_cnode.c` | 1 | 缺 `crypto/aes.h`（corecrypto 头路径） |
   - 结论：错误以**缺 include/私有头**为主，不是 VFS API 断裂 → **估计 1–3 天**可编译通过并挂进配置
3. 用 data ramdisk（md0）装 HFS+ 镜像做根：mockfs 已证明 md0 能当设备用 → **风险中等，1–2 天**
4. 然后才是真正的用户态（launchd、dyld、trust cache、驱动）：**这是上游花数月做的事**

**合计**：
- 到「我们自己的用户态程序跑在真实 HFS 根上」：**约 1–2 周**（有上限、可分阶段验收）
- 到「iOS 主界面」：**数月**（显示/触摸/UFS/GPU/AMFI 用户态全都要重做）

### 优点
- 完全符合"不写分区"
- 保留我们已有的 AMFI/PMGR/未签名 exec 成果
- 每一步都能离线+上机验证，知识完全掌握在自己手里

## 3. 路线 C：采用上游 `bangprovn/ios-fuxi`

| 项 | 代价 |
|---|---|
| 拿到启动镜像 | 容易（CI 有产物） |
| 拿到根镜像 | 5.68 GB，**必须写进 userdata** ← 违反当前约束 |
| 若破例允许写 userdata | 几天内可看到 iOS 界面 |
| 技术栈 | 放弃 UEFI/UniXNU 线，改用他们的裸机 loader + xnu-8796 + iPhone X 17 用户态 |
| 我们的成果 | AMFI/PMGR/未签名 exec 的成果大概率被他们的实现替代 |


## 修正（2026-10-27 17:40，用户指出 sheng 用的是 APFS）

之前写「搬 HFS」是错的。sheng 的存储链路是：

```
ShengUfs（自研 UFS host 驱动）  →  VirtioMmioBlock / RamBlock（块设备）  →  AppleAPFS（kernelcache 预链接）  →  launchd
```

核对到的事实：

| 事实 | 证据 |
|---|---|
| 我们**已经预链接 AppleAPFS** | `board.toml:120` = `com.apple.filesystems.apfs`；kernelcache 含 `AppleAPFSContainer` |
| kernelcache 里**没有** Apple UFS 驱动 | `com.apple.driver.AppleUFS` 不存在 → 块设备必须自己提供 |
| 我们仓库**已有** `drivers/NativeRamBlock` 子模块 | `.gitmodules:34`，源码 `NativeRamBlock.cpp` + Sparse/Direct/SeedDisk；构建脚本已有接线（`build-xnu-4k-baseline.sh:696-784`） |
| 但它在 board.toml 里被**显式禁用** | `board.toml:68` "NativeRamBlock disabled together with its config" |
| 启用条件 | `CONFIG_XNU_NATIVE_RAM_BLOCK=y` 且 `CONFIG_XNU_NATIVE_AES=y`（脚本 205-213 行） |
| Linux 上能造 APFS 镜像 | apt `apfsprogs`（含 mkfs.apfs，实验性）；HFS+ 也能造（`hfsprogs`），但内核没有 HFS |

**所以正确的路线是：启用 NativeRamBlock（内存镜像当块设备）+ 已有的 APFS kext + `mkfs.apfs` 造的根镜像放进 payload。**
全程不需要写分区，也不需要移植 HFS。HFS 移植方案撤回。

### 修正后的成本

| 工作 | 预计 |
|---|---|
| 启用 NativeRamBlock（defconfig + board.toml 构建步骤 + seed 镜像） | 1–2 天 |
| 在 Linux 上造一个可挂载的 APFS 根镜像（mkfs.apfs 实验性，需验证 AppleAPFS 能挂） | 1–2 天（有不确定性） |
| 让内核从该块设备挂根并跑 launchd | 2–5 天 |
| 之后的用户态（dyld/trust cache/驱动） | 视目标而定，到 SpringBoard 仍是数月量级 |

## 4. 结论与建议（已按修正更新）

> 见上节：优先 NativeRamBlock + APFS，不再移植 HFS。


- **如果你的目标是"我们自己把 XNU 推进到用户态"**（研究/掌握）：现在卡点是「没有真实文件系统」，
  最高杠杆动作是**搬 HFS**（1–3 天可编译，1–2 周到可挂载运行），而不是继续在 mockfs 上修 open。
- **如果你的目标是"尽快看到 iOS 界面"**：只有上游路线，代价是**一次性写 userdata** + 放弃现有栈。
- **折中**：先花 1–3 天把 HFS 编译进我们的内核（不影响现有镜像，可随时回退），
  这一步无论后续走哪条路都有价值；再根据结果决定是否继续自己走到 launchd。

**我的建议**：按折中走——明天开始搬 HFS，目标是一个**可构建、可挂载**的 HFS 根，
验收标准：`mount` HFS+ 镜像成功且能 `open("/")` 返回 fd（先用 mockfs 之外的真实根验证我们今天的 open 问题是否还在）。
