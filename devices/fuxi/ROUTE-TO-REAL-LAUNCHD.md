# 到"真实 launchd 运行"还差什么（离线评估）

> 目的：把"用户态能跑"和"真实 launchd 能跑"之间的差距量化，避免在 HFS-in-boot.img 这条路上
> 撞到容量/依赖墙才发现。

## 1. 两个不同的里程碑

| 里程碑 | 判据 | 需要的组件 | 现状 |
|---|---|---|---|
| **M1 用户态可用** | 静态 init 能执行、能读写文件、能正常退出 | 内核 exec + HFS 读路径 | 探针已就位（K/M/N 系列），卡在 SIGBUS |
| **M2 真实 launchd** | `/sbin/launchd` 真正跑起来并进入事件循环 | 新增：dyld、libSystem、dyld shared cache、必要的 `/System`、`/private/var` 骨架 | 未开始 |

## 2. M2 的硬性依赖（为什么不是"放个二进制"就行）

1. **动态链接**：iOS 的 launchd 依赖 `LC_LOAD_DYLINKER`（dyld4）与 libSystem；内核 exec 会把控制权交给
   dyld，dyld 再从 **dyld shared cache** 里映射 libSystem 等库。
2. **dyld shared cache 体积**：iOS 17 arm64e 的 `dyld_shared_cache_arm64e` 通常在 **1.2–1.5 GB** 量级
   （含多个缓存文件时更大）。
3. **路径骨架**：至少需要 `/System/Library/Caches/com.apple.dyld/`、`/private/preboot`（或等价物）、
   `/usr/lib/`、`/private/var/` 的存在与可写性；launchd 启动早期会打开这些位置。
4. **签名/策略**：launchd 是平台二进制，AMFI/代码签名路径需要放行（我们已有 unsigned-exec 补丁，
   但真实二进制通常**带签名**，需要 trust cache 或等价机制）。

## 3. 两条可行路线的容量对比

| 路线 | 根镜像存放位置 | 容量可行性 | 风险 |
|---|---|---|---|
| **HFS 卷嵌在 boot.img 的 payload 里**（当前） | RAM payload（FAT 分区内） | 现在 162 MB 镜像已验证可加载；要放 1.2–1.5 GB 根，需要 ~1.5 GB 的 boot.img | ABL 对 `fastboot boot` 镜像大小有未知上限；加载时间显著变长；RAM 里再解一份会更紧张 |
| **userdata 上的根镜像 + extent/窗口读**（upstream 路线） | 用户数据分区（`fastboot erase userdata` 可复位） | 容量几乎不受限（分区有多大用多大） | 需要内核侧存储驱动：`NativeUfs`（`CONFIG_XNU_NATIVE_UFS` 在 pass2 静默失败，未修）或 bangprovn 的 `sweet_ufs.c` extent 映射 |

**结论（离线判断）**：M2 实际需要 **userdata 路线**；HFS-in-boot.img 适合把 M1 打穿并验证用户态/文件系统语义。

## 4. 因此的推荐顺序

1. **先打穿 M1**（当前工作）：K → M/N 系列定位 SIGBUS，让"静态 init 能读写 HFS 文件并正常退出"成立。
2. **再做 M1.5**：做一个更接近真实的最小环境（多段二进制 + `/usr/lib` 骨架 + argv/env 处理），
   仍然静态链接，验证加载器/文件系统在更复杂布局下的正确性。
3. **最后转 M2**：
   a. 修 `CONFIG_XNU_NATIVE_UFS` 的 pass2 静默失败（先拿到第一处错误）；
   b. 用 `NativeUfs/RootLayout` 的窗口读把 userdata 上的大根镜像映射成根设备；
   c. 放入真实 root 树（launchd + dyld + shared cache + 系统骨架），处理签名/trust cache；
   d. 若 userdata 路线受阻，再评估"超大 boot.img"是否被 ABL 接受（先做一次 256 MB / 512 MB 的
      `fastboot boot` 加载测试即可低成本试探）。

## 5. 现在就能离线做、但你回来才需要上机的检查

| 检查 | 方式 | 价值 |
|---|---|---|
| boot.img 大小上限 | 镜像 S 已经是 421 MB 的 `fastboot boot` 测试件 | 决定是否需要 userdata 路线 |
| 真实 root 树来源 | 需要你提供 IPSW 或预解出的 iOS 根目录 | M2 的前置条件 |

