# 2026-10-29 凌晨复盘：漏掉的探针与上游进展

> 目的：下一轮开工时不用重新回忆。每条都标明依据。

## 1. 上游仓库新进展（2026-10-28，GitHub API 查询结果）

| 仓库 | 最新提交（时间/主题） | 说明 |
|---|---|---|
| `bangprovn/ios-fuxi` | 10-28 14:19 `gpu: per-copy GPU timestamps …`；13:56 `FUXI_MTL_RT_SHADOW`；10:15 启用 `shaderFloat16/16-bit storage/shaderInt16` | 他们已经在做 **Metal 后端优化**（逐 draw GPU 时间戳、管线统计），说明 SpringBoard 早已跑起来，现在在调性能/渲染正确性 |
| `hanzi123-bilp/uefi-fuxi` | 10-28 12:16 / 09:43 / 09:23 / 08:55 `loader: payload with the pinned root extents`；07:39 `loader: drop the GPU probe call, it prevents the root from mounting` | 他们在做 **loader 的 root extent 表**（把根镜像的物理 extent 塞进 payload），以及发现"GPU probe 会破坏 root 挂载" |

对我们有用的两条：
1. **"pinned root extents"**：如果将来要把根放在 `userdata`（我们已允许写 userdata），可以照他们的做法：安装时把根镜像写成连续文件，装载器把 extent 表放进 payload，内核按 extent 读——**不需要 UFS 驱动**。这可能是比 NativeUFS 更省的一条路。
2. **"GPU probe 会破坏 root 挂载"**：我们自己也开着若干 probe（`XNU_ADRENO_*` 之类），如果后续 root 挂载异常，可以先怀疑这些 probe。

## 2. 复盘：本轮探针覆盖缺口

### 已确认（有白格证据）

- 注册 → `bsd_autoconf` → mountroot → `VFS_ROOT` → `set_rootvnode` → devfs → `load_init_program` → **exec 成功** → init 退出（`kern_exit` 的 initproc 分支到达）
- 从 `kern_exit` 的 mark 29/30/31/32 能走到 32：`proc_crash_coredump`、`sync()` 均完成，**停在 `panic_with_options()` 调用之前**

### 漏掉/需要补的探针（按价值排序）

| # | 缺口 | 为什么要补 | 怎么补 |
|---|---|---|---|
| P1 | **panic 漏斗入口没有探针**：不知道 `panic_with_options` 是否真被进入，还是它内部就挂了 | 目前"panic 没渲染"可能有两种：panic 没进去 / 进去了但渲染卡住。这决定是修诊断通道还是修 panic 路径 | 在 `osfmk/kern/debug.c` 的 `panic_with_options`（:1048）与 `panic_trap_to_debugger` 各加一格 |
| P2 | **不知道是哪个文件系统挂载成功** | mountroot 成功可能是 mockfs 而非 HFS。若要 HFS 成为根，必须知道 HFS 是否被尝试、失败在哪 | 在 `hfs_mount`（已有 13 号）与 `mockfs_mountroot` 各加**互不重叠**的独立格子，而不是连续编号 |
| P3 | **init 内部无可见性** | init 是用户态 Mach-O，无法直接画格；但可以让它**自己**往 framebuffer 写一个像素作为"我执行到了"的标记 | 在 probe init 里 mmap framebuffer 或直接写 `0xB8000000`（需先验证该物理地址在用户态可映射） |
| P4 | 没有确认 `sync()` 遍历到了哪些挂载点 | 若后续在 `sync()` 卡住，需要知道遍历顺序 | 在 `vfs_sync` 调用处加格 |
| P5 | 刻度尺位置：三行都在屏幕下半部，**panic 屏一绘制就会被清空** | 本轮已出现"看到 33 白但没字"，若 panic 真的绘制，我们会失去刻度信息 | 把刻度移到顶部（已验证 panic 屏只清 h/8 以下）或改为"左上角单行 + 大格子" |

### 方法论教训（写下来避免重犯）

1. **探针必须先验证通道有效**：第一版追踪写 `PE_state.video.v_baseAddr`（物理地址）直接 data abort，浪费了两次上机。以后先用"已知能启动的构建"验证探针，再上真实验。
2. **残留探针会造成假卡点**：10-27 的 `panic("probe stage 2")` 留在代码里，TA2–TA4 的"7 白"其实是探针自己 panic。
3. **探针要放在"必然执行"的代码路径上**：TA9/TA11/TA12 的 24/25 号点分别落在死代码（编译关闭的钩子）和 `if (launchdsuffix)` 分支里，三次上机白跑。
   - 规则：**改完先在编译产物里 `nm`/反汇编确认调用点进入代码段**，并人工核对该语句在同一条件下必然执行。
4. **改 HFS 源码后必须手动重编归档**：`local-build.sh` 不负责 `build/sweet-hfs`，否则内核里还是旧代码（TA6 第一次就因此白跑）。
5. **构建脚本只改仓库副本**：`local-build.sh` 每次把仓库脚本复制进工作树。

## 3. 下一轮计划（按顺序）

1. **Q8 主目标**：造带真实 `/sbin/launchd` 的 HFS+ 根镜像 → 让 HFS 成为根 → PID 1 从 HFS 启动。
   - 障碍：Linux 侧没有可靠的 HFS+ 填充工具（`hpmount` 不认新卷）。
   - 备选方案：
     a. 在 macOS 上完成填充（如果用户有 Mac/VM）；
     b. 写一个最小 HFS+ 写入器（只需创建 root dir + 一个文件 + catalog/extents）；
     c. 用 `7z`/`libfsapfs` 从上游预置镜像里抽取文件树，再用同一写入器放进去；
     d. 参考 hanzi123 的 "pinned root extents" 路线，把根放在 userdata（我们已允许）。
2. **P1/P2**：补 panic 漏斗和"哪个 FS 挂载成功"的探针（各一次上机）。
3. 视情况决定是否继续修 panic 渲染（诊断价值 vs 主目标）。

## 4. 当前工作树状态（交接用）

- 树内插桩：`bsd_init.c`（0,1,7,8,9,10,11,15,16,17,18,19,20,21,22）、`kern_exec.c`（23–28）、
  `kern_exit.c`（29–32）、`bsd/sweet_hfs/core/hfs_vfsops.c`（13）、`hfs/sweet_hfs_iokit.c`（2–6）、
  `ShengPlatformPower.cpp`（`fuxi_trace_mark` 36 格实现）。
- **这些插桩都是临时的**，正式提交前应改成 boot arg 开关或移除。
- HFS 注册调用在 `bsd_init.c` 的 `vfsinit()` 之后（无条件，不依赖 `SWEET_HFS` 宏）。
- 最新镜像：TA14 `8cddaf56`（36 格刻度）；基线对照 TC `41e87753`（无 HFS，出文本）。
