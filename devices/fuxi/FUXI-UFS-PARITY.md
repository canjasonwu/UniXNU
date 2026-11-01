# fuxi UFS 现状与 sheng 的差距（NAS：抄作业对照表）

> 写于 2026-11-01。对照对象：本仓 `drivers/NativeUfs`（子模块，commit `af785c1`，2026-10-21 03:31:05 +0800）与
> sheng 的 `drivers/ShengUfs`（commit `99d8bcd`，2026-11-01），以及 `devices/fuxi/board.toml`、
> `devices/fuxi/configs/fuxi_defconfig`。
> 结论：**驱动代码 8/10 个头文件与 sheng 完全一致，差的不是驱动，是"板级策略 + 版本落后两个提交 +
> 内存窗口"**。

## 1. 子模块逐文件对照

| ShengUfs 头文件 | 我们的 `drivers/NativeUfs` | 结论 |
|---|---|---|
| `GccUfs.h` | 一致 | 时钟/GDSC 寄存器定义可直接复用 |
| `RootCommands.h` | **落后**（`diff -u`：26 增 / 4 删，2 个 hunk） | sheng 新增 panic 路径的 host lock（200 ms try-lock，CPU 持锁时仍能发命令）；fuxi 的 panic 诊断会受益 |
| `RootDiagnostics.h` | 一致 | — |
| `RootLayout.h` | **落后**（`diff -u`：23 增 / 11 删，5 个 hunk） | sheng 支持 **manifest v2**（`objects`：内核自己读的策略表/固件数据，flags=3、4096 字节块）；fuxi 目前只解析 v1 的 windows |
| `RootPublication.h` | 一致 | 我们已在用（`NativeUfs.cpp:1640` `ufsPublishRootWindows`、`:1943` `ufsPrepareRootWindows`） |
| `RootQueue.h` | 一致 | — |
| `RootTransportLayout.h` | 一致 | — |
| `RootWindowRead.h` | 一致 | 读窗口逻辑一致 |
| `SmmuTakeover.h` | 一致 | top-bank-first 的 SMMU 接管策略一致 |
| `TransferPoll.h` | 一致 | 轮询语义一致 |

**动作**：**不能**直接用"升级子模块"的方式拿到那两个补丁——两边不是同一个仓库：

| 用途 | 仓库 | 当前提交 |
|---|---|---|
| 我们的子模块 `drivers/NativeUfs` | `github.com/UniXNU/NativeUfs.git` | `af785c1` |
| sheng 的 `drivers/ShengUfs` | `github.com/UniXNU/ShengUfs.git` | `99d8bcd` |

两个浅克隆（`rev-list --count HEAD` 均为 1），离线无法判定祖先关系，所以只能 **cherry-pick**：
把 sheng `99d8bcd` 里的 host-lock 与 manifest-v2 两个改动摘到 `NativeUfs` 仓库，再更新子模块指针。
`af785c1`（"size the end-of-disk reserve with sheng-log-reserve"）是 NativeUfs 仓库的本地提交，
摘补丁后要验证它与 sheng 的新代码是否冲突。

**更关键的一条**：`build-nativeufs.py` **编的不是这个子模块**。它编的是
`/root/refs/NativeUFS-fuxi`（第三个仓库，`ljhhkx529/NativeUFS-fuxi.git` @ `e9ae0f6`，且工作树有
`M NativeUfs.cpp` 未提交修改）。所以上表比较的是"子模块里的代码"，**不是镜像里跑的代码**；
升级子模块不会改变归档产物。要让三者一致，得先把 refs 那份的修改并回子模块，或反过来让脚本编子模块。

## 2. 板级策略对照（这才是真正的差距）

