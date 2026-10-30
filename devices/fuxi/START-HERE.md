# 从这里开始（fuxi XNU bring-up，2026-10-31）

> 一句话现状：启动链已打通到 **PID 1 被 exec**、HFS 根能挂、panic 文字通道已修复；
> 卡点是 **PID 1 用户态执行阶段收到 SIGBUS（保护故障）**，正在用镜像批次收敛原因。

## 1. 已经确认的事实（有实测证据）

| 事实 | 证据 |
|---|---|
| 162 MB boot.img 可被 fastboot 加载 | `Sending 165644 KB OKAY` |
| HFS+ 卷能作为根挂载 | 阶段刻度 15（mountroot 成功分支内）亮 |
| HFS 里的 Mach-O 被正确读取 | 内核打印 `size=16552 magic=feedfacf ncmds=7 cmds=2d8`，与文件一致 |
| exec 走到 `load_machfile` 且成功 | 失败分支探针从未触发 |
| panic 文字通道已修复 | 屏幕能显示 `initproc failed to start …` 等文本 |
| PID 1 的死亡原因是信号 | `exit reason namespace 2 subcode 0xa` = **SIGNAL / SIGBUS(10)** |

## 2. 已撤销的错误结论（别再按这些走）

1. "namespace 2 = EXEC / FAIRPLAY_DECRYPT" —— **错**。`OS_REASON_EXEC` 是 9，namespace 2 是 `OS_REASON_SIGNAL`。
2. "PID 1 已运行并正常退出" —— **未证实**。mark 29–31 在任何死亡路径都会走。
3. "panic 屏没显示是因为 vinfo 未绑定" —— 不是主因。真因是 panic blob 被放进了 **`__TEXT`（initprot=1，不可执行）**，已改到 `__TEXT_EXEC` 修复。

## 3. 待测镜像（11 个，全部已构建）

一键测试脚本：

```powershell
pwsh -NoProfile -File "C:\Users\Jason Wu\dsh\test-batch.ps1" -Image K
```

| 编号 | sha256 前缀 | 内容 | 建议 |
|---|---|---|---|
| **K** | `56190f40` | HFS + `getpid+exit(1)` | **先测**：一句话判定"用户态是否执行过" |
| **J** | `ad979cd3` | HFS + 全探针（加载/取指/数据/SIGBUS/vm_fault） | 单镜像全诊断 |
| **M** | （构建中） | HFS + 只调 `open("/hello.txt")` | 若 K 显示用户态能跑，用它定位 HFS 读路径 |
| H | `5dc38a0b` | HFS + 4K 对齐探针 | 页对齐假设 |
| I | `cd11c48c` | mockfs + 4K 对齐探针 | H 的对照 |
| F | `636ba13d` | HFS + 无栈探针 + vm_fault 判决 | 栈假设 |
| G | `98be25d6` | HFS + 写栈探针 + vm_fault 判决 | 与 F 对比 |
| A | `3ff5efdf` | HFS + EL0 故障/SIGBUS 探针 | 细节 |
| D | `a16cfc75` | HFS + 打印 `entry/sp/stack/size` | 加载参数 |
| E | `d69d4be6` | mockfs 版 D | 对照 |
| C | `9c166363` | HFS + 无栈裸探针 | 旧实验 |
| B | `e305ac66` | mockfs 普通 init | 对照 |

## 4. 判读速查

| 屏幕文字 | 含义 | 下一步 |
|---|---|---|
| `… (signal 0, exit status 1)` | 用户态**执行过**并退出 | 测 M，定位 HFS 调用 |
| `fuxi-iabort-el0: esr=… far=… pc=…` | 用户态取指故障 | 看 `pc` 落在哪；结合 D/E 的 entry |
| `fuxi-dabort-el0: esr=… far=… pc=…` | 用户态数据故障 | 看 `far` 是栈还是代码页 |
| `fuxi-vmfault: ret=… addr=… prot=…` | 故障判决，`ret=2` = 保护故障 | 结合 `prot`（读/写/执行） |
| `fuxi-sigbus: caller=…` | 内核主动投 SIGBUS | 把 `caller` 发我，反查函数 |
| `fuxi-load-bad: entry=… sp=… size=…` | 加载参数不可用 | 直接是栈/入口设置 bug |

辅助解码（ESR → 故障类型 + 探针指令偏移）：

```powershell
wsl -d Ubuntu-24.04 -u root -- python3 /root/decode-el0.py "esr=96000045 far=1000000f0 pc=1000004c8"
```

## 5. 构建与自检

```bash
# 仓库根目录（WSL）
bash devices/fuxi/scripts/local-build.sh          # 完整构建
bash devices/fuxi/scripts/verify-probes.sh        # 探针自检（应 11/11 ok）
```

规则（血泪教训）：
- 改完内核源码**先 `touch`** 那个文件再构建，否则增量 make 会跳过；
- **不要删 `.o`**：归档步骤按固定清单跑 `nm`，缺一个对象整个构建在最前面失败；
- panic 屏 blob 必须落在 `__TEXT_EXEC`（`__TEXT` 不可执行）。

## 6. 文档索引

| 文档 | 内容 |
|---|---|
| `kernel-autopilot.md` | 逐轮记录（含 Locator、撤销结论） |
| `RETROSPECTIVE-ROUND-2026-10-30.md` | 目标/链路/卡点/调试方法复盘 |
| `SIGBUS-BATCH-2026-10-31.md` | 镜像批次与判读表 |
| `DIAGNOSTIC-KERNEL.md` | 探针清单、语义、自检 |
| 仓库 `devices/fuxi/BOOT-CHAIN-LEDGER.md` | 启动链逐级证据 |

## 7. 我认为的下一步（拿到 K 的一行之后）

1. K 显示 `exit status 1` → 测 **M**（open 探针），定位 HFS 用户态读路径
2. K 显示故障探针 → 按那行文字 + `decode-el0.py` 定位
3. 之后：换真实 `/sbin/launchd`（需要把真实二进制放进 HFS 卷）
4. 最后再回到"NativeUfs/extent 路线"的取舍