## 6. `CONFIG_XNU_NATIVE_UFS=y` 已能构建（2026-10-31 解决）

### 6.1 第一层：归档路径不对 → `set -e` 下静默退出

内核脚本按 `$ROOT/build/native-ufs/libNativeUfs.a`（`$ROOT` = 构建树）查找归档，而我们的
`build-nativeufs.py` 原本只写到**仓库根**的 `build/native-ufs/`。pass2 里
`XNU_ALLOW_MISSING_ARCHIVES=0`，`test -f` 失败 → `set -e` 静默退出（日志无任何输出）。

**已修**：`build-nativeufs.py` 现在同时写两份（仓库根 + 树内路径）。

### 6.2 第二层：链接期缺符号（已用桩补齐）

| 缺失符号 | 处理 |
|---|---|
| `IOBlockStorageDevice` 的 gMetaClass / 构造析构 / vtable / `getProperty` / `requestIdle` / `setProperties` / `doSetPriority` / `doGetProvisionStatus` / `doUnmap` / `init` | 新增 `devices/fuxi/IOStorageStubs.cpp`：按 Itanium ABI 的 mangled 名提供零值桩（只满足链接，运行时从不启动该 IOService） |
| `_boot_log_errors` / `_boot_log_first_lba` / `_boot_log_rounds` / `_boot_log_state` / `_boot_log_tick` | 同一文件里提供零值全局（`CONFIG_XNU_BOOT_PROGRESS=n` 时使用；启用 BootProgress 时必须删掉，否则符号冲突） |

### 6.3 验证结果（实测）

```
CONFIG_XNU_NATIVE_UFS=y 构建：exit 0
kernel.macho 51,560,448 字节（比默认内核 51,482,624 多约 77 KB）
nm 统计 NativeUfs 符号：50 个（含 NativeUfsBlock 的 vtable/start/free/doAsyncReadWrite…）
镜像存档：artifacts-NATIVEUFS/boot.img = ebbc3893474fdee57ef2bdfdc1f4ea6f325e7b8e098b6f0b1527a8a15784b36f
```

默认配置（`=n`）重建也验证过：51,482,624 字节、NativeUfs 符号 0 个、探针自检 11/11 ok。
两套内核可并存（镜像分别存档），因此 **M2 的第一个门槛已经跨过**：

> 内核现在可以链接 sheng 的 NativeUfs 读路径，下一步是在启动早期调用它，把 userdata 上的根镜像读进内存。

### 6.4 userdata 根镜像格式：已离线实现并可自检（2026-10-31）

内核侧读取逻辑来自 `drivers/NativeUfs/RootLayout.h`（`findRootLayout`）。格式（userdata 分区**偏移 0** 的 4096 字节块）：

| 偏移 | 内容 |
|---|---|
| 0 | 魔数 `SHNGROOT`（`0x474e4853 0x544f4f52`） |
| 8 | version = 1 |
| 12 | 窗口数（1–4） |
| 16 | `imageBytes`（u64，整个已刷镜像大小） |
| 24 | 窗口表，每条 128 字节：`name[32]`（可打印 ASCII，NUL 结尾，唯一）、`offset`(u64, 4K 对齐, ≥4096)、`size`(u64, **512 对齐**, 非 0)、`+48`=512、`+52`=1、`+56`=sha256[32] |
| 4064 | 前 4064 字节的 sha256 |

**关键约束**（都已在工具里实现并被自检覆盖）：

- `size % 512 == 0`，sha256 覆盖**落盘的那 `size` 字节**（所以载荷按 512 零填充后再算哈希）
- `offset` 4K 对齐、窗口之间不得重叠（按 4K 向上取整后判断）
- GPT 不在这个文件里：设备 LUN0 的 GPT 由内核读取来找 `userdata` 分区，镜像只是该分区的内容
- `RootPublication.h` 的 `ufsBuildRootPlan` 要求窗口名里**同时存在 `SystemOS` 与 `OS`**（2–4 个窗口），

  发布顺序为 `SystemOS` → 其它 → `OS`

**工具**（新增，可离线自测）：