| 项 | sheng | fuxi 当前 | 影响 |
|---|---|---|---|
| `CONFIG_XNU_NATIVE_UFS` | `y` | `n`（注释：缺 IOKit 存储头） | 驱动不参与内核 |
| 归档 | `component:ShengUfs` + 绑定 | **本次已加** `build-nativeufs.py`（自有脚本，能编过；`build-arm-driver.py NativeUfs` 在 clang-24 上崩） | 归档产出 `build/native-ufs/libNativeUfs.a` |
| 根策略 kernel args | `native-ufs-root=1`、`native-source-storage=1`、`offline-apfs-load=1` | 仅 `-apfs_shared_datavolume` + `rd=md0`（mockfs） | 根来源不同 |
| PMGR | `sheng-pmgr=2` | 无（board 注释：AMFI 会以 "No PMGR?" panic） | 启用 native-ufs-root 的前置 |
| 数据持久化 | `sheng-data-persist=2`、`persistent_data=true` | 无 | userdata 语义 |
| 安装/重置菜单 | `[payload.install]` + `sheng-data-reset` | 无（base 不支持该 schema） | 数据卷重置 |
| `packed_data_ramdisk` | `true` | `false` | payload 数据盘形态 |
| `owned_memory_size` | `0x130000000`（4.75 GiB；5.1 是十进制 GB） | `0x1C000000`（448 MiB） | 能装下的根内容 |
| UFS 供电/时钟 | `ShengPlatformPower` | 我们已有 `fuxi_ufs_inspect`/`fuxi_ufs_clock_enable`（`fuxi-ufs-probe`/`fuxi-ufs-clock` 门槛） | 需真机验证 GCC/GDSC 值 |

## 3. 我们缺的 API（按"必须/可选"分）

**必须**（没有就无法挂载 UFS 根）
1. `CONFIG_XNU_NATIVE_UFS=y` 的头文件前提：`IOKit/storage/IOBlockStorageDevice.h` 的可用形态。
   本仓已有 `devices/fuxi/IOStorageStubs.cpp`，本次构建已验证 `build-nativeufs.py` 能产出归档；
   还要确认**内核链接**时不会因为 `IOStorageFamily` 符号缺失而失败（下一步用一次完整 link 验证）。
2. `native-ufs-root=1` 对应的根匹配路径：`RootPublication` 把窗口发布成块设备后，`mountroot` 需要
   `root_match_volume_uuid` 命中；我们 board 里已有该 UUID（`04769D26-…`）。
3. PMGR 服务：sheng 用 `sheng-pmgr=2`；fuxi 的 `ShengPlatformPower` 目前"只发布服务"，需要在
   `native-ufs-root` 打开后做一次 AMFI 对照实验（不需要 userdata）。

**可选**（提升诊断/后续能力）
4. `RootCommands.h` 的 panic-safe host lock（sheng 新提交）。
5. `RootLayout.h` 的 manifest v2（`objects`）：我们当前的根 manifest 是 v1；若将来要内核自读策略表才需要。
6. `sheng-data-persist` / `persistent_data` / install 菜单：属于 userdata 生命周期，等 base 升级到 schema v2。

## 4. 建议的推进顺序（与 `FUXI-FROM-SHENG.md` 一致，这里只列 UFS 侧）

1. **cherry-pick** sheng `99d8bcd` 的 host-lock / manifest-v2 改动到 NativeUfs 仓库（不是"升级子模块"，见 §1），离线先做
2. 用一次完整 `unixnu build kernel` 验证 `build-nativeufs.py` 归档 + 内核 link（离线；本次已启动）
3. 真机：`fuxi-ufs-probe=1` / `fuxi-ufs-clock=1` 确认 GCC/GDSC 与时钟分支（已有镜像 W/X 可复用）
4. 真机：`native-ufs-root=1` + PMGR 对照（只需 HFS 根镜像加参数，不动 userdata）
5. 之后才是 userdata 镜像与根切换

## 5. 回退

- 子模块指针不变；回退只需丢弃那两个 cherry-pick
- 归档项新增不影响未启用 `CONFIG_XNU_NATIVE_UFS` 的镜像（归档只产出 .a，不参与 link）
- 根策略切换仍只改 board.toml + defconfig 一位，随时可回退到 mockfs/HFS 路线
