# fuxi 启动链路台账（HFS 阶段）

> 目的：不再依赖上下文记忆。每一项都写明**代码位置**、**证据来源**、**证据等级**。
> 证据等级：`[原文]` = 我自己跑出的工具输出 / 日志原文；`[索引]` = 用户口述或照片（附路径）；
> `[程序]` = 脚本/解析器算出的结果；`pending` = 尚未验证，写清怎么验证。
> 本文件取代此前 `HFS-REMAINING-SUSPECTS.md` 中被 init 污染过的结论（该文件保留作历史）。

## 0. 北极星与本阶段目标

- 北极星：XNU 在 fuxi 上启动到用户态。
- 阶段目标：让内核在**带真实文件系统**的情况下完成启动（当前：HFS 已编译进内核，但启动会卡死）。

## 1. 启动链路（代码位置来自当前工作树）

| # | 阶段 | 位置 | 状态 |
|---|---|---|---|
| L1 | ABL → `fastboot boot boot.img` | ANDROID! v2，kernel 1 MiB + payload FAT 64 MiB | 已验证（多次 boot 成功） |
| L2 | UEFI/UniXNU → DarwinBoot.efi | `devices/fuxi/board.toml [payload] loader = firmware` | 已验证（能进内核） |
| L3 | DarwinBoot 解析内核 Mach-O | `drivers/DarwinBoot/DarwinBootPkg/Driver/DarwinBoot.c:113 ParseKernel`，窗口上限 `:20 WINDOW_SIZE=SIZE_256MB`，跨度检查 `:179` | 已验证：内核跨度 64.05 MiB < 254 MiB（[程序] 段表解析），不是卡点 |
| K1 | 内核入口探针（绿/蓝/品红三条带） | `devices/fuxi/scripts/patch-xnu-entry-probe.py`（stage1 蓝=入口 MMU off，stage2 品红=MMU 前） | 已验证：三条带 = K1 完成（[原文] 脚本注释 :9-10，[索引] 照片） |
| K2 | `arm_init` → `PE_init_platform` → `boot_progress_init` | `osfmk/arm/arm_init.c:347/372/373` | pending：无独立证据确认到达 |
| K3 | `arm_vm_init`（建内核页表） | `osfmk/arm/arm_init.c:534-536`（boot_progress 标记在两侧） | pending |
| K4 | `startup` → `bsd_init` | `osfmk/kern/startup.c`（boot_progress 补丁插入 4 处） | pending |
| B1 | `bsd_init` 主体 | `bsd/kern/bsd_init.c` | 已验证：能到 B3（见下） |
| B2 | `vfsinit()` | `bsd/kern/bsd_init.c:793` | 已验证（B1 之后立刻） |
| **B3** | **`sweet_hfs_register()`（我们的 HFS 注册）** | `bsd/kern/bsd_init.c:798` | **当前卡点候选** |
| B4 | `bsd_autoconf()` → `IOKitBSDInit()` → `sheng_pmgr_late_probe()` | `bsd/kern/bsd_init.c:932 / 1314`，`patches/xnu/offline-legacy-start.patch` | 已验证：无 HFS 时 AMFI 通过（GOODINIT 出 panic 文本） |
| B5 | `vfs_mountroot()` | `bsd/kern/bsd_init.c:1025` → `bsd/vfs/vfs_subr.c:1210` | pending（有 HFS 时是否到达未知） |
| B6 | mockfs 挂根 | `bsd/miscfs/mockfs/mockfs_vfsops.c:117 mockfs_mountroot`，我们的 Mach-O 检查在 `:87 mockfs_check_macho` | pending |
| B7 | 执行 PID 1 | `bsd/kern/kern_exit.c:1861`（`initproc failed to start` 就在此处打印） | 已验证（无 HFS 时） |

## 2. 已确认的事实（带证据）