```bash
python3 devices/fuxi/scripts/root-manifest.py selftest
python3 devices/fuxi/scripts/root-manifest.py build --out /tmp/userdata-root.img \
    --window OS=/root/fuxi-root/root.hfs
python3 devices/fuxi/scripts/root-manifest.py verify --image /tmp/userdata-root.img
```

自检内容：正常构建+校验、**篡改载荷被拒**（sha256 不符）、**篡改清单被拒**（清单哈希不符）。
实测：用 64 MiB 的 HFS 卷作为 `OS` 窗口生成 67,112,960 字节镜像并通过校验。

### 6.5 内核侧启动钩子：已实现（2026-10-31）

驱动本身是按 kext personality（`IOProviderClass`）匹配的 IOService，而我们把驱动直接链进内核，
没有 personality 注册表。最小改动是在驱动文件里追加一个 C 入口（复用驱动全部代码）：

```c
extern "C" int native_ufs_probe_start(void);   /* 由 devices/fuxi/scripts/nativeufs-start-hook.py 追加 */
```

它的动作：在 `gIOServicePlane` 里递归找 **`IOPlatformExpertDevice`** → `new NativeUfs` → `init` →
`attach(provider)` → `start(provider)`；返回 `1` 启动成功、`0` 驱动拒绝（自身会打日志）、负数表示阶段失败。

内核侧（`ShengPlatformPower.cpp` 的晚期探针）**不使用弱符号**（Mach-O 内核链接不允许未定义弱符号），
而是让驱动归档自己注册函数指针：

- `ShengPlatformPower.cpp` 定义 `void (*fuxi_ufs_start_hook)(void) = NULL;`
- `NativeUfsStartHook.cpp`（只编进 NativeUfs 归档）用 **static constructor** 把真实钩子装进这个指针
- 因此 `CONFIG_XNU_NATIVE_UFS=n` 时指针恒为 NULL，内核正常链接

调用条件：boot arg `fuxi-ufs-start=1`；驱动行为由 `native-ufs-probe` 决定，`=10` 是它自带的**只读检查**
（LUN0 GPT → `userdata` → 首块清单 → panic 打印 `userdata first/last`、块 0 魔数、`manifest status`、`windows`）。

**镜像 V2**（最新，已构建）：
`artifacts-NATIVEUFS-START/boot.img = ed626a572459fa1d0213d44c43881301e3eaa328cc8c2c76148f1518f469af96`
（kernel 51,560,448 字节，含 NativeUfs 与注册钩子；内核参数 `native-ufs-probe=10 fuxi-ufs-start=1`）

判读：

| 屏幕 | 含义 |
|---|---|
| `fuxi-ufs-start: hook=1` + 驱动的 `NATIVE_UFS root check …` | ✅ 驱动起来了，并读到了 GPT/清单（下一步接 RAM-root） |
| `fuxi-ufs-start: hook=0` | 驱动拒绝启动（`start` 返回 false，通常 provider 类型不符） |
| `fuxi-ufs-start: hook=-1/-2` | 找不到注册表根 / 找不到 `IOPlatformExpertDevice` |
| `fuxi-ufs-start: hook=-4/-5` | `init` / `attach` 失败 |
| `fuxi-ufs-start: driver not linked` | 镜像不是 `NATIVE_UFS=y` 构建 |
| 驱动自己的 `bring-up failed: <why>` | 硬件初始化失败（why 指出阶段） |

### 6.7 UFS 时钟使能探针（镜像 W）

驱动**不会自己开电源/时钟**（`hostBringUp` 直接映射控制器就用）。它的只读 `inspect` 也只是**检查**
GDSCR/CBCR。所以单独做了只写"时钟分支使能位"的探针：

- boot arg `fuxi-ufs-clock=1` 触发；写前/写后都读回并通过 panic 打印
- 只写 **bit0（branch enable）**：AXI(0x77018)、AHB(0x77024)、AGGRE(0x770e4)、UNIPRO(0x77068)、
  TX(0x77028)、RX(0x7702c)、PHY_AUX(0x770b0)；**绝不碰** reset、RPMh/TCSR、QMP PHY
- GDSCR（0x77004）只读：`bit31=PWR_ON`。**不自己上电**（需要 RPMh），只在文字里报告 `powered=0/1`

