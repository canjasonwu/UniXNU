# 待测镜像总清单（2026-10-31）

> 快照：23 个 `boot.img`，全部在 `C:\Users\Jason Wu\dsh\artifacts-*\boot.img`。
> 完整性校验：`pwsh -NoProfile -File C:\Users\Jason Wu\dsh\verify-artifacts.ps1`（对 `artifacts-manifest.json`）

## 测试顺序（省轮次）

| 阶段 | 字母 | 目的 |
|---|---|---|
| ① 定性：用户态到底跑没跑过 | **K** | HFS 根 + `getpid+exit(1)`：出现 `exit status 1` 即证用户态执行过 |
| ② UFS 硬件事实（只读） | **P** | 电源/时钟/CAP（含 32/64 位 DMA 能力），不链接驱动 |
| ③ UFS 驱动（按 P 的结果选） | **U** / **X** / **W** | U=读窗口报告；X=时钟+驱动启动；W=只时钟 |
| ④ 从自己注册的 md 挂根 | **R** / **Y** / **Z** | R=UFS 读后挂根；Y=注册 md 静默切根；Z=报告设备号 |
| ⑤ 若 ① 显示用户态能跑 | **M** / **N** | M=只调 open；N=带 `__DATA` 的探针 |
| ⑥ SIGBUS 细节（备用） | **A** / **F** / **G** / **J** | A=EL0 故障+SIGBUS 来源；F/G=vm_fault 判决（无栈/写栈）；J=单镜像全诊断 |
| ⑦ 对照 | **B** / **C** / **D** / **E** / **H** / **I** | mockfs 对照、无栈、加载参数、4K 对齐对比 |
| ⑧ 容量天花板 | **S** | 421 MB boot.img 能否被 `fastboot boot` 接受 |

## 镜像明细

| 字母 | sha256（前 12） | 大小 | 内容 |
|---|---|---|---|
| A | `3ff5efdff553` | 162 MiB | HFS 根，探针 getpid→open(/hello.txt)→write→空转；EL0 故障 + SIGBUS 来源探针 |
| B | `e305ac662245` | 162 MiB | mockfs 根，普通 init（对照） |
| C | `9c166363127c` | 162 MiB | HFS 根，无栈裸汇编探针 |
| D | `a16cfc755733` | 162 MiB | HFS 根，exec 后打印 entry/sp/stack/size |
| E | `d69d4be68f33` | 162 MiB | mockfs 根，D 的对照 |
| F | `636ba13dbda8` | 162 MiB | HFS 根，无栈 + vm_fault 判决 |
| G | `98be25d6ace5` | 162 MiB | HFS 根，写栈 + vm_fault 判决 |
| H | `5dc38a0b4ea2` | 162 MiB | HFS 根，4K 对齐探针 |
| I | `cd11c48cc811` | 162 MiB | mockfs 根，4K 对齐探针（H 对照） |
| J | `ad979cd32ded` | 162 MiB | HFS 根，全探针单镜像诊断 |
| L | `d98b8dd3b1b5` | 162 MiB | 同 U（UFS 读窗口到 RAM），但驱动含低地址 DMA 兜底（4 次重试） |
| K | `56190f401999` | 162 MiB | HFS 根，`getpid+exit(1)`（用户态判据） |
| M | `2c2e693036d7` | 162 MiB | HFS 根，只调 `open("/hello.txt")`，退出码 5/6 |
| N | `3765a9060581` | 162 MiB | HFS 根，带 `__DATA` 的探针（写全局 + open） |
| P | `5487c2bf146b` | 162 MiB | 只读 UFS 体检（powered/busclocks/cap），不链接驱动 |
| R | `56329a9d311b` | 162 MiB | UFS 读 OS 窗口→注册 md→静默切根 |
| S | `416e16dd119d` | **402 MiB** | 256 MiB ramdisk，容量天花板测试 |
| U | `ca3f313b2b9e` | 162 MiB | UFS 时钟 + 读 OS 窗口→注册 md（报告模式） |
| V | `ed626a572459` | 162 MiB | NativeUfs 启动钩子 + 驱动只读检查 |
| W | `1339b20b8ae8` | 162 MiB | UFS 时钟分支使能报告 |
| X | `07726d8a5a67` | 162 MiB | 时钟使能 + 驱动启动 + 只读检查 |
| Y | `d98141a70444` | 162 MiB | 用现有 RAMDisk 注册第二个 md 并静默切根 |
| Z | `4d8f7163c630` | 162 MiB | Y 的报告版（panic 打印设备号） |

## 相关文档

| 文档 | 内容 |
|---|---|
| `START-HERE.md` | 总入口：现状、已撤销结论、批次、下一步 |
| `SIGBUS-BATCH-2026-10-31.md` | SIGBUS 批次的判读表 |
| `DIAGNOSTIC-KERNEL.md` | 探针清单与自检脚本 |
| `ROUTE-TO-REAL-LAUNCHD.md` | M1/M2 路线、userdata 格式、端到端手册 |
| `UFS-PREFLIGHT.md` | UFS 只读体检判读（含 DMA 4 GiB 约束与决策表） |
| `RETROSPECTIVE-ROUND-2026-10-30.md` | 复盘（目标/链路/卡点/调试方法） |
