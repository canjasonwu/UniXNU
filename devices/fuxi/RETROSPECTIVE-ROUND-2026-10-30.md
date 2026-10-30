# 复盘（截至 2026-10-30）：目标 / 链路 / 卡点 / 调试方法

> 依据：`kernel-autopilot.md`、`BOOT-CHAIN-LEDGER.md` 与真机照片。凡是推断都标注，未验证写 pending。

## A. 目标复盘

| 层级 | 内容 | 现状 |
|---|---|---|
| 北极星 | XNU 在 fuxi 上跑到用户态 | 进行中 |
| 阶段目标（当前） | 让 PID 1 真正执行 | **未达成**：进程被杀（SIGBUS=10），一条指令都没确认执行 |
| 每轮可验证问题 | 例：`load_machfile` 是否成功、根是否 HFS 挂载、exec 后是否取指成功 | 前两个已答，第三个未答 |

### 复盘发现的目标层问题

1. **"阶段目标"曾被描述成"启动链打通到 PID 1"**，但真正的判据应是"用户态第一条指令执行过"。多次把"进程被创建/被杀死"当成"进程运行过"（已撤销，见 `kernel-autopilot.md` 10-30 01:10 条目）。
2. **缺少"验收定义"**：比如"run launchd"应定义成：`/sbin/launchd` 从 HFS 执行后，能通过 syscall 让内核可见的状态（我们现在用 mark/数字代替）。
3. 目标与手段混淆：为看状态先做了 5 代显示，属手段而非目标，值得约束（见 D 节改进）。

## B. 链路复盘（逐段证据与缺口）

| # | 环节 | 证据 | 状态 |
|---|---|---|---|
| L1 | `fastboot boot` 162 MB 镜像 | `Sending 165644 KB OKAY` | ✅ |
| L2 | UEFI/UniXNU → DarwinBoot → 内核 | 入口色带出现 | ✅ |
| K1 | 内核入口探针（绿/蓝/品红） | `patch-xnu-entry-probe.py:9-10` | ✅ |
| K2 | `arm_init` → `PE_init_platform` → 虚拟内存建立 | 无独立证据 | **pending**（仅由 K1 后的继续运行推断） |
| B1 | `bsd_init` 进入 | mark 0 | ✅ |
| B2 | `vfsinit` | mark 1 | ✅ |
| B3 | `sweet_hfs_register`（fsadd/zones/sysctl/返回） | mark 2–7 | ✅ |
| B4 | `bsd_autoconf` → `IOKitBSDInit` → PMGR 晚探针 | mark 8–10 | ✅ |
| B5 | `vfs_mountroot` 成功 | mark 15（在成功分支内） | ✅ |
| B6 | **谁挂载了根** | 无直接标记；靠"ramdisk 是 HFS+ 卷 + mockfs 拒绝非 Mach-O"推断 | **pending（缺口 P2）** |
| B7 | `VFS_ROOT` / `set_rootvnode` | mark 17/18 | ✅ |
| B8 | `IOSecureBSDRoot` / imageboot / devfs / rootauth | mark 16/19/20/21 | ✅ |
| B9 | `load_init_program` 进入 | mark 23 | ✅ |
| B10 | `__mac_execve` → `load_machfile` 成功 | mark 45/44；失败分支 47 从未点亮 | ✅ |
| B11 | **用户态取指** | 唯一现象是 initproc 被 SIGBUS 杀死（WTERMSIG=10） | **未确认**（缺口 P6） |
| B12 | init 退出 → panic → 文字 | 卡在 panic 渲染器：EL1 取指权限故障（EC=0x21, FSC=0x0F） | ❌ 破损 |

## C. 卡点复盘（含尚未验证的假设）

| ID | 卡点 | 证据 | 状态 |
|---|---|---|---|
| C1 | 用户态第一条指令失败（SIGBUS 10） | 七段 `31/10/0`；`load_machfile` 成功 | **原因未知**（我的 EL0 过滤器写成了 EL1，数据没抓到） |
| C2 | panic 渲染器跳坏页（EC=0x21, FSC=0x0F） | 无 HFS 注册的对照镜像同样出现 | 假设：内联 panic blob 落在不可执行页/跨段；**待离线验证** |
| C3 | NativeUfs `pass2` 静默失败 | 打开 `CONFIG_XNU_NATIVE_UFS=y` 时无输出退出 | 未定位（缺 stderr） |
| C4 | HFS+ 造卷 | QEMU + Ubuntu guest 成功，guest 内 sha256 与源一致 | ✅ 已解 |
| C5 | boot.img 尺寸上限 | 162 MB 实测可加载 | ✅ 已解 |

## D. 复盘：panic 之前用过的调试方法与缺点