**镜像 W**：`artifacts-UFS-CLOCK/boot.img = 1339b20b8ae838ed6b4c4009469a818e8721c35b73ca9e45723c769e394ca3a5`
（默认 `NATIVE_UFS=n` 内核，无驱动；内核参数 `fuxi-ufs-clock=1`）

判读：

| 屏幕 | 含义 |
|---|---|
| `fuxi-ufs-clock: powered=1 …` | ✅ UFS 电源域已开（ABL 遗留），时钟分支值也已打印 |
| `powered=0` | 电源域没开 → 驱动 bring-up 必然失败，需要先解决 RPMh 上电 |
| `after` 与 `before` 相比对应 bit0 由 0→1 | ✅ 时钟使能写入生效 |
| GCC map failed | 物理窗口映射失败（地址/内存描述符问题） |

下一步（按 powered 的真值分支）：
- `powered=1`：把时钟使能并入镜像 V 的启动路径（`fuxi_ufs_start` 之前调用），再测驱动 bring-up；
- `powered=0`：先解决 RPMh 上电（或确认 ABL 是否会保持 UFS 电源）。

### 6.8 合并镜像 X：时钟使能 + 驱动启动（一次上机回答全部阶段）

镜像 W 单独测时钟时会 panic，看不到驱动阶段。为了减少上机次数，把时钟使能改成**可复用函数**
（`fuxi_ufs_clock_enable()`，不 panic），由 `fuxi_ufs_start()` 在启动驱动前调用，最后统一报告：

- `fuxi-ufs-clock=1` → 先使能缺失的时钟分支（记录 powered / 写了几条）
- `fuxi-ufs-start=1` → 调用驱动启动钩子
- `native-ufs-probe=10` → 驱动自己的只读检查（GPT → userdata → 清单）

最终 panic 形如：

```
fuxi-ufs-start: hook=<结果> clock_powered=<0|1> clock_written=<N>
```

若驱动能走到自己的检查，还会出现它自己的 `NATIVE_UFS root check …` panic（那是更好的结果）。

**镜像 X**：`artifacts-UFS-X/boot.img = 07726d8a5a671c34498b6729d9ff7f4ea919cfd3b95995dd753db7408fbedfa8`
（`NATIVE_UFS=y` 内核，参数 `fuxi-ufs-clock=1 fuxi-ufs-start=1 native-ufs-probe=10`）

判读：

| 屏幕 | 含义 | 下一步 |
|---|---|---|
| `NATIVE_UFS root check …` | ✅ 时钟+电源+控制器都 OK，驱动读到了 GPT/清单 | 接 RAM-root |
| `hook=0` + `clock_powered=1` | 控制器可用但驱动 `start` 拒绝 | 检查 provider 类型/驱动日志 |
| `hook=0` + `clock_powered=0` | **电源域未开** | 必须先解决 RPMh 上电 |
| `hook=-2` | 找不到 `IOPlatformExpertDevice` | 改 provider 搜索路径 |
| `clock enable failed (GCC map)` | GCC 窗口映射失败 | 检查物理内存描述符 |

### 6.10 RAM-root 接线原型：自己注册 md 设备并切换 rootdev（2026-10-31）

关键 API（Apple 自己的 `IOFindBSDRoot` 就用它把 ADT RAMDisk 变成 `/dev/md0`）：

```c
dev_t mdevadd(int devid, uint64_t base_sectors, unsigned int size_sectors, int phys);
dev_t mdevlookup(int devid);
```

`bsd/dev/memdev.c` 已无条件编译进内核（`mdevadd`/`mdevlookup` 都在内核符号里）。

接法（已实现原型）：

1. 晚期探针（`sheng_pmgr_late_probe`，在 `bsd_autoconf` 内、`setconf()` **之前**）：
   - `fuxi_mdev_test()`：读 `/chosen/memory-map` 的 `RAMDisk` 属性（virt, size）→
     `mdevadd(-1, base>>12, size>>12, 0)` 注册成新的 md 设备并记住设备号
2. `setconf()`（`bsd_init.c`，`IOFindBSDRoot` 之后）新增一行：
   `rootdev = fuxi_rootdev_override(rootdev);`
   → 若已注册 md 设备则**静默**返回它，`vfs_mountroot` 就会从这块 RAM 设备挂根

