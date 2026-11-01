# 从 sheng 抄作业：fuxi 跑起真实 iOS 用户态的逐字段迁移清单

> 写于 2026-11-01。依据：`UniXNU/UniXNU-sheng`（commit 已 clone 到 /root/refs/upstream/UniXNU-sheng）、
> `UniXNU/UniXNU-sm8550-common`、本仓 `devices/fuxi/board.toml`。
> 结论先说：**我们的 board.toml 本来就是 sheng 形态的**，缺的不是架构，而是 4 个策略字段 + 1 个驱动策略
> + 1 个固件能力。sheng 已在同一颗 SM8550 上把原版 iPadOS 用户态跑到主屏（README: "The original iPadOS
> userland runs from the IPSW; the home screen and Settings are reached"、"UFS ✅ Native UFS root"）。

## 0. 一句话差异

sheng 的启动策略是 **prelink 保留 Apple kexts（含 APFS/IOSStorageFamily）+ 原生 UFS 根（userdata 里放
IPSW 的 OS/SystemOS 卷）+ 5.1 GiB 内存窗口**；我们现在是 **mockfs/HFS 数据盘当根 + 448 MiB 窗口**。
所以"sheng 能跑真实 launchd、我们不能"的根源不是内核缺能力，而是**根文件系统的来源与内存窗口**。

## 1. 已经就位的部分（不用动）

| 项 | 证据 |
|---|---|
| board.toml 的 schema（board/ipsw/userdata/kernel/prelink/firmware/payload/adt） | `devices/fuxi/board.toml` 的 section 列表与 sheng 一致 |
| IPSW 目标（iPad7,1 17.7.11 21H461）与 sha256 | fuxi board L19-43，与 sheng 相同 URL/product/version |
| prelink 保留 Apple kexts（apfs、IOSStorageFamily、AMFI、sandbox、AppleImage4…31 个 root） | fuxi board `[kernel.prelink].roots` |
| `-apfs_shared_datavolume` kernel arg | fuxi board kernel_args |
| userdata 的 LBA 实测值 | fuxi board L45-47：first 0x2f7840、last 0x3afc3fa、4096 字节 LBA |
| userdata windows（OS / Cryptex1,SystemOS） | fuxi board `[userdata].windows` |
| UFS 时钟/GDSC 操作与读窗口钩子 | `devices/fuxi/platform-power/ShengPlatformPower.cpp`（`fuxi_ufs_inspect`、`fuxi_ufs_clock_enable`、`fuxi_ufs_start_hook`、`fuxi_ufs_read_window_hook`） |
| NativeUfs 归档构建与 start-hook 注册 | `devices/fuxi/NativeUfsStartHook.cpp`、`IOStorageStubs.cpp`、`scripts/build-nativeufs.py`；上游 `scripts/build-arm-driver.py` 已支持 `NativeUfs` 组件 |
| UFS 判读依据（host/GCC/SMMU 地址） | 见 §2 的 sheng 常量，与 sm8550-common 一致 |

## 2. 必须改的字段（逐个）

| # | 字段 | sheng 值 | fuxi 当前 | 动作 |
|---|---|---|---|---|
| 1 | `[payload.adt].owned_memory_size` | `0x130000000`（5.1 GiB） | `0x1C000000`（448 MiB） | 提到 sheng 量级；需要固件内存扩展（§3.1） |
| 2 | `[payload.adt].packed_data_ramdisk` | `true` | `false` | 改为 true（与 userdata 根配套） |
| 3 | `[payload].kernel_args` 中的根策略 | `native-source-storage=1`、`native-ufs-root=1`、`offline-apfs-load=1`、`-apfs_shared_datavolume`、`sheng-pmgr=2` | 只有 `-apfs_shared_datavolume` + `rd=md0`（mockfs） | 去掉 `rd=md0`，加 `native-ufs-root=1` + `offline-apfs-load=1`；PMGR 开关见 §3.2 |
| 4 | `[kernel].archives` | 含 `component:ShengUfs`、`VirtioMmioBlock`、`RamBlock`、`build-sheng-platform-power.py` 等 19 项 | 9 项，含 `build-mockfs-fixture.py`、`build-fuxi-platform-power.py`，**无 UFS 归档** | 增加 `build-arm-driver.py NativeUfs`（或 fuxi 自己的 UFS 驱动归档）；移除 mockfs-fixture 仅在切根之后 |
| 5 | `configs/fuxi_defconfig` | `CONFIG_XNU_NATIVE_UFS=y`、`CONFIG_XNU_SHENG_PMGR=y` | `CONFIG_XNU_NATIVE_UFS=n`（注释：缺 IOBlockStorageDevice 头）、`SHENG_PMGR=y` | 先解决头文件问题（本仓已有 `IOStorageStubs.cpp`），再置 y |
| 6 | `[payload].size_mib` | 176–208 | 160 | 切换后按内核+ADT 实际大小调（sheng 208 对应 kernels+kexts 更大） |
| 7 | `[payload].install` / `persistent_data` | 有 install 菜单、`persistent_data = true` | 无 | 迁移时补（数据卷可持久化与重置语义） |
| 8 | `[os_overrides]` | 按 17.7.11 / 26.5.2 两套 override | 无 | 先只做 17.7.11 一套 |

> 注：`root_policies` 在 sheng 的 **17.7.11** override 里是 `"remove" = ["payload.root_policies"]`，
> 即 iOS 17 路线**不**用 VerifiedRoot 的 policy 包，只用 `root_match_volume_uuid`。我们照抄这条。

