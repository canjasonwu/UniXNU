# IPSW 解包报告与真实 launchd 的架构约束（2026-10-31）

输入：`C:\Users\Jason Wu\Downloads\iPhone12,1_17.0_21A329_Restore.ipsw`
（iPhone 11 / iOS 17.0 / 21A329，7,293,542,641 字节）

## 1. 已取出的真实内容

| 文件 | 大小 | sha256 | 结构 |
|---|---|---|---|
| `sbin/launchd` | 576,080 | `8a81bff3a51b9fbb7d95d59a7f83ff7ab54b9f5f830cdcbd67d9cb656c6e0645` | Mach-O arm64e，filetype=2（可执行），20 条 load command |
| `usr/lib/dyld` | 961,840 | `cbd74fb9ea3b2226707da2efb1c0b95bbba0521ba865faf15c30a68edcffd84b` | Mach-O arm64e，filetype=7（dylinker） |

launchd 的依赖极少（这是关键好消息）：

```
dylinker : /usr/lib/dyld
dylibs   : /usr/lib/libSystem.B.dylib, /usr/lib/libobjc.A.dylib
```

## 2. dyld shared cache 在哪里、有多大

iOS 17 把 cache 放在 **cryptex SystemOS**（`097-84276-002.dmg`，裸 APFS），不在根卷：

```
卷名: Dawn21A329.N104SystemCryptex
路径: /System/Library/Caches/com.apple.dyld/
大小: 54 个文件, 2.72 GiB
```

已完整取出 51 个（主 cache 475,136 B + 分片 `.01`–`.50` + 两个 `.dylddata`），
**缺 3 个读不出来的文件**（7-Zip 对 DMG 和裸 APFS 两条路都返回 0 字节 + "Sub items Errors"）：

```
dyld_shared_cache_arm64e.30.dyldlinkedit   (清单标称 26,230,784 B)
dyld_shared_cache_arm64e.52.dyldlinkedit   (清单标称 390,545,408 B)
dyld_shared_cache_arm64e.symbols           (符号表，运行时不需要)
```

根因推测：这几个文件在 APFS 里以 **资源叉/压缩（decmpfs）** 形式存放，7-Zip 的 APFS 实现不展开。

## 3. 由此得到的架构结论（比缺文件更重要）

**真实 launchd 跑不起来的直接瓶颈不是内容，而是 RAM 窗口。**

| 项 | 数值 | 出处 |
|---|---|---|
| 内核被允许管理的内存 | **448 MiB** | `devices/fuxi/board.toml` 的 `owned_memory_size = "0x1C000000"` |
| DarwinBoot 能拿到的连续内存 | ~490 MiB | 早期实测（释放 4 个厂商预留后） |
| 完整 dyld cache | **2.72 GiB** | 本报告第 2 节 |
| 最小自包含根（估） | 数十 MB | launchd 依赖只有 libSystem + libobjc（+闭包） |

所以：

- **整包 cache 塞进 boot.img 的 ramdisk 不可能**（2.7 GiB ≫ 0.44 GiB），`fastboot boot` 的 payload 路线到此为止。
- 能不能跑真实 launchd，取决于能否把**可执行依赖缩小到 448 MiB 以内**。

## 4. 三条候选路线（各有代价）

### A. 最小根：从 cache 抽 dylib 闭包（估计 40–100 MB，可行但缺工具）
- 需要从 dyld cache 里把 libSystem/libobjc 及其闭包**提取成独立 dylib**
- 当前卡点：`ipsw` 3.1.739 对这版 v5 分片 cache 直接报 `invalid argument`；缺的两个 `.dyldlinkedit` 恰恰可能影响 fixups
- 需要补的能力：能读 APFS 压缩/资源叉的工具（`apfs-fuse` 源码构建，或 macOS 的 `dsc_extractor`）

### B. 放大内存窗口（改 DarwinBoot）
- 目标：把 `owned_memory_size` 提到 ~3.5 GiB，让 payload 能装下 2.7 GiB ramdisk
- 现实：DarwinBoot 要求**一段连续**常规内存；当前最大 ~490 MiB。要 3.5 GiB 需要释放/让出大段 ABL 预留区，可行性未知，且 3 GB 级 boot.img 能否被 `fastboot boot` 接受也未知

### C. UFS 块设备（真正的 M2，绕过 RAM 限制）
- 让根文件系统按需从 userdata 读（实现 IOStorageFamily over NativeUfs）
- 不占 RAM，但工程量大，且依赖 P/U 两张镜像先把 UFS 硬件跑通

## 5. 复现步骤（已在本机验证过的命令）

```bash
# 1) 用 NanaZip(7-Zip) 从 IPSW 取两个 dmg
7z x <ipsw> 097-84049-002.dmg 097-84276-002.dmg -o<work>

# 2) 根卷（UDIF）：直接列 APFS 卷内文件，取 launchd / dyld
7z x 097-84049-002.dmg "sbin\launchd" "usr\lib\dyld" -o<root>

# 3) cryptex（裸 APFS）：取 cache 的 51 个分片
7z x 097-84276-002.dmg "System\Library\Caches\com.apple.dyld\*" -o<root>
```

已确认：`097-84276-002.dmg` 偏移 32 处是 `NXSB`（裸 APFS），`fsapfsinfo` 能读；
`097-84049-002.dmg` 是 UDIF（无 koly 尾部，由 NanaZip 解析）。
`fsapfsmount`（libfsapfs 20201107）能挂上但所有查找返回 ENOENT，不可用。

## 6. 需要你决定的事

1. 是否愿意再下一份 **iOS 16.x 的 IPSW**（cache 可能是单文件、7-Zip 可完整读出）
2. 是否允许我在这台机器上构建 `apfs-fuse`（读 APFS 资源叉）来补齐那 3 个文件
3. 或者直接走 C 路线（先测 P/U，把 UFS 跑通），不碰 cache 抽取

## 7. B 路线的可行性（读 DarwinBoot 源码后的结论）

`drivers/DarwinBoot/DarwinBootPkg/Driver/DarwinBoot.c` 的 `AllocateWindow()`：

- 只接受 **`EfiConventionalMemory`** 描述符里的一段**连续**区间，长度必须 ≥ 请求的窗口
- 失败时屏幕打印 `NO WINDOW <size> BIG RUN <bytes> @ <start>`，并列出 ≥1 MiB 的空闲块
- 也就是说：要装 2.7 GiB 的 ramdisk，UEFI 内存图里必须存在 **≥2.7 GiB 的连续常规内存**

当前事实：释放厂商预留后最大连续块 **~490 MiB**（早期实测）；`make-fuxi-adt.py` 允许的窗口上限是
`0x200000000`（8 GiB），限制不在软件参数，而在**内存图的连续性**。

结论：B 路线要成立，必须让 ABL 的大部分预留区变成常规内存——风险高（TZ/AOP/安全世界区域不能随便让），
且 3 GB 级 `boot.img` 能否被 `fastboot boot` 接受也未验证。**不建议先走 B。**

## 8. 我的建议顺序

1. 先等 **A5** 的真机结果（它决定 SIGBUS 根因，与 IPSW 无关但更靠前）
2. A 路线：需要能读 APFS 压缩/资源叉的工具才能补齐那 3 个文件；我可以在这台机器上构建
   `apfs-fuse`（需要你同意下载源码），或者你提供一份 **iOS 16.x IPSW**（cache 可能是单文件，
   7-Zip 能直接读全）
3. C 路线（UFS 按需读根）：工程量大但绕开 RAM 限制，需要先有 **P / U** 两张镜像的真机结论