两种模式（boot arg `fuxi-mdev-test`）：

| 值 | 行为 |
|---|---|
| `1` | 注册后立即 panic，屏幕显示 `fuxi-mdev-test: registered md dev 0x.. base=.. bytes=..` |
| `2` | 静默注册并切换 rootdev，让启动继续 → 用来验证"从自己注册的 md 挂根" |

**镜像**：

- `artifacts-MDEV/boot.img = d98141a704445c6d1612af3aeb2a45a707fd8ecbafba834b6b305fb097230bee`（`=2` 挂载测试）
- `artifacts-MDEV-REPORT/boot.img = 4d8f7163c63092183e84095277b7686578ec327cfde6ed4b7a485c487c507cc2`（`=1` 报告）

判读：

| 屏幕（Y） | 含义 |
|---|---|
| 与基线相同（`initproc failed to start …`） | ✅ 根确实从我们注册的 md 设备挂载并跑到了 init |
| `fuxi-mdev-test: /chosen/memory-map missing` 或 `RAMDisk property missing` | ADT 里没有该节点，需换数据来源 |
| `mdevadd failed (-1)` | 设备槽/重叠等问题（重叠会 panic 并打印区间） |
| 屏幕（Y1） | 显示注册到的 `md dev/base/bytes`，确认注册成功 |

> 这一步**不碰 UFS 硬件**：数据仍来自 payload 的 RAMDisk，只是把"注册 md + 切根"这条 M2 必需的机制先用现有内容验证通。

### 6.11 读窗口到内存的完整链路（2026-10-31 已实现、待真机）

驱动自身的"发布根窗口"走 IOStorageFamily（我们只有桩，运行时不可用），所以补了一个**只读读取钩子**，
写进驱动自己的编译单元（复用它的 `hostBringUp` / `findRootLayout` / `hostRead4k` / SHA256）：

```c
extern "C" int native_ufs_read_window(const char *name, void *dst,
                                      unsigned long long maxBytes,
                                      unsigned long long *outBytes);
/* 0 成功；-1 host bring-up；-2 内存；-3 GPT/清单解析；-4 清单被拒；
   -5 窗口名不存在；-6 大小/对齐；-7 读失败；-8 sha256 不符 */
```

内核侧（`ShengPlatformPower.cpp`）新增 `fuxi_ufs_read()`，由 boot arg 控制：

| boot arg | 行为 |
|---|---|
| `fuxi-ufs-read=1` | 分配 wired 缓冲（`fuxi-ufs-read-mb`，默认 96 MiB）→ 读 `OS` 窗口 → `mdevadd` 注册 → **panic 报告结果** |
| `fuxi-ufs-read=2` | 同上但**静默**，交给 `setconf()` 的 `rootdev` 覆盖去挂载 |

读取钩子通过**构造函数注册**（`fuxi_ufs_read_window_hook` 指针，platform-power 拥有并初始化为 NULL；
`NativeUfsStartHook.cpp` 在驱动链接时装入）——所以 `CONFIG_XNU_NATIVE_UFS=n` 的内核照常链接。

**镜像 UFSREAD**：`artifacts-UFSREAD/boot.img = ca3f313b2b9e08ea228a70047fb04eefaaa878a263d741ddd6406798c100bd29`
（`NATIVE_UFS=y`，参数 `fuxi-ufs-clock=1 fuxi-ufs-read=1 fuxi-ufs-read-mb=96`）

判读：

| 屏幕 | 含义 | 下一步 |
|---|---|---|
| `fuxi-ufs-read: ok dev=0x.. bytes=..` | ✅ UFS 读通、窗口校验通过、md 注册成功 | 改成 `fuxi-ufs-read=2` 测"HFS 从 md 挂根" |
| `window read failed (-1)` | host bring-up 失败（电源/时钟/DMA/SMMU） | 看驱动日志里的 `why`；必要时解决 RPMh |
| `window read failed (-3/-4)` | GPT 或清单解析失败 | 检查 userdata 是否已按 6.4 刷入镜像 |
| `window read failed (-8)` | sha256 不符 | 写入/读取不一致，检查刷写方式 |
| `driver not linked` | 镜像不是 `NATIVE_UFS=y` 构建 | 用 UFSREAD 镜像 |