## 3. 依赖顺序（不能跳步）

### 3.1 内存窗口
sheng：`owned_memory_size = 0x130000000`，由固件模块（`ShengBootMemoryDxe`）扩展内存后交给 DarwinBoot。
fuxi：我们已有 `FuxiBootMemoryDxe`（prepare-firmware 会安装），但当前只做到 ~490 MiB 连续块。
**下一步**：对照 sheng 的固件内存策略，把 FuxiBootMemoryDxe 的目标从"释放若干厂商预留"改成
"尽可能扩大连续窗口"，并用 `NO WINDOW`/`BIG RUN` 诊断在真机测出能到的上限（先 1 GiB 还是能到 4 GiB）。
这是唯一可能卡死的硬件相关未知项。

### 3.2 PMGR（AMFI 的前置）
fuxi board L237 的注释已经写明：`native-ufs-root=1` 需要 PMGR 集成，**否则 AMFI 以 "No PMGR?" panic**。
sheng 用 `CONFIG_XNU_SHENG_PMGR=y` + `sheng-pmgr=2`；我们的 defconfig 已是 `SHENG_PMGR=y` 且平台层
"只发布服务"（`devices/fuxi/platform-power`）。**下一步**：验证 fuxi 的 PMGR 服务能否让 AMFI 通过——
在 HFS 根镜像上只加 `-v debug=0x14e` 与 PMGR 相关 arg 做一次对照实验即可，不需要 userdata。

### 3.3 NativeUfs 根
把 `NativeUfs` 归档编进内核（第三方驱动已能编，见 `scripts/build-nativeufs.py`），
再让 `mountroot` 走 `native-ufs-root=1`：这要求 `IOStorageFamily/IOSStorageFamily` 的块设备注册路径可用
（prelink roots 已含 `com.apple.iokit.IOStorageFamily`，sheng 已证明这条路成立）。

### 3.4 userdata 镜像
sheng 的 `build userdata` 把 IPSW 的 OS + SystemOS 卷写进 userdata 分区（需要 macOS `hdiutil`）。
**下一步**：用上游 `macos.yml` runner workflow 生成 fuxi 的 userdata 镜像（本仓已有 CI 经验），
LBA 用 fuxi 实测值（0x2f7840–0x3afc3fa）。

### 3.5 切换根策略
以上四步都过之后，才把 kernel_args 从 `rd=md0` 换成 `native-ufs-root=1`；mockfs-fixture 归档同步移除。

## 4. 需要真机测量的量（按优先级）

| 量 | 为什么 | 怎么测 |
|---|---|---|
| 固件连续内存上限 | 决定 owned_memory_size 能提多少 | `FuxiBootMemoryDxe` + DarwinBoot `NO WINDOW … BIG RUN …` 诊断（已有） |
| UFS GCC/GDSC 寄存器实际值 | sheng 的 `GCC_BASE=0x00100000`、`UFS_HC_BASE=0x01d84000` 大概率相同，但时钟分支 fuxi 可能不同 | 已有 `fuxi-ufs-probe` / `fuxi-ufs-clock` 镜像（W/X 系列） |
| UFS SMMU stream 与 context bank | sheng 用 Apps SMMU `0x15000000`、stream `0x60` | 已有 `fuxi_ufs_read_window` 探针；失败时按 `SmmuTakeover.h` 的 top-bank-first 策略重试 |
| PMGR 节点与 AMFI 关系 | 决定 native-ufs-root 能否启用 | 在 HFS 根镜像上加 PMGR arg 对照 |
| userdata 分区当前内容 | 切换前必须确认可擦写、可回滚 | `fastboot erase userdata` 即可回滚（本项目约束允许） |

## 5. 立即行动（不需要设备）

1. `git clone https://github.com/UniXNU/UniXNU-sheng /root/refs/upstream/UniXNU-sheng`（已做）
   并 `git submodule update --init drivers/ShengUfs`（已做）；对照 `drivers/ShengUfs/*.h` 与我们的
   `platform-power` UFS 代码，列出缺失 API。
2. 把 `[payload.install]`、`persistent_data`、`[os_overrides]` 三段从 sheng 裁成 fuxi 的 17.7.11 版本。
3. 在 `board.toml` 里新增 `[kernel].archives` 的 `build-arm-driver.py NativeUfs` 项（先不启用根切换）。
4. 用 `unixnu status --device devices/fuxi --json` 确认新增字段被解析（棋盘式校验，避免 TOML 静默忽略）。
5. 在 CI 上加一个 `board.toml` 结构校验 job（本仓已有 `tools/ci-preflight.py` 可扩展）。

## 6. 风险与回退

- **回退点**：当前 mockfs/HFS 路线（A 路线探针链）完整保留；切换根策略只改 board.toml + 一个 defconfig 位，
  随时可 `git checkout -- devices/fuxi/board.toml` 回到现状。
- **最大风险**：内存窗口到不了 4–5 GiB（固件无法释放足够连续内存）→ 那时退回"最小根 + HFS"（A 路线）。
- **次大风险**：fuxi UFS 时钟/供电域与 sheng 不同 → 先只读窗口验证，不要直接上根挂载。
- **数据**：切 userdata 会覆盖 Android 用户数据；`fastboot erase userdata` 可恢复出厂，符合本项目的可回滚约束。
