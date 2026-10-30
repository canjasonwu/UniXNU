# fuxi 测试总手册（2026-10-31，30/40 轮）

> 一页看完：先测哪张、怎么测、屏幕出现什么代表什么、出问题怎么恢复。
> 镜像完整性先跑 `pwsh -NoProfile -File C:\Users\Jason Wu\dsh\verify-artifacts.ps1`（期望 `ok=28 bad=0`）。

## 1. 三条主线（按优先）

| 线 | 问题 | 镜像 |
|---|---|---|
| **A. 用户态到底跑没跑** | SIGBUS 是内核问题还是探针问题？ | **A2**（唯一带 SP 对齐入口） |
| **B. UFS 硬件事实** | 电源/时钟/CAP（含 32/64 位 DMA） | **P**（只读，不链接驱动） |
| **C. UFS 读取链路** | 能否从 userdata 读出镜像并挂根 | **U**（报告）/ **L**（DMA 兜底）/ **R**（挂根） |

## 2. 推荐执行顺序

```powershell
# 0) 完整性
pwsh -NoProfile -File "C:\Users\Jason Wu\dsh\verify-artifacts.ps1"

# 1) 用户态判定（最重要）
pwsh -NoProfile -File "C:\Users\Jason Wu\dsh\test-batch.ps1" -Image A2

# 2) UFS 只读体检
pwsh -NoProfile -File "C:\Users\Jason Wu\dsh\test-batch.ps1" -Image P

# 3) 按 P 的结果选一张
#    powered=1 且 busclocks=1 且 cap bit24!=0  -> U
#    cap bit24==0（只支持 32 位 DMA）         -> L
#    powered=0                                 -> 先解决 RPMh，别再耗驱动
# 4) U 成功后再测 R（挂根）
```

## 3. 每张镜像的判读

### A3（内核侧报告初始用户 SP 的低 4 位）

这张**不需要用户态能跑**：内核在 `thread_setuserstack` 之前把算出来的 sp 打印出来并停下。

| 输出 | 结论 |
|---|---|
| `fuxi-sp-align: sp=… low=0 entry=…` | 内核给的 sp **本来就是 16 对齐** → SP 假设不成立，死因在别处（用 C2/T） |
| `fuxi-sp-align: sp=… low=8 entry=…` | ✅ **假设成立**：内核给的 sp 只有 8 对齐 → 旧探针首个 `stp` 必然对齐故障 → SIGBUS |
| 七段数码管出现 **55** | 同上（内核检测到 sp 未对齐并点亮 55） |

### A4（内核已修：入口前把 sp 向下对齐到 16）

| 输出 | 结论 |
|---|---|
| 出现 `exit status 20/21/22` 或与基线相同的 `initproc failed to start …` | ✅ 修复生效（用户态至少能跑到探针逻辑） |
| 仍是 SIGBUS | sp 对齐不是全部原因 → 用 C2/T 继续 |

### A2（对齐入口 + `__DATA` 探针）

屏幕/结果 → 结论：

| 输出 | 结论 | 下一步 |
|---|---|---|
| `… (signal 0, exit status 20)` | ✅ 用户态跑通，`__DATA` 读写正常，但 PID 不是 1 | 查 exec 的 pid 语义 |
| `exit status 21` | ✅ 用户态跑通 + `open("/hello.txt")` 成功 | 直接进入"真实内容"阶段 |
| `exit status 22` | ✅ 用户态跑通，但 open 失败 | 查 HFS 用户态读路径 |
| 仍是 SIGBUS | 死因不是 SP 未对齐 | 测 **C2**（类别数字 50/52/54）+ **T**（`a_esr`） |

### K2（最小对齐探针：`getpid` + `exit(1)`）

A2 的极简对照，用于分辨"是 `__DATA`/`open` 特有问题还是通用用户态问题"：

| 输出 | 结论 |
|---|---|
| `… (signal 0, exit status 1)` | ✅ 最小用户态跑通（exec + syscall + exit 全链路 OK） |
| `exit status 6` 或其他 | 探针自定义的其它分支（看 `init-exit7.c`） |
| SIGBUS | 连最小探针都跑不起来 → 用 C2/T 深入 |

### P（UFS 只读体检）

| 字段 | 含义 |
|---|---|
| `powered=1/0` | UFS 电源域是否已开（0 = 需要 RPMh，驱动必然失败） |
| `busclocks=1/0` | 总线时钟是否可用（0 = 先测 W 使能时钟） |
| `cap & 0x01000000` | 是否支持 64 位 DMA（0 = 必须低 4 GiB，用 L） |
| `hcs/hce` | 控制器当前状态 |

### U / L（读窗口到 RAM 并注册 md，报告模式）

| 输出 | 结论 |
|---|---|
| `fuxi-ufs-read: ok dev=0x.. bytes=..` | ✅ 读通 + sha256 通过 + md 注册成功 → 测 R |
| `failed (-1)` | host bring-up 失败（看驱动日志 `why`；32 位 DMA 用 L） |
| `failed (-3/-4)` | GPT/清单解析失败 → 确认 userdata 已按格式刷入 |
| `failed (-7)` | 读失败（SMMU/DMA/超时） |
| `failed (-8)` | sha256 不符 → 刷写不一致 |
| `driver not linked` | 用错镜像了（该镜像是 `NATIVE_UFS=n`） |

### R（读窗口后静默切根）

| 输出 | 结论 |
|---|---|
| 与基线相同的 `initproc failed to start …` | ✅ 根确实来自我们注册的 md（M2 链路打通） |
| 其他 panic | 按 `decode-panic.py` 解析 |

### C2 / T（SIGBUS 类别与细节）

C2 是七段数码管数字：

| 数字 | 含义 |
|---|---|
| 50 | 进入对齐故障路径（若随后 SIGBUS 会再打 52） |
| 52 | 对齐故障无法模拟 → SIGBUS |
| 54 | catch-all → SIGBUS（同步外部中止：映射属性问题） |

T 的 panic 文本用工具解析：

```powershell
wsl -d Ubuntu-24.04 -u root -- python3 /root/UniXNU/devices/fuxi/scripts/decode-panic.py decode --text "把 panic 那行粘进来"
```

## 4. 出现问题的恢复

- 任何镜像都只通过 `fastboot boot` 运行，**不写 boot 分区**
- 若刷过 userdata（M2 测试）：`fastboot erase userdata` 后正常启动 Android 即可恢复
- 设备卡住：长按电源强制重启

## 5. 重要背景（避免重复踩坑）

1. **A–C2 这批镜像的探针入口没有对齐 sp**（`sub sp, sp, #0x30; stp …`）。若 SP 未对齐假设成立，
   它们的 SIGBUS 可能来自探针自身，与内核/HFS 无关。**只有 A2 带对齐入口。**
2. `namespace 2 subcode 0xa` = **SIGNAL / SIGBUS(10)**，不是 FairPlay/EXEC 解密。
3. ADT **不需要**给 `ufshc` 补 `reg`：驱动用硬编码基址，钩子传的是 `IOPlatformExpertDevice`。
4. `mdevadd(..., phys=0)` 的 base 是**内核虚拟地址**（`memdev.c` 里 `fvaddr = (mdBase<<12)+off` 后直接 `bcopy`）——
   我们的用量正确，且缓冲区地址/长度必须 4096 对齐（代码里已有断言）。
5. HFS 源码是**外部编译 + force_load**，改 `bsd/sweet_hfs` 后必须重建归档（`local-build.sh` 已自动做）。