### 6.12 端到端运行手册（M2 真机流程）

工具与镜像都已就绪，按下面顺序执行即可。**只在 userdata 上做改动，`fastboot erase userdata` 可完全恢复。**

1. **准备 HFS 根卷**（已有则跳过；约 15–20 分钟）
   ```bash
   cd /root/hfs-populate && python3 populate-hfs.py     # 会生成 root.hfs
   cp root.hfs /root/fuxi-root/root.hfs
   ```
2. **生成 userdata 根镜像**（按分区大小填充；分区大小可用 `fastboot getvar userdata-size` 查）
   ```bash
   bash devices/fuxi/scripts/build-userdata-root.sh \
       --hfs /root/fuxi-root/root.hfs \
       --out /tmp/userdata-root.img \
       --pad-to <userdata-size-bytes>
   ```
   输出会打印镜像 sha256、窗口布局与**刷写/恢复命令**。
3. **刷入 userdata**（数据会丢，可逆）
   ```powershell
   fastboot flash userdata /tmp/userdata-root.img
   ```
4. **先跑只读报告模式**：`-Image U`（`fuxi-ufs-read=1`）
   - 期望：`fuxi-ufs-read: ok dev=0x.. bytes=..`
   - 失败：按 6.11 的返回码定位
5. **再跑挂根模式**：`-Image V2M`（`fuxi-ufs-read=2`）
   - 期望：启动继续 → HFS 从注册的 md 挂载 → 出现与基线相同的 `initproc failed to start …`
   - 这一步成功就代表 **"根来自 userdata 镜像"** 的完整链路打通
6. **恢复 Android 数据分区**
   ```powershell
   fastboot erase userdata
   ```
   然后正常启动 Android，让它重建数据分区。

注意事项：

- 步骤 4/5 的内核必须含 NativeUfs（`CONFIG_XNU_NATIVE_UFS=y`），默认镜像不含。
- ADT **不需要**给 `ufshc` 补 `reg`：驱动用硬编码基址，且启动钩子传入的是 `IOPlatformExpertDevice`，
  不是 `ufshc` 节点。这一点已从源码确认，避免做无谓的 ADT 改动。
- 若步骤 4 返回 `-1`（host bring-up 失败），先看驱动日志里的 `why`：最常见是电源域没开（见 6.7 的
  `powered` 值），其次是 DMA/SMMU 初始化。

### 6.13 为真实 root 内容准备的投放机制（2026-10-31）

填充脚本（`/root/hfs-populate/populate-hfs.py`）现在有两项能力，专为接真实 launchd/dyld 准备：

1. **rootfs 骨架**：自动创建
   `sbin`、`usr/lib`、`usr/libexec`、`usr/bin`、`etc`、`private/var`、`private/preboot`、
   `private/etc`、`System/Library/Caches/com.apple.dyld`、`System/Library/LaunchDaemons`
2. **任意文件投放**（可重复）：
   ```bash
   python3 populate-hfs.py --file /path/to/dyld:/usr/lib/dyld \
       --file /path/to/dyld_shared_cache_arm64e:/System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64e
   ```
   `--file SRC:DEST`，`DEST` 相对卷根；缺失源文件会直接报错。
   仍可用 `--launchd /path/to/launchd` 覆盖 `/sbin/launchd`。

**`--dry-run` 会生成 transfer/seed ISO 并停止**（不启 QEMU），可用于快速核对骨架与投放结果：

```
dropped: /root/hfs-populate/xfer/usr/lib/rootfs-test.txt 13 bytes
dry run: transfer ISO and seed built, not starting QEMU
```

### 6.14 下一步

### 6.13 下一步

1. 若镜像 V 显示 `hook=1` 且驱动读到清单：把"分窗读入 RAM + sha256 校验"接到 RAM-root；
2. 若 hook<0：把 provider 搜索换成 `IORegistryEntry::fromPath("/device-tree", gIODTPlane)` 等备选路径；
3. 若 `bring-up failed`：按 `why` 补 ADT 属性（`ufshc` 节点目前**没有 `reg`**，可能需要补 `reg`/`interrupts`）。
