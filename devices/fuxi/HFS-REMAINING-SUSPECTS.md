# HFS 集成：剩余可疑点与验证顺序

> 2026-10-27 深夜整理。日期内已确认：HFS 40 个对象编译通过、链接进内核（内核 51.48 MB）、
> 注册调用已确认编译进 `bsd_init.o`（`nm -u` 可见 `_sweet_hfs_register`）。
> 但**只要内核里带着 HFS，启动就停在三色带、无 panic 文本**——即使 data ramdisk 仍用已知可启动的
> Mach-O init（镜像 ZU `07fe3a40`）。

## 0. 已知的对照组

| 镜像 | 内核 | data ramdisk | 结果 |
|---|---|---|---|
| 之前（无 HFS） | 无 HFS | Mach-O init | ✅ 启动到 init 死亡 panic |
| ZU `07fe3a40` | **含 HFS** | Mach-O init（同前） | ❌ 三色带 |
| ZL/ZM | 含 HFS | HFS+ 卷 | ❌ 三色带 |
| ZS/ZT | 含 HFS + 注册调用 | Mach-O / HFS | ❌ 三色带 |

**结论：问题由"HFS 代码/注册存在"引起，与 ramdisk 内容无关。**

## 1. 可疑点（按可能性排序）

### S1. `sweet_hfs_register()` 内部卡住（最可能）

调用点：`bsd_init.c` 第 797 行（`vfsinit()` 之后、`vfs_mountroot()` 之前）。内部步骤：

1. `panic_on_assert` 赋值（无害）
2. `PE_parse_boot_argn`（DEBUG 下，无害）
3. `hfs_converterinit()` —— 已换成空实现（`-encodings` 已移除）
4. 构造 `op_descs` / `vfs_fsentry`（栈上，无害）
5. **`vfs_fsadd(&vfe, &handle)`** ← 可疑：可能在 vfs 锁/链路未就绪时阻塞
6. **`hfs_init_zones()`** ← 可疑：zone 子系统在此时可能未就绪
7. **`hfs_sysctl_register()`** ← 可疑：sysctl 树可能尚未建立，注册链路可能递归/阻塞
8. 我们插入的 stage-2 panic（没有出现 → 说明 1–7 中某一步之前就停了）

**验证方式**：在函数入口插 `panic("fuxi-hfs: entered")`。
- 看到 → 卡在 3–7 之间，再按 5/6/7 逐个加探针（每步一次上机）
- 看不到 → 说明**根本进不了这个函数**（见 S2/S3）

### S2. 只是"内核里有 HFS 代码"就破坏启动（构造器/数据段）

force_load 会把 HFS 的所有对象链进来，包括：
- 全局变量初始化（`panic_on_assert`、`hfs_corruption_panics`、zone 表等）
- 可能存在的 `__attribute__((constructor))` 或 section 顺序变化

**验证方式（最便宜、最决定性）**：**保留 HFS 归档但注释掉注册调用**。
- 若能正常启动 → 问题在 S1（注册函数内部），后续只在此处排查
- 若仍三色带 → 问题在 S2（代码存在即破坏，需查构造器/段布局/符号冲突）

### S3. 注册时机过早

即使 S1/S2 都排除，`bsd_init` 第 797 行的时机也可能过早：`vfs_fsadd` 通常由 kext 在
IOKit 启动后调用，而我们在 `vfsinit()` 刚结束就调用。
**验证方式**：把注册移到 `bsd_init` 中更晚的位置（例如 mountroot 前一行 `vfs_mountroot()` 之前），
或用 boot arg 延迟。

### S4. 符号/定义冲突（可能但不直接导致 hang）

我们自己提供的替换项与内核同名符号：
- `panic_on_assert`（`#ifndef` 保护）
- `hfs_converterinit` / `hfs_getconverter`（空实现）
- `OSKextGetCurrentLoadTag` 宏重定向
若某处重复定义会链接失败（当前没有），但**同名弱符号/不同签名**可能造成运行时错乱。

### S5. Force-load 与我们的 4K 页/pmap 补丁交互

内核从 50.94 MB 涨到 51.48 MB。我们的板级补丁里有 4K 页表、sync 64-bit PA 等对地址敏感的改动。
理论上不会冲突，但可以用"只链接 HFS 的空归档（不含对象）"做对照验证。

## 2. 建议的下一步实验顺序（每次 1 个变量、1 次上机）

| 顺序 | 实验 | 判定 |
|---|---|---|
| 1 | **保留归档、注释注册调用** | 能启动 → S1；仍挂 → S2 |
| 2 | 若 S1：在 `sweet_hfs_register` 入口 panic | 看到 → 逐个步骤排查；看不到 → S3 |
| 3 | 若 S1：把 `vfs_fsadd` 之后立即 panic | 定位 5/6/7 哪一步 |
| 4 | 若 S2：去掉 HFS 归档只留空归档 | 确认是否"代码存在即破坏" |
| 5 | 注册成功后再处理挂载：`DKIOC*` ioctl、日志重放、块大小、大小写敏感 | 见下节 |