| 事实 | 证据 |
|---|---|
| 无 HFS + `getpid+exit` init → 启动成功，打印 `initproc failed to start … (signal 0, exit status 1) timer-irqs=2` | 镜像 `artifacts-GOODINIT/boot.img` = `0dc48ad3e23215fbcbb99f9015ccb83ab135487518fe2f2a51ea0571587967d0`（[原文] sha256sum）；屏幕照片 `C:\Users\Jason Wu\.dsh\attachments\v1\objects\1b\1b60813c3008dead9443a311f93ba7be79847371cabae2ea4ba9a0cecde7ddff`（[索引]） |
| 有 HFS（注册调用已确认编译进 `bsd_init.o`）+ 同一 init → 三条带、无文本 | `artifacts-T1/boot.img` = `5aefe42e1a5045efb99a5c0d1c8d028e7e8aa0949447ce36771886bbd7bfbdad`；离线核对：`nm -u bsd_init.o | grep sweet_hfs_register` 有引用；内核 51,482,624 B（[原文] 构建输出） |
| 三条带 = 内核入口探针（K1），不是 panic 屏 | `patch-xnu-entry-probe.py`:9-10 注释；真 panic 会用品红带区域画文字（[索引] 用户描述） |
| 三色带状态下无任何 panic = 内核未 panic，只是卡住 | 同上 + GOODINIT 对照 |
| HFS 归档 38 对象（去掉了 encodings 与构造器） | `build/sweet-hfs/libSweetHFS.a`；`build-sweet-hfs.py` 输出 `failed: 0, built: 38`（[原文]） |

## 3. 无效实验台账（不要再引用这些结论）

| 镜像/时间 | 为什么无效 |
|---|---|
| ZL `4e3394a1`…ZM/ZN/ZO/ZS/ZT（10-27 晚） | 注册调用当时**未编译进内核**（`bsd_init.o` 无引用）→ "探针不响"不能证明卡点 |
| ZU `07fe3a40` 及之后全部（10-27 22:00 后 ～ 10-28 20:20） | data ramdisk 里是 `open("/")` 探针 init（`devices/fuxi/platform-power/init.c` 旧内容），该调用本身会挂死内核（历史 artifact ZJ 已证）→ 全部"三条带"与 HFS 无关 |
| CONTROL2 `08b014d5` / BASELINE `cb3acbd4` / V1 / V4 | 同上，init 污染；V4 的"540 KB 哑块"结论作废 |
| 10-27 的 patch 状态破坏 | 我曾 `git checkout -- bsd/kern/bsd_init.c` 丢失补丁 hunk，且 `git checkout -f -- .` 恢复的是索引不是 HEAD → 当时树状态不可信。已通过 `git checkout HEAD -- .` + 整套补丁重打恢复（[原文] `git status` 与逐补丁 reverse-check 输出） |

## 4. 当前未解问题（按证据清单）

| 编号 | 问题 | 怎么验证（一次一变量） |
|---|---|---|
| **Q1** | 卡点是否在 `sweet_hfs_register()` 内部（`bsd_init.c:798`）？ | T2 `artifacts-T2/boot.img` = `f3ef7a2ebffc2372ac2d5ea04b59f96dd85b7c14fd33790782139174f3ec759b`：探针在 `vfs_fsadd`+`hfs_sysctl_register` 之后。出文字 → Q1=否；三条带 → Q1=是 |
| Q2 | 若在注册内部，具体是 `hfs_converterinit` / `vfs_fsadd` / `hfs_init_zones` / `hfs_sysctl_register` 哪一步？ | 在注册函数内按 1/4、1/2、3/4 位置继续二分（每步 1 次上机） |
| Q3 | 若注册通过，卡点是否在 `vfs_mountroot()`（`bsd_init.c:1025`）尝试 HFS 挂载？ | 同一 T2 内核里已有 `hfs_mount reached` 探针；把注册探针移除后单独看它 |
| Q4 | data ramdisk 换成真实 HFS+ 卷后，HFS 能否挂上？ | 用 `devices/fuxi/scripts/build-hfs-root.py` 造空 HFS+ 卷当 ramdisk，期望"initproc failed … ENOENT"文本（证明挂上了、只是没文件） |
| Q5 | 挂载后 PID 1 需要 `/sbin/launchd`，如何往 HFS+ 镜像里放文件？ | pending：`hpmount` 不认新卷；候选 = macOS 侧填充 / 写最小 HFS+ 写入器 / 用上游预置镜像 |