| 方法 | 优点 | 缺点 | 处置 |
|---|---|---|---|
| 多色带入口探针 | 无需任何系统就绪；能证明内核入口 | 只能证明"到过入口" | 保留 |
| `panic()` 探针 | 有文字、信息量大 | 一次性、破坏状态、依赖渲染器（现已坏） | 少用 |
| 累计刻度（方块） | 直观 | 会数错；被 panic 清屏；被后续 mark 覆盖 | **废弃** |
| 二进制位显示（10 格） | 位置固定 | 只有最后一次；状态被覆盖 | 废弃 |
| 覆盖位图（40–53） | 能回答"哪些路径跑过" | 无顺序、无次数；只覆盖 14 个点 | 保留为"路径覆盖"用途 |
| 七段数字（现状） | 稳定、可读、无计数歧义 | 只有 3 个数字；被 abort 报告覆盖；无字符串 | 保留，扩展 |
| 用户态退出码编码 | 能把用户态结果带回内核侧 | 依赖 panic；只 1 位 | 待 panic 修好后恢复 |
| 离线源码对照 | 零成本，可排除假设（如 header 权限） | 只能排除，不能证明运行期 | **应作为默认第一步** |
| 对照组镜像（无 HFS / 不注册 / mockfs vs HFS） | 定位"是不是某组件引起" | 每次要构建+上机 | 保留，用于二分 |
| QEMU | 快、可 gdb | **到不了 `bsd_init`**（pmap 阶段 halt） | 不用于 exec 阶段 |
| `fastboot boot`（RAM-only） | 安全、快、可反复 | 每次要人按键重启 | 保留 |

### D 节结论：三个可立即改进的点

1. **缺"事件序列"**：现在只能看最后一个值。改进：内核侧固定 **环形日志**（如 32 条 `{id, arg}`），显示时**分页**（每页 8 条，用两位数字表示 id、两位表示 arg）。一次上机可读整段序列，不必"一探针一上机"。
2. **缺"名字"**：纯数字需要对照表。改进：加极简 5×7 字库（约 200 字节）打印事件缩写（`MOUNT`/`EXEC`/`FAULT`），直接可读。
3. **显示被 panic 清屏 / 被 abort 覆盖**：改进：把面板放在 **panic 不清扫的顶部区域**；abort 报告改为**写入环形日志**而不是覆盖主面板；条件允许时在 panic 后**重绘**一次。

### D 节长期建议

**接一个真正的控制台**（USB CDC 或板载 UART）。loader 已有 USB monitor 的先例（upstream 的 loader 就带 USB 串口）。一次性投入后，所有内核状态可直接读文本，调试轮次成本会降一个数量级。

## E. 遗漏点汇总（此前没系统列过的）

| # | 遗漏 | 影响 | 建议补法（离线优先） |
|---|---|---|---|
| P1 | panic 渲染器未验证 blob 段归属 | 看不到任何 panic | 离线检查 patch 后 cave/blob 是否完整在 `__TEXT`，改链接段 |
| P2 | 无"谁挂载了根"的标记 | "HFS 挂载成功"仍是推断 | 在 `hfs_mountfs` 成功处 / `mockfs_mountroot` 成功处各加独立事件 |
| P3 | EL0 故障采样写错（用了 EL1 编号） | 用户态失败原因未知 | 改用 0x20/0x24 并记录 FAR/PC |
| P4 | 未验证"实际执行的 init 路径" | 可能 exec 了别的候选文件 | 在 `init_programs` 循环里记录被选中的索引/路径哈希 |
| P5 | 未检查二进制页面向齐（4K vs 16K） | 可能是映射失败的隐藏原因 | 离线 `otool -l` 看 segment 对齐/`__TEXT` fileoff |
| P6 | 未确认"用户态是否真的取指过" | 一切"跑起来了"都是推断 | 让用户态做**内核可见**的动作（如 write 到已知 fd），内核侧记事件 |
| P7 | panic 前的 K2/K3 段（arm_init/arm_vm_init）无探针 | 若以后出问题无基线 | 在 `arm_init` 关键点加事件（并入环形日志） |
| P8 | 调试插桩无开关 | 正式镜像会带插桩 | 用 boot arg 控制，默认关闭 |

## F. 建议的下一步顺序（不涉及编译，待批准后执行）

1. **离线**：检查 panic cave/blob 段归属；若不可靠，改为链接段（这会恢复 panic 文字）。
2. **离线**：内核侧环形事件日志 + 5×7 字库显示（替代一次性探针）。
3. **离线**：修正 EL0 采样（0x20/0x24 + FAR/PC）并接入事件日志。
4. 之后 1 次上机：拿用户态 fault 全量数据 → 定位 C1。
5. 修 exec 映射 → 换真实 `/sbin/launchd`。