## 3. 注册成功后才需要面对的挂载问题（预判）

`hfs_mountfs` 会对设备做一串 ioctl，我们用的是 md 设备（`bsd/dev/memdev.c`，`D_DISK` +
`mdevstrategy` 已具备）：
- `DKIOCGETBLOCKSIZE` / `DKIOCGETPHYSICALBLOCKSIZE` / `DKIOCGETBLOCKCOUNT`
- `DKIOCSETBLOCKSIZE`（读写路径需要 `FWRITE`）
- 日志重放（我们的镜像用 `mkfs.hfsplus` 默认非日志卷，应跳过）
- 大小写：镜像用标准 HFS+（`H+`），非 HFSX

若其中某个 ioctl 返回错误，HFS 会打印并让 mountroot 继续尝试下一个文件系统；最终会 panic
`Failed to mount root device`（可读文本），因此挂载阶段的失败是**可见**的，不像现在这样静默。

## 4. 环境/构建层面的遗留问题（不直接相关但会拖慢后续）

1. **per-object CFLAGS 不触发重编**：改 `EXTRA_MAKE_ARGS` 后必须 `touch` 源文件或删对象；
   删对象又会导致后续链接失败（今天踩过两次）。安全做法是 `touch` 源文件。
2. **配置系统绕过**：HFS 通过"外部编译 + force_load"接入，`options SWEET_HFS` 无效，
   因此 `bsd_init.c` 里的守卫改成无条件调用。
3. **hfs_encodings 模块**：已移除；HFS+ 不需要转换器。若将来要挂老 HFS 卷再补。
4. **旧的 hpmount 工具**：不兼容新 `mkfs.hfsplus` 的卷（HFSX 或 4K 块），已放弃用它在
   Linux 上填文件；将来填根镜像需要别的方案（用 macOS、或写一个最小 HFS+ 写入器、或用
   `hfsprogs` 的 `-J`+官方工具）。

## 5. 代码状态

- 已提交：HFS 编译/链接、注册补丁、mockfs Mach-O 检查、HFS+ 镜像脚本（`a6386f2` 及后续本地修改）
- 未提交：今晚最后几轮的探针与转换器替换（下次提交前先清理探针）
- 关键脚本：
  - `devices/fuxi/scripts/stage-hfs.py`：拉取并 staging HFS 源码
  - `devices/fuxi/scripts/enable-hfs.py`：六处集成
  - `devices/fuxi/scripts/build-sweet-hfs.py`：40→38 对象编译 + 归档
  - `devices/fuxi/scripts/build-hfs-root.py`：HFS+ 根镜像


## 2026-10-27 23:20 - Screen legend corrected, two hypotheses eliminated

The three colour bands are the **kernel entry probe** (green/blue/magenta), not the panic
screen. A real panic replaces the magenta band with text, as seen in earlier sessions. So
"three bands, no text" means the kernel entered and then hung with no panic at all - it does
not mean the panic text renderer failed. That eliminates the earlier S6 hypothesis.

Eliminated by measurement (each image boots the entry probe and then hangs):
- V1 1ec71b08: HFS archive linked, registration compiled out (`#if SWEET_HFS` never defined).
- V3 ab876573: same, plus the __mod_init_func table removed from the archive (the three HFS
  sysctl-list constructors). nm -u shows no sweet_hfs reference in bsd_init.o.

So the trigger is neither the registration logic nor the module constructors: the mere
presence of the HFS code/symbols in the kernel image is enough to hang the boot.

Process problems found and repaired today:
- local-build.sh copies the repository build script into the tree at every build, so edits
  made only in the tree are overwritten. The first "no HFS control" was invalid for this
  reason: both kernels contained `_hfs_vfsops` (2 symbol hits in each). Edit the repository
  copy, never the tree copy.
- A scripted edit of the build script corrupted it (syntax error at line 936). Restored with
  `git checkout -- scripts/build-xnu-4k-baseline.sh`, tree copy re-synced, and a healthy
  build verified afterwards (image 75ad7d86, kernel exports _hfs_vfsops).

Next experiments, in order:
1. V4 redone correctly: no HFS, but a ~540 KB dummy blob force-linked. Same hang -> the
   trigger is image size/layout, not HFS content; boots -> the trigger is HFS-specific and
   the next step is an object-level bisect starting with the IOKit glue alone.
2. If layout: compare the Mach-O segment tables of a pre-HFS known-good kernel and the
   HFS kernel (segment count, file vs vm sizes, alignment), and check what the loader maps.
3. Keep the archive bisect scripted: build-sweet-hfs.py already parameterised by OBJECTS.