## 5. 关键构建事实（避免重复踩坑）

- `local-build.sh` 每次把**仓库** `scripts/build-xnu-4k-baseline.sh` 复制进工作树 → 只改仓库副本。
- 树里 `git checkout -f -- .` 恢复的是**索引**；要恢复上游请用 `git checkout HEAD -- .`（[原文] HEAD=`d8b8029 xnu-10063.141.1`）。
- 归档步骤在**补丁应用之前**运行（`local-build.sh` 的 archive 1..9 → 之后才是 `build-xnu-4k-baseline.sh`），所以补丁必须先应用好再构建。
- `zzz-boot-progress.patch` 与其它补丁在 `kern_exec.c` 上上下文冲突，本次用 `patch -p1 --fuzz=3` 应用（[原文] 日志显示 hunk offset）。
- 内核 `boot-progress` 等功能的 boot 参数见 `devices/fuxi/board.toml` kernel_args。

## 6. 记录与哨兵

- 每轮结论追加到 `kernel-autopilot.md`，并在本台账更新 Q 表状态。
- 本轮收尾运行 `pwsh tools\check-autopilot.ps1`。


## 7. 2026-10-28 晚：屏幕刻度尺测量结果（本轮）

追踪实现：`fuxi_trace_mark(index)` 画 16 个槽位（顶部，panic 屏不清扫该区域），已到达=白、未到达=深灰。
实现要点：必须用 `vinfo.v_baseaddr`（`osfmk/console/video_console.c:119`，内核 VA）；
用 `PE_state.video.v_baseAddr`（物理地址）会 data abort（实测镜像 2e0d883f，far=0xb802a320）。

| 镜像 | sha256 前 8 位 | 白格 | 结论 |
|---|---|---|---|
| TC（无 HFS） | 41e87753 | 3（旧 10 格映射 0/1/7） | 追踪有效 + 基线健康，出 `initproc failed to start` 文本 |
| TA2 | fecea3aa | 7 | 到 `hfs_sysctl_register()` 之后 |
| TA3 | 39085859 | 7 | 同 TA2；根因 = 遗留探针 panic 仍在胶水里 |
| TA5 | bbab84b9 | 12 | 移除全部遗留探针 → 到 `vfs_mountroot()` **入口** |
| TA6 | f2d17da4 | 16 | `vfs_mountroot()` **成功返回**（slot 15 在成功分支内），无 panic 文本 |

### 由此确定

1. **HFS 注册完整通过**：`hfs_converterinit`、`vfs_fsadd`、`hfs_init_zones`、`hfs_sysctl_register` 全部完成并返回。
2. **根文件系统成功挂载**：`vfs_mountroot()` 返回 0。
3. 当前卡点：**mountroot 成功之后、exec PID 1 之前**，且没有 panic（真卡住，不是 panic）。

### 遗留待清

- `devices/fuxi/hfs/sweet_hfs_iokit.c` 里仍有 `printf("SWEET HFS: ... built in and registered")`；
  早期内核上下文里 printf 可能阻塞，尚未验证（TA6 已通过该处，说明至少当前路径无碍）。
- 追踪代码目前是临时插桩（`bsd_init.c` 多处 + HFS 胶水 + `ShengPlatformPower.cpp` 的 `fuxi_trace_mark`），
  正式提交前要么保留为可选（boot arg 控制），要么移除。

### 下一步（Q6）

在 `vfs_mountroot()` 成功（slot 15）与 exec PID 1 之间插 16+ 号点：
`devfs` 挂载、`/dev` 节点创建、`bsd_find_basesystem_dmg`、`exec_init`。
