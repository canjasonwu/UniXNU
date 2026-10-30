# 从这里开始（fuxi XNU bring-up，2026-10-31 更新）

> 一句话现状：启动链已打通到 **PID 1 被 exec**、HFS 根能挂、panic 文字通道已修复；
> 卡点是 **PID 1 用户态执行阶段收到 SIGBUS（保护故障）**，用镜像批次收敛中。
> 同时 **M2（根镜像放在 userdata、由内核读进 RAM 再挂载）的代码链路已全部写完**，等真机验证。

## 0. 先看这个：23 张镜像的完整性

```powershell
pwsh -NoProfile -File "C:\Users\Jason Wu\dsh\verify-artifacts.ps1"
```

应输出 `checked 23 artifact(s): ok=23 bad=0`。清单见 `ARTIFACTS-MANIFEST.md`
（每个镜像的用途 + 推荐测试顺序）。

## 1. 已经确认的事实（有实测证据）

| 事实 | 证据 |
|---|---|
| 162 MB boot.img 可被 fastboot 加载 | `Sending 165644 KB OKAY` |
| HFS+ 卷能作为根挂载 | 阶段刻度 15（mountroot 成功分支内）亮 |
| HFS 里的 Mach-O 被正确读取 | 内核打印 `size=16552 magic=feedfacf ncmds=7 cmds=2d8`，与文件一致 |
| exec 走到 `load_machfile` 且成功 | 失败分支探针从未触发 |
| panic 文字通道已修复 | 屏幕能显示 `initproc failed to start …` 等文本 |
| PID 1 的死亡原因是信号 | `exit reason namespace 2 subcode 0xa` = **SIGNAL / SIGBUS(10)** |
| 驱动自身不开电源/时钟 | `hostBringUp` 直接映射控制器使用；`inspect` 只读 GDSCR/CBCR |
| 内核有 `mdevadd`，可把任意 RAM 注册成 md 设备 | `bsd/dev/memdev.c` 无条件编译，`mdevadd`/`mdevlookup` 在内核符号里 |

## 2. 已撤销的错误结论（别再按这些走）

1. "namespace 2 = EXEC / FAIRPLAY_DECRYPT" —— **错**。`OS_REASON_EXEC` 是 9，namespace 2 是 `OS_REASON_SIGNAL`。
2. "PID 1 已运行并正常退出" —— **未证实**。mark 29–31 在任何死亡路径都会走。
3. "panic 屏没显示是因为 vinfo 未绑定" —— 不是主因。真因是 panic blob 被放进了 **`__TEXT`（initprot=1，不可执行）**，已改到 `__TEXT_EXEC` 修复。
4. "需要给 ADT 的 `ufshc` 补 `reg`" —— **不需要**。驱动用硬编码基址，启动钩子传的是 `IOPlatformExpertDevice`。

## 3. 推荐测试顺序

### 第一优先：定性 + 硬件事实

