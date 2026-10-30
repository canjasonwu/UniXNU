# 待测镜像批次（2026-10-30，离线批次）

> 目标：定位 HFS 根上 PID 1 被 **SIGBUS**（保护故障）杀死的来源，并修到真实 launchd 能跑。
> 关键纠正：`OS_REASON_EXEC` = 9；**namespace 2 = `OS_REASON_SIGNAL`**。屏上
> `exit reason namespace 2 subcode 0xa` 的真实含义是 **signal 10 = SIGBUS**，不是"解密失败"。
> `ux_exception.c:54`：`EXC_BAD_ACCESS` 的 code ≠ `KERN_INVALID_ADDRESS` 时映射为 SIGBUS，
> 即 **SIGBUS = 保护故障**（页存在、权限不允许），不是地址不存在。

## 镜像清单

| 编号 | 文件 | sha256 | 根文件系统 / 探针 | 用途 |
|---|---|---|---|---|
| A | `artifacts-SIGBUS/boot.img` | `3ff5efdff5534600e3cb14dcfd126753f512038e5632a2f6de012b6f46122cde` | HFS，探针：getpid→open("/hello.txt")→write→空转 | 抓 EL0 故障 / SIGBUS 来源 |
| B | `artifacts-SIGBUS-CONTROL/boot.img` | `e305ac6622458b7bdd3e2f0c9a372fb6d4ac597e0786ef186932a98188489eb5` | mockfs，普通 init（退出码 1） | 对照：panic 屏 + 用户态 exec |
| C | `artifacts-HFS-NAKED/boot.img` | `9c166363127ce1bcbd59ccd87f63205a760e6e15fc44183c0ac7f573c908e963` | HFS，**无栈**裸汇编探针 | 栈假设实验 |
| D | `artifacts-LOADINFO/boot.img` | `a16cfc7557338994a2baaec0020cd17d6b6ff164e664cd3bbdc6487195a06160` | HFS，无栈探针 | exec 后打印 `entry/sp/stack/size` |
| E | `artifacts-NAKED-MOCKFS/boot.img` | `d69d4be68f33e3fd40be7b80af2d96c034ad5c5e1ff5892f6db391af86e9e6bf` | mockfs，同探针 | D 的对照（只有根 FS 不同） |
| F | `artifacts-VMFAULT/boot.img` | `636ba13dbda8e58991153ef23cb5a6d4a15ac8e3a4c57bf832770133f0c64b75` | HFS，无栈探针 | **vm_fault 判决**：失败时打印返回值/地址/权限/PC/SP |
| G | `artifacts-VMFAULT-STACK/boot.img` | `98be25d6ace5d776f92b38b7dcbe8bd91f19298c2e53bc2a6734f9460c8c9249` | HFS，会写栈的探针（getpid→open→write→空转） | 同上，但会触发栈访问，验证栈假设 |
| H | `artifacts-4K-HFS/boot.img` | `5dc38a0b4ea25aae585c6bcc211f01a0e11b31f8d6d56f0b3d7444e66ac5e825` | HFS，**4K 对齐**探针 | 验证"16K 对齐二进制 vs 4K 用户页"假设 |
| I | `artifacts-4K-MOCKFS/boot.img` | `cd11c48cc8112f74b6c9c60ec036d5ea131802b73b22f3ef5beb2dc69e6e8909` | mockfs，4K 对齐探针 | H 的对照 |
| **J** | `artifacts-OMNIDIAG/boot.img` | `ad979cd32ded0e002bed21354ad718f3c855c95a0f5bf277ad325654eb335cd2` | HFS，会写栈的探针，**全部探针** | **单镜像全诊断**（推荐先测） |

### 推荐：先测 J

J 把四类信息合在一个内核里，且**加载参数只在明显异常时才 panic**，所以一次上机可以拿到：

- 若 `fuxi-load-bad: entry=… sp=… size=…` → 加载结果本身不可用（入口/栈参数错误）
- 若 `fuxi-iabort-el0` → 取指落在没权限/没映射的页（入口地址问题最强信号）
- 若 `fuxi-dabort-el0` → 数据访问故障（附 far/pc）
- 若 `fuxi-vmfault: ret=… addr=… prot=…` → 故障判决（ret=2 即保护故障）
- 若 `fuxi-sigbus: caller=…` → 内核主动投 SIGBUS
- 若出现数字 / 无 panic → init 正常运行

## 探针一览（内核侧，先发生者先 panic）

| 探针 | 触发 | 屏幕文字 |
|---|---|---|
| EL0 取指故障 | class 0x20 | `fuxi-iabort-el0: esr=… far=… pc=…` |
| EL0 数据故障 | class 0x24 | `fuxi-dabort-el0: esr=… far=… pc=…` |
| SIGBUS 投递 | `psignal(p, SIGBUS)` | `fuxi-sigbus: caller=… pid=…` |
| vm_fault 失败 | 用户故障无法解析 | `fuxi-vmfault: ret=… addr=… prot=… pc=… sp=…` |

## 判读

### `fuxi-vmfault`（镜像 F/G）——最直接

- `ret=2` = `KERN_PROTECTION_FAILURE` → 保护故障（SIGBUS 的直接来源）
- `ret=3` 附近 = `KERN_INVALID_ADDRESS` → 地址未映射
- `addr` = 出错地址；`prot` = 访问类型（1=读 2=写 4=执行，可组合）
- `pc`/`sp` = 用户态位置与栈指针

把这一行发我，我用 payload 内核符号表 + 探针字节定位到具体指令。

### `fuxi-load`（镜像 D/E）

对比 D 与 E 的 `sp/stack/size`：

- 完全一致 → 加载/栈设置相同，差异在映射路径
- 不同 → 直接是 `exec_setup_stack` 的 bug

### 其它

- `fuxi-*-abort-el0`：ESR 解码（FSC 13–15=权限故障，4–7=未映射，16=总线错误）
- `fuxi-sigbus: caller=…`：内核主动发 SIGBUS，caller 地址可反查函数
- 无 panic 只有数字：init 正常空转/退出（好事）

## 一键测试

```powershell
pwsh -NoProfile -File "C:\Users\Jason Wu\dsh\test-batch.ps1" -Image F
```

脚本只做 `fastboot boot`，不写存储。设备不在 fastboot 时会提示按电源。

## 辅助脚本

```powershell
wsl -d Ubuntu-24.04 -u root -- python3 /root/decode-el0.py "esr=96000045 far=1000000f0 pc=1000004c8"
```

## 建议顺序

1. **J**（单镜像全诊断）：一次拿到判决/故障类型
2. 若 J 显示入口/取指问题 → **H** 与 **I**（4K 对齐对比）
3. **F/G**（无栈/写栈对比）
4. **A**（ESR 细节）、**D/E**（加载参数对比）、**B**（对照）
