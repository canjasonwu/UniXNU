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
| **K** | `artifacts-HFS-EXIT1/boot.img` | `56190f40199905ef8c51ed7a6dea455ac8165ae6f7109a33df2229d92e4e64b7` | HFS，`getpid+exit(1)` 探针 | **终审**：用户态是否真的执行过 |
| **M** | `artifacts-HFS-OPEN/boot.img` | `2c2e693036d7422596527da1fbe1189fb2e8ea4f9dd320f759966034da9931b2` | HFS，只调 `open("/hello.txt")`，退出码 5=成功 / 6=失败 | 若 K 显示用户态能跑，用 M 定位 HFS 用户态读路径 |
| **N** | `artifacts-HFS-DATA/boot.img` | `3765a90605816ecccba54946485448d40ac19226fa0028e3c803b38ae5a01a35` | HFS，**带 `__DATA` 段**的探针（写全局变量 + open） | 覆盖"有可写数据段"的布局，更接近真实二进制 |
| **S** | `artifacts-SIZE256/boot.img` | `416e16dd119dd10ff3d900e5e63d380f89d044bac5ca37c67310af900ad4842a` | 421 MB boot.img（256 MiB ramdisk + 400 MiB payload） | **只测容量**：ABL 是否能接受这么大的镜像 |

### 镜像 N 的判读

| 屏幕 | 结论 |
|---|---|
| `… exit status 21` | ✅ 写 `__DATA` 成功 + 打开 `/hello.txt` 成功 |
| `… exit status 22` | `__DATA` 正常，但 open 失败 |
| `… exit status 23` | **`__DATA` 读写失败**（数据段权限/映射问题） |
| 故障探针文字 | 按文字定位（数据段问题常表现为写入保护故障） |

### 镜像 S 的用途（不含用户态实验）

只回答一个问题：**421 MB 的 `fastboot boot` 镜像能否被接受**。

| 现象 | 结论 |
|---|---|
| `Sending 'boot.img' (411404 KB) OKAY` | 大镜像可行 → 真实 root 有希望直接放进 payload |
| 失败/超时 | 真实 root 必须走 userdata 路线（见 `ROUTE-TO-REAL-LAUNCHD.md`） |

### 镜像 M 的判读

| 屏幕 | 结论 |
|---|---|
| `… (signal 0, exit status 5)` | ✅ 用户态不仅执行，还能**在 HFS 根上打开文件** |
| `… (signal 0, exit status 6)` | 执行正常，但 HFS 打开失败（查 hfs_vnop_open 路径） |
| `… (signal 0, exit status 3)` | 执行了，但 getpid() != 1 |
| 故障探针文字 | 用户态在 open 前后发生故障（按文字定位） |

### 镜像 K 的判读（最重要的一条）

| 屏幕 | 结论 |
|---|---|
| `initproc failed to start -- no exit reason available -- (signal 0, exit status 1)` | ✅ **用户态确实执行了**：探针跑完 `getpid` 并 `exit(1)`。那么 SIGBUS 是**后面**的事（或根本是别的进程） |
| `fuxi-iabort-el0` / `fuxi-dabort-el0` / `fuxi-vmfault` / `fuxi-sigbus` | ❗ 用户态没跑起来，故障探针先触发——按对应文字定位 |
| 只有数字 | init 死在更早的位置（看阶段号） |

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

## 装入真实 launchd（为下一步准备）

填充脚本现在支持直接放入任意二进制：

```bash
cd /root/hfs-populate
python3 populate-hfs.py --launchd /path/to/launchd --dry-run   # 先干跑验证，不启 QEMU
python3 populate-hfs.py --launchd /path/to/launchd             # 真正填充（约 15-20 分钟）
```

干跑会打印装入的字节数与 ISO 大小，不会启动 guest；正常填充完成后 guest 会打印
`HASH CHECK` 里的 sha256，可与源文件对比。

## 建议顺序

1. **K**：一句话判定"用户态是否执行过"（最省轮次的定性实验）
2. **J**（单镜像全诊断）：拿到故障判决/故障类型
3. 若 J 显示入口/取指问题 → **H** 与 **I**（4K 对齐对比）
4. **F/G**（无栈/写栈对比）
5. **A**（ESR 细节）、**D/E**（加载参数对比）、**B**（对照）
