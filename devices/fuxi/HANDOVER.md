# fuxi XNU bring-up 交接包（2026-10-31 07:58）

> 一页看懂：现在到哪、回来先做什么、每张镜像是什么、还需要你提供什么。

## 1. 当前状态（一段话）

启动链已经打通到 **PID 1 被 exec**、HFS 根能挂、panic 文字通道可用。
最近的观测是 **PID 1 死于 SIGBUS**（
amespace 2 subcode 0xa = SIGNAL/SIGBUS(10)，**不是** FairPlay/解密）。
本轮之后的两个候选死因都已做成可判定的镜像：

1. **用户态入口 SP 未 16 对齐**（探针首个 `stp` 直接对齐故障）→ 镜像 **A3** 直接打印内核算出的 sp 低 4 位；**A4** 携带内核侧修复
2. **探针自身未对齐**（A–C2 这批镜像的探针入口没有对齐 sp）→ 镜像 **A2**/**K2** 带对齐入口

M2（根镜像放 userdata、内核读进 RAM 再挂载）的**代码链路已全部写完**：读窗口（含 sha256 校验）→ `mdevadd` 注册 md → `setconf` 覆盖 rootdev，只等真机验证。

## 2. 回来先做的三件事

```powershell
# 0) 先确认镜像没坏（31 项检查，秒级）
pwsh -NoProfile -File "C:\Users\Jason Wu\dsh\verify-artifacts.ps1"

# 1) 用户态判定：先 A3（证明 sp 对齐），再 A4（修复版能否跑起来）
pwsh -NoProfile -File "C:\Users\Jason Wu\dsh\test-batch.ps1" -Image A3
pwsh -NoProfile -File "C:\Users\Jason Wu\dsh\test-batch.ps1" -Image A4

# 2) UFS 硬件事实：P（只读，不链接驱动）
pwsh -NoProfile -File "C:\Users\Jason Wu\dsh\test-batch.ps1" -Image P
```

每张镜像的完整判读表在 **`TEST-RUNBOOK.md`**；出现 panic 时把它那一行贴给我，我这边用
`decode-panic.py` 直接给出符号与 ESR 解码。

## 3. 镜像索引（31 张，自动生成）

| 字母 | sha256（前 12） | 大小 | 路径 |
|---|---|---|---|
| `A` | `3ff5efdff553` | 162 MiB | C:\Users\Jason Wu\dsh\artifacts-SIGBUS\boot.img |
| `A2` | `084b679eec7a` | 162 MiB | C:\Users\Jason Wu\dsh\artifacts-ALIGNED\boot.img |
| `A3` | `b8b328796e6a` | 162 MiB | C:\Users\Jason Wu\dsh\artifacts-SP-PROBE\boot.img |
| `A4` | `9b1897b5d157` | 162 MiB | C:\Users\Jason Wu\dsh\artifacts-SP-FIX\boot.img |
| `B` | `e305ac662245` | 162 MiB | C:\Users\Jason Wu\dsh\artifacts-SIGBUS-CONTROL\boot.img |
| `C` | `9c166363127c` | 162 MiB | C:\Users\Jason Wu\dsh\artifacts-HFS-NAKED\boot.img |
| `C2` | `0db35a1a5e19` | 162 MiB | C:\Users\Jason Wu\dsh\artifacts-SIGBUS-CLASS\boot.img |
| `D` | `a16cfc755733` | 162 MiB | C:\Users\Jason Wu\dsh\artifacts-LOADINFO\boot.img |
| `E` | `d69d4be68f33` | 162 MiB | C:\Users\Jason Wu\dsh\artifacts-NAKED-MOCKFS\boot.img |
| `F` | `636ba13dbda8` | 162 MiB | C:\Users\Jason Wu\dsh\artifacts-VMFAULT\boot.img |
| `G` | `98be25d6ace5` | 162 MiB | C:\Users\Jason Wu\dsh\artifacts-VMFAULT-STACK\boot.img |
| `H` | `5dc38a0b4ea2` | 162 MiB | C:\Users\Jason Wu\dsh\artifacts-4K-HFS\boot.img |
| `I` | `cd11c48cc811` | 162 MiB | C:\Users\Jason Wu\dsh\artifacts-4K-MOCKFS\boot.img |
| `J` | `ad979cd32ded` | 162 MiB | C:\Users\Jason Wu\dsh\artifacts-OMNIDIAG\boot.img |
| `K` | `56190f401999` | 162 MiB | C:\Users\Jason Wu\dsh\artifacts-HFS-EXIT1\boot.img |
| `K2` | `8681a9cadd11` | 162 MiB | C:\Users\Jason Wu\dsh\artifacts-EXIT7-ALIGNED\boot.img |
| `L` | `d98b8dd3b1b5` | 162 MiB | C:\Users\Jason Wu\dsh\artifacts-UFSREAD2\boot.img |
| `M` | `2c2e693036d7` | 162 MiB | C:\Users\Jason Wu\dsh\artifacts-HFS-OPEN\boot.img |
| `N` | `3765a9060581` | 162 MiB | C:\Users\Jason Wu\dsh\artifacts-HFS-DATA\boot.img |
| `O` | `b96533c22f73` | 162 MiB | C:\Users\Jason Wu\dsh\artifacts-SIGBUS-STATE\boot.img |
| `P` | `5487c2bf146b` | 162 MiB | C:\Users\Jason Wu\dsh\artifacts-UFS-CAP\boot.img |
| `Q` | `283f52c2928e` | 162 MiB | C:\Users\Jason Wu\dsh\artifacts-PAGEIN\boot.img |
| `R` | `56329a9d311b` | 162 MiB | C:\Users\Jason Wu\dsh\artifacts-UFSREAD-MOUNT\boot.img |
| `S` | `416e16dd119d` | 402 MiB | C:\Users\Jason Wu\dsh\artifacts-SIZE256\boot.img |
| `T` | `e82ed508f3aa` | 162 MiB | C:\Users\Jason Wu\dsh\artifacts-ABORT-RING\boot.img |
| `U` | `ca3f313b2b9e` | 162 MiB | C:\Users\Jason Wu\dsh\artifacts-UFSREAD\boot.img |
| `V` | `ed626a572459` | 162 MiB | C:\Users\Jason Wu\dsh\artifacts-NATIVEUFS-START\boot.img |
| `W` | `1339b20b8ae8` | 162 MiB | C:\Users\Jason Wu\dsh\artifacts-UFS-CLOCK\boot.img |
| `X` | `07726d8a5a67` | 162 MiB | C:\Users\Jason Wu\dsh\artifacts-UFS-X\boot.img |
| `Y` | `d98141a70444` | 162 MiB | C:\Users\Jason Wu\dsh\artifacts-MDEV\boot.img |
| `Z` | `4d8f7163c630` | 162 MiB | C:\Users\Jason Wu\dsh\artifacts-MDEV-REPORT\boot.img |

## 4. 需要你提供的东西

| 需要 | 用途 | 没有它的后果 |
|---|---|---|
| **设备时间**（任意一次测试） | 任何一张镜像的结果都能把"猜"变成"改代码" | 只能继续离线审计 |
| **IPSW 的根目录**（`/sbin/launchd`、`/usr/lib/dyld`、`dyld_shared_cache_arm64e`） | 真实 launchd 的内容 | 无法进入真实内容阶段 |
| userdata 分区大小（`fastboot getvar partition-size:userdata`） | 打包镜像的填充尺寸 | 可用 256 MiB 缺省值 |

## 5. 未决问题（按重要性）

1. **SIGBUS 真因**：A3 的 `low` 值一出来即可判定是否 SP 对齐；不是的话用 C2（类别数字 50/52/54）+ T（`a_esr`）
2. **UFS 电源/CAP**：P 的 `powered` 与 `cap bit24` 决定驱动路线是否可行
3. **M2 读取链路**：U（报告）/ L（32 位 DMA 兜底）/ R（挂根）
4. **真实 launchd 内容**：缺 IPSW 根目录

## 6. 文档与记录

| 文档 | 内容 |
|---|---|
| `TEST-RUNBOOK.md` | 单页测试手册：每张镜像的判读与恢复 |
| `ARTIFACTS-MANIFEST.md` | 镜像用途与推荐顺序（人读版） |
| `SP-ALIGN-AUDIT.md` | SP 对齐改动的 7 项审计 |
| `UFS-PREFLIGHT.md` | UFS 只读体检判读（DMA 4 GiB 约束等） |
| `ROUTE-TO-REAL-LAUNCHD.md` | M1/M2 路线、userdata 格式、端到端手册 |
| `kernel-autopilot.md` | 逐轮记录（含 Locator 与撤销结论） |
| repo `devices/fuxi/START-HERE.md` | 仓库侧入口（与本地一致） |

## 7. 纪律要点（避免重踩）

- 全程只用 `fastboot boot`，**不写 boot/bootloader/GPT**；userdata 可用 `fastboot erase userdata` 恢复
- HFS 源码是**外部编译 + force_load**：改 `bsd/sweet_hfs` 后 `local-build.sh` 会自动重建归档
- 探针必须带对齐入口（`build-probe.py` 会在构建时反汇编自检）
- `fuxi_trace_mark` 的正式声明是 `void fuxi_trace_mark(int)`，本地不要再写 `extern`

## 8. 第 37–38 轮新增的工具（都已在离线验证过）

| 工具 | 用途 | 验证方式 |
|---|---|---|
| `verify-consistency.ps1` | 交叉核对"测试脚本字母映射 / 清单 / 磁盘 / 文档引用哈希" | 实测 `consistency: 329 check(s) ... consistency: ok`，另有 120 个历史目录仅作提示 |
| `rehearse-intake.sh` | 用**合成根目录**跑通 IPSW 投放全链（检查器 → 投放 dry-run → 打包 → 刷写计划） | 实测最后一行 `rehearsal: ok` |
| `decode-panic.py`（升级） | 支持**多记录 panic 屏**（一次截图里有多行 panic），逐条符号化与 ESR 解码 | selftest 4 项断言 + 双记录样例 `records: 2` |

回来后的建议顺序（补一条 0.5）：

```powershell
pwsh -NoProfile -File "C:\Users\Jason Wu\dsh\verify-consistency.ps1"   # 四者一致性
```