# fuxi bring-up 一页总结（2026-10-31 08:01）

## 1. 一句话

启动链已打通到 **PID 1 被 exec**；HFS 根能挂、panic 文字通道可用；当前死在 **SIGBUS**。
三个候选死因都做成了"一次上机即可判定"的镜像，**剩下的全部取决于真机数据或 IPSW 根目录**。

## 2. 已确认事实（都有实测证据）

| 事实 | 证据 |
|---|---|
| 162 MB boot.img 可被 fastboot 加载 | `Sending 165644 KB OKAY` |
| HFS+ 可作根挂载 | 阶段刻度 15（mountroot 成功分支内）亮 |
| HFS 内容读取正确 | 内核打印 `size=16552 magic=feedfacf ncmds=7 cmds=2d8` |
| exec 走到 `load_machfile` 且成功 | 失败分支探针从未触发 |
| panic 文字通道可用 | 屏幕出现 `initproc failed to start …` |
| PID 1 死因是信号 | `namespace 2 subcode 0xa` = **SIGNAL / SIGBUS(10)** |
| 驱动不自己开关电源/时钟 | `hostBringUp` 直接映射控制器；`inspect` 只读 GDSCR/CBCR |
| 内核可把任意 RAM 注册成 md | `bsd/dev/memdev.c` 的 `mdevadd` 无条件编译；`phys=0` 时 base 是内核虚拟地址 |
| HFS 是外部编译 + force_load | `sweet_hfs.h` 里 `NSWEET_HFS 0`，`ios-obj` 无 hfs 目标文件 |

## 3. 已撤销的错误结论（不要按这些走）

1. "`namespace 2` = EXEC / FAIRPLAY_DECRYPT" —— 错，`OS_REASON_EXEC` 是 9，namespace 2 是 SIGNAL
2. "PID 1 已运行并正常退出" —— 未证实，mark 29–31 在任何死亡路径都会走
3. "panic 屏不显示是 vinfo 未绑定" —— 真因是 blob 落在不可执行的 `__TEXT`
4. "需要给 ADT 的 `ufshc` 补 `reg`" —— 不需要，驱动用硬编码基址

## 4. SIGBUS 的三条候选路径与判定方法

| 候选 | 判别镜像 | 看到什么 → 结论 |
|---|---|---|
| 内核给的 sp 未 16 对齐 | **A3**（打印 sp 低 4 位） | `low=0` = 已对齐（修复生效）；`low=8` = 仍未对齐 |
| 探针自身未对齐 sp | **A2 / K2**（带对齐入口） | 出现 `exit status` = 探针侧没问题 |
| 对齐以外的类别（如外部中止） | **C2 / T** | 数码管 **52**=对齐故障；**54**=catch-all（同步外部中止）；T 给 `a_esr` |

## 5. 最小待测集合（4 步）

| 字母 | sha256（前 12） | 大小 | 回答什么 |
|---|---|---|---|
| **A3** | `b8b328796e6a` | 162 MiB |
| **A4** | `9b1897b5d157` | 162 MiB |
| **A2** | `084b679eec7a` | 162 MiB |
| **K2** | `8681a9cadd11` | 162 MiB |
| **P** | `5487c2bf146b` | 162 MiB |
| **U** | `ca3f313b2b9e` | 162 MiB |
| **L** | `d98b8dd3b1b5` | 162 MiB |
| **R** | `56329a9d311b` | 162 MiB |
| **C2** | `0db35a1a5e19` | 162 MiB |
| **T** | `e82ed508f3aa` | 162 MiB |

推荐顺序：**A3 → A4 → A2/K2 → P**；UFS 路线成功后才测 U/L/R。

## 6. 需要你提供（缺了会卡住什么）

| 需要 | 缺了会怎样 |
|---|---|
| **一次真机测试**（任意一张） | 只能继续做离线审计，无法推进真实故障 |
| **IPSW 根目录**（launchd / dyld / dyld_shared_cache_arm64e） | 无法进入"真实 launchd"阶段 |
| userdata 分区大小 | 可用 256 MiB 缺省，仅影响刷写对齐 |

## 7. 文档索引

| 文档 | 内容 |
|---|---|
| `HANDOVER.md` | 交接包（状态、三件事、**31** 张镜像索引、需求、未决问题） |
| `TEST-RUNBOOK.md` | 单页测试手册（逐镜像判读 + 恢复） |
| `SP-ALIGN-AUDIT.md` | SP 对齐改动 7 项审计 |
| `UFS-PREFLIGHT.md` | UFS 只读体检判读（DMA 4 GiB 约束） |
| `ROUTE-TO-REAL-LAUNCHD.md` | M1/M2 路线、userdata 格式、端到端手册 |
| `kernel-autopilot.md` | 逐轮记录（Locator / 证据等级 / 撤销结论） |
| `verify-artifacts.ps1` / `verify-consistency.ps1` | 镜像完整性与四者一致性 |
| `rehearse-intake.sh` | 用合成根目录演练整条投放链 |

## 8. 数字复核（本文件由清单现场生成，31 张镜像）