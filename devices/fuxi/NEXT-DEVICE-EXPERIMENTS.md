# 下次上机实验包（判据已写明）

> 生成时间：2026-10-27（用户离开期间，目标模式自动推进）
> 约束：**只用 `fastboot boot`，不写任何分区**；失败长按电源 10–15 秒回 bootloader。
> 每个实验都是单变量，按顺序做即可；**卡住不推进就停，记录现象**。

---

## 实验 0（基线，1 分钟）

- 镜像：`artifacts-ZB/boot.img`（sha256 `13742060…`，含定时器计数，优先用这个）
  - 旧版可用 `artifacts-ZA/boot.img`（`9b8a0568…`，无定时器计数）
- 内容：PID 1 只调用 `getpid()` 后 `exit(1 或 2)`；**不碰任何可能阻塞的调用**
- 期望屏幕：
  - `initproc failed to start … (signal 0, exit status 1 …) timer-irqs=N` → **本次实验成功**：
    syscall 能执行、进程 1 身份正确；**同时 N 直接回答定时器问题**：
    - `timer-irqs=0` → 定时器中断从未触发（证实假设）
    - `timer-irqs>0` → 定时器正常，静默挂起另有原因
  - `exit status 2` → getpid ≠ 1（意外，记录下来）
  - **无文本** → 说明连 `getpid + exit` 都走不到，问题在 mockfs/exec 层或调度，不是 init 的 syscall
- 为什么先做这个：此前带 `open()/write()/reboot()` 的版本全部静默，需要先确认最小路径可用。

## 实验 1（阻塞路径定位）

- 镜像：需要现场构建（把 `devices/fuxi/experiments/init-probe3.c` 拷到
  `devices/fuxi/platform-power/init.c` 后跑 `local-build.sh`）
- 内容：`getpid`+`getuid`+`geteuid`+`open("/dev/null" "/" "/dev/zero" "/dev")`+`write(1)`+`close`
- 期望：
  - 出现 `exit status N` → 按位解码（详见 `fuxi-routes.md`）就知道 devfs/mockfs 哪些调用可用
  - 仍然无文本 → 结合实验 0 的结论，问题在某个 `open` 上；下一步把它拆成单个调用

## 实验 2（panic 日志地址，验证离线修复）

- 本次构建已包含修复：ADT 增加顶层 `pram { reg = <0xFFFF0000, 0x4000> }` 与
  `chosen/embedded-panic-log-size`
- 期望：内核日志里 **不再出现** `debug_log_init: Error!! gPanicBase is still not initialized`
  （屏幕上看不到内核日志；这一项主要通过 QEMU 已验证，真机作为补充确认）
- 风险：若 `0xFFFF0000` 不可写，`ml_io_map_wcomb` 失败并安全回退，不会崩溃

## 实验 3（定时器中断是否真的存在）

- 目的：验证 Xyvern 实测的 **fuxi vtimer INTID = 19** 假设；这关系到调度/wakeup 是否工作
- 需要先做的离线改动（下一轮）：内核侧在 timer FIQ 路径加计数器，panic 屏打印该计数
- 判据：计数 > 0 → 定时器正常；恒为 0 → INTID 不对，需要按 19 配置

## 实验 4（UFS 只读探针，镜像已就绪）

- 镜像：`artifacts-ZD/boot.img`（sha256 `9b09de71…`），内核参数已含 `fuxi-ufs-probe=1`
- 实现：把参考驱动 `NativeUfs::inspect()` 的**只读**逻辑移植进 platform-power stub，
  在 `sheng_pmgr_late_probe()`（bsd_autoconf 内）执行：
  1. 先只映射并读 GCC 电源域寄存器（GDSCR bit31 = PWR_ON，CBCR bit31 = CLK_OFF）
  2. **只有** PHY 已上电且 AXI/AHB 使能位满足时，才去读 UFS 控制器寄存器（CAP/VER/IS/HCS/HCE）
  3. 用 `panic()` 打印全部读数（屏幕可见）
- 期望屏幕（示例）：
  ```
  fuxi-ufs-probe: gdsc=8xxxxxxx axi=... ahb=... unipro=... txs=... rxs=... aux=...
  powered=1 busclocks=1 hc_read=1
  cap=... ver=... is=... hcs=... hce=...
  ```
- 判据：
  - `hc_read=1` 且 `ver` 是合理的 UFSHCI 版本 → 控制器可访问，UFS 路线可行
  - `powered=0` → 固件没给 UFS 上电（需要 GCC 侧配置后才能读）
  - 屏幕无输出或直接回到菜单 → 探针在早期就挂住，需要回退（长按电源）
- **安全**：全程只读，不写寄存器、不写分区；默认镜像（`artifacts-ZC`，`4e3394a1…`）
  **不带**该参数，可作为对照。

## 实验 5（NativeUFS 完整只读驱动，后续）

- 需要先做：`devices/fuxi/NATIVEUFS-PLAN.md` 的路径 A（ADT 加 `qcom,sm8550-ufshc` 节点 +
  内核内建 bootstrap）
- 引导参数：`native-ufs-probe=2 native-ufs-diagnostics=1`
- 判据：日志出现 `NativeUfs: start provider=… probe=2 …` 且 GCC GDSC 上电

---

## 现象 → 结论 对照表

| 屏幕现象 | 结论 | 下一步 |
|---|---|---|
| 实验 0 出现 `exit status 1` | syscall + 进程 1 正常 | 做实验 1 |
| 实验 0 出现 `exit status 2` | getpid 返回值异常 | 查 mach 层 getpid 实现 |
| 实验 0 无文本 | exec/调度层问题 | 回到 mockfs/exec 排查，别再改 init |
| 实验 1 有 `exit status` | 拿到 devfs/mockfs 能力位图 | 按位决定下一步 |
| 实验 1 无文本 | 某个 open 阻塞 | 拆成单调用逐个试 |
| 任何实验出现新 panic 文本 | 新信息 | 按文本定位 |

## 每次上机记录什么

- 镜像 sha256、屏幕原文（拍照或抄写）、耗时、是否需要按电源恢复
- 结果写回 `kernel-autopilot.md`，并把新镜像放进 `artifacts-*`
