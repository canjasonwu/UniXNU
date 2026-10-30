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
| boot.img 大小上限 | 逐级做一个"空 payload + 大文件"的镜像（256/512 MB）试 `fastboot boot` | 决定是否需要 userdata 路线 |
| `CONFIG_XNU_NATIVE_UFS=y` 的 pass2 静默失败 | 本地打开开关，抓**第一处**错误 | 打开 M2 的大门 |
| 真实 root 树来源 | 需要你提供 IPSW 或预解出的 iOS 根目录 | M2 的前置条件 |