| 字母 | sha256 前缀 | 作用 |
|---|---|---|
| **K** | `56190f40` | HFS + `getpid+exit(1)`：出现 `exit status 1` 即证**用户态执行过** |
| **P** | `5487c2bf` | 只读 UFS 体检：电源/时钟/**CAP（含 32/64 位 DMA）**，不链接驱动 |

### 第二优先：UFS 读取链路（按 P 的结果选）

| 字母 | sha256 前缀 | 作用 |
|---|---|---|
| **U** | `ca3f313b` | 时钟 + 读 `OS` 窗口到 RAM + 注册 md（报告模式） |
| **L** | `d98b8dd3` | 同 U，但驱动含**低地址 DMA 兜底**（CAP bit24=0 时用这个） |
| **X** | `07726d8a` | 时钟使能 + 驱动启动 + 驱动只读检查 |
| **W** | `1339b20b` | 只测时钟分支使能 |
| **R** | `56329a9d` | 读窗口后**静默切根**到该 md（真挂载测试） |

### 第三优先：md 机制与细节诊断

| 字母 | sha256 前缀 | 作用 |
|---|---|---|
| **Y** / **Z** | `d98141a7` / `4d8f7163` | 用现有 RAMDisk 注册第二个 md 并切根 / 报告版 |
| **J** | `ad979cd3` | 单镜像全探针诊断 |
| **M** / **N** | `2c2e6930` / `3765a906` | `open()` 探针 / 带 `__DATA` 的探针（K 显示用户态能跑后测） |
| A/F/G | `3ff5efdf` / `636ba13d` / `98be25d6` | EL0 故障、`vm_fault` 判决（无栈/写栈） |
| H/I/B/C/D/E | 见 `ARTIFACTS-MANIFEST.md` | 4K 对齐、mockfs 对照、加载参数 |
| **S** | `416e16dd` | 402 MB boot.img，容量天花板 |

一键测试：

```powershell
pwsh -NoProfile -File "C:\Users\Jason Wu\dsh\test-batch.ps1" -Image K
```

## 4. 判读速查

| 屏幕文字 | 含义 | 下一步 |
|---|---|---|
| `… (signal 0, exit status 1)` | 用户态**执行过**并退出 | 测 M，定位 HFS 调用 |
| `fuxi-iabort-el0: esr=… far=… pc=…` | 用户态取指故障 | 看 `pc`；结合 D/E 的 entry |
| `fuxi-dabort-el0: esr=… far=… pc=…` | 用户态数据故障 | 看 `far` 是栈还是代码页 |
| `fuxi-vmfault: ret=… addr=… prot=…` | 故障判决，`ret=2` = 保护故障 | 结合 `prot` |
| `fuxi-sigbus: caller=…` | 内核主动投 SIGBUS | 把 `caller` 发我反查 |
| `fuxi-ufs-read: ok dev=0x.. bytes=..` | UFS 读通、sha256 通过、md 注册成功 | 改 `read=2`（镜像 R）测挂根 |
| `fuxi-ufs-read: window read failed (-1)` | host bring-up 失败（电源/时钟/DMA/SMMU） | 看驱动日志 `why`；必要时用 L |
| `window read failed (-3/-4)` | GPT/清单解析失败 | 确认 userdata 已按格式刷入 |
| `window read failed (-8)` | sha256 不符 | 检查刷写方式 |
| `fuxi-ufs-clock: powered=0` | 电源域没开 | 需要 RPMh 上电，别在驱动侧耗 |

辅助解码：

```powershell
wsl -d Ubuntu-24.04 -u root -- python3 /root/decode-el0.py "esr=96000045 far=1000000f0 pc=1000004c8"
```

## 5. 工具链（投放真实 iOS 根内容时用）

```bash
# 1) 投放前自检（必需文件 / arm64 / 体积 / shared cache 分片）
python3 devices/fuxi/scripts/check-ios-root.py check --root /path/to/ios-root --print-command

# 2) 造卷（自动建骨架；--launchd / --file 投放；写后全卷 sha256 比对）
cd /root/hfs-populate && python3 populate-hfs.py --launchd /path/to/launchd \
  --file /path/to/dyld:/usr/lib/dyld \
  --file /path/to/dyld_shared_cache_arm64e:/System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64e

# 3) 打包 userdata 镜像（分区感知填充 + 刷写计划）
bash devices/fuxi/scripts/build-userdata-root.sh --hfs /root/fuxi-root/root.hfs \
  --out /tmp/userdata-root.img --pad-to 268435456
```

## 6. 构建与自检

```bash
# 仓库根目录（WSL）
bash devices/fuxi/scripts/local-build.sh          # 完整构建
bash devices/fuxi/scripts/verify-probes.sh        # 探针自检（应 11/11 ok）
```

规则（血泪教训）：
- 改完内核源码**先 `touch`** 那个文件再构建，否则增量 make 会跳过；
- **不要删 `.o`**：归档步骤按固定清单跑 `nm`，缺一个对象整个构建在最前面失败；
- panic 屏 blob 必须落在 `__TEXT_EXEC`（`__TEXT` 不可执行）；
- 配置翻转（`CONFIG_XNU_NATIVE_UFS`）必须删 `startup.o.json` 并整体重编，否则不生效。

## 7. 文档索引

| 文档 | 内容 |
|---|---|
| `ARTIFACTS-MANIFEST.md` | 23 张镜像的用途与测试顺序 |
| `kernel-autopilot.md` | 逐轮记录（含 Locator、撤销结论） |
| `SIGBUS-BATCH-2026-10-31.md` | SIGBUS 批次判读表 |
| `DIAGNOSTIC-KERNEL.md` | 探针清单、语义、自检 |
| `ROUTE-TO-REAL-LAUNCHD.md` | M1/M2 路线、userdata 格式、端到端手册 |
| `UFS-PREFLIGHT.md` | UFS 只读体检判读（DMA 4 GiB 约束、决策表） |
| `RETROSPECTIVE-ROUND-2026-10-30.md` | 复盘（目标/链路/卡点/调试方法） |
| 仓库 `devices/fuxi/BOOT-CHAIN-LEDGER.md` | 启动链逐级证据 |

## 8. 下一步（按结果分支）

1. K 显示 `exit status 1` → 测 **M/N**，定位用户态 HFS 读路径
2. K 显示故障探针 → 用 `decode-el0.py` + 那行文字定位
3. P 给出 CAP/power → 选 **U/L/X/W** 推进，再 **R** 测"根来自 userdata"
4. 拿到 IPSW 根内容 → `check-ios-root.py` → `populate-hfs.py` → `build-userdata-root.sh` → 真 launchd
