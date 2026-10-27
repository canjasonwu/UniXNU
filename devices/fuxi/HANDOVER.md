# fuxi 交接文档（用户离开期间的自动推进结果）

> 更新时间：2026-10-27
> 约束不变：只用 `fastboot boot`，不写任何分区；本地构建优先。
> 详细过程见 `kernel-autopilot.md`；路线评估见 `fuxi-routes.md`；实验清单见
> `devices/fuxi/NEXT-DEVICE-EXPERIMENTS.md`。

---

## 1. 现在的状态（一句话）

内核在真机上已经**越过 AMFI、挂载根、执行 PID 1**；离线侧已经建成 **QEMU 测试床 + 内核控制台读取**，
可以不用手机观察内核启动；下一步卡点是「用户态为什么静默挂起」与「真实根从哪来」。

## 2. 真机已达成的里程碑（都有截图/日志证据）

| 里程碑 | 证据 |
|---|---|
| UEFI 链、内核初始化、ADT、SMP | 多轮上机 |
| AMFI `No PMGR?` | 发布并注册 `ApplePMGRNub`（探针 status 7） |
| AMFI 接口 + trustcache | panic 前进到挂载根 |
| 挂载 mockfs 根 | `Process 1 exec of /sbin/launchd failed, errno 8` |
| 执行自建 PID 1 | `initproc failed to start … (signal 0, exit status 0)` |
| 未签名 exec 放行 | `EXEC_EXIT_REASON_SECURITY_POLICY` 不再杀 init |

## 3. 离线能力（本轮最重要的产出）

| 能力 | 工具 |
|---|---|
| 在 QEMU 跑整条链（UniXNU→DarwinBoot→XNU） | `devices/fuxi/scripts/qemu-boot.sh` |
| 读内核控制台（msgbuf，无需串口/手机） | `devices/fuxi/scripts/qemu-console.sh` + `show-msgbuf.py` |
| 在 halt 状态读寄存器/采样 PC/符号化 | gdb + 内核符号表 |
| 物理内存读取（QMP） | `xp` via human-monitor-command（已验证可用） |
| panic 日志区域 | ADT `/pram` + `/chosen/embedded-panic-log-size`（已修复，QEMU 验证 `EXT_PANICLOG: Initializing list`） |

## 4. 已修复的真实缺陷（不是猜测，均有证据）

1. **panic 日志地址**：`PLATFORM_PANIC_LOG_PADDR` 是树莓派地址；改为从设备树读，
   QEMU 验证 `gPanicBase` 错误消失。
2. **补丁循环幂等性**：新增补丁导致循环重跑并在 `bsd_init.c` 误判失败 → 改为按内容签名只应用一次。
3. **构建环境**：availability 头镜像、TrustCache/DriverKit 头恢复、SDK include 路径，均已固化进
   `local-build.sh` / `build-arm-driver.py` / `restore-headers.py`。
4. **UFS 只读探针**：移植参考驱动的 `inspect()`，默认关闭、boot arg 开启、全程只读
   （镜像 `artifacts-ZD`）。

## 5. 待你决策的一件事（影响后续路线）

| 选项 | 内容 | 代价 |
|---|---|---|
| **A** | 允许一次性写 `userdata` 放 HFS+ 根（复用 `hanzi123-bilp` iosroot / `bangprovn` 方案） | 违背当前「不写分区」安全线，但能最快到 SpringBoard |
| **B** | 坚持不写分区：NativeUFS 只读挂载 或 裁剪根镜像放进 boot 镜像 | 工作量大，完全在安全线内 |
| **C** | 直接采用 `bangprovn/ios-fuxi` 成品（已进主界面），我们做增量 | 等于换栈，放弃现有 UniXNU 积累 |

**我的建议**：先做实验 0/4（无需决策，信息量最大），再按结果选 A 或 B；C 只在你想尽快看到 iOS 界面时选。

## 6. 下一次上机（照做即可）

| 顺序 | 镜像 | 期望 | 判据 |
|---|---|---|---|
| 1 | `artifacts-ZB/boot.img`（`13742060…`） | `exit status 1 … timer-irqs=N` | N=0 → 定时器从未触发；N>0 → 定时器正常 |
| 2 | `artifacts-ZD/boot.img`（`9b09de71…`） | `fuxi-ufs-probe: gdsc=… powered=… hc_read=…` | `hc_read=1` → UFS 可访问 |
| 对照 | `artifacts-ZC/boot.img`（`4e3394a1…`） | 无探针输出 | 排除参数干扰 |

每次只需：`fastboot boot <镜像>` → 拍照 → 长按电源恢复。**全程不写分区。**

## 7. 已知未解问题

1. **用户态静默挂起**：带 `open()/write()/reboot()` 的 init 不产生 panic 文本；
   最小 `write+exit` 版本正常。定时器计数实验将给出关键线索。
2. **QEMU 内核 halt**：`pmap_enter_options_internal` 同步异常 → halt，原因未定位；
   它挡住了 bsd_init 之后的离线测试（UFS 探针因此只能真机验证）。
3. **定时器 INTID**：Xyvern 实测 fuxi 为 19，我们继承 BCM2837 通用定时器配置；
   是否真的丢中断待实验 0 的 `timer-irqs` 回答。

## 8. 代码状态

全部已推送到 `canjasonwu/UniXNU` 的 `main`（最新提交见 `git log -1`），包含：
补丁 `zzz-timer-count.patch`、`zzz-unsigned-exec.patch`、`zzz-pmgr-publish-at-kext-start.patch`，
脚本 `qemu-boot.sh`、`qemu-console.sh`、`show-msgbuf.py`、`build-nativeufs.py`、`restore-headers.py`，
文档 `fuxi-routes.md`、`NATIVEUFS-PLAN.md`、`BOARD-AUDIT.md`、`NEXT-DEVICE-EXPERIMENTS.md`。
