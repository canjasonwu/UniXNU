# 抄上游 root 加载方案：评估与实验计划（2026-10-29）

> 用户指示：试试抄他们的 extent 表方案，"反正最后也就是跑而已"，仍然用屏幕方块（`fuxi_trace_mark`）做诊断。

## 1. 上游到底怎么做的（读代码结论）

### bangprovn/ios-fuxi：extent 表（ADT 属性）+ UFS 驱动

文件：`xnu/patches/0088-fuxi-extent-root.patch`（200 行，改 `bsd/dev/sweet_ufs.c`）。

- root 是 userdata 里一个**固定（pinned）文件**，块被驱动独占；
- ADT `/chosen` 两个属性：
  - `fuxi-root-extents`：u64 三元组 `{文件块, 分区块, 块数}`（4 KiB 单位、分区相对）
  - `fuxi-root-blocks`：root 总块数
- `fx_load_extents()` 校验：文件块必须从 0 连续递增、分区范围合法、extent 之间不重叠；
- 读：`fx_map(lblk)` 把 root 逻辑块映射到设备 LBA，读只走 root 的 extent；
- 写：`fx_write_allowed(lba,count)` **只允许写 root extent 内**，其它一律 `EROFS`；
- 块设备名 `ufsroot0`，交给 HFS 挂载当根。

### hanzi123-bilp/uefi-fuxi：loader 侧

- `fuxi-loader-build/loader.c` 明确"**No storage access**"：loader 不读存储，payload 内嵌；
- 他们的 "payload with the pinned root extents" 提交只改二进制 payload —— extent 表是**构建时**算好塞进 payload 的。

### 我们已有的等价物：`drivers/NativeUfs`

- README：「Native UFS storage driver. Owns the host controller, SMMU, queues and the root read path.
  **Current state: the native UFS root works on sheng.**」
- `RootLayout.h`：读 LUN0 GPT + **root image manifest**，窗口结构
  `RootWindow{char name[32]; uint64_t offset; uint64_t size; uint8_t sha256[32];}`，
  另有 `imageBytes`、GPT CRC、ext4 魔数等校验字段；
- `RootWindowRead.h`：4 KiB 窗口读 + 完整性校验；
- `RootPublication.h`：按 SystemOS/OS 次序发布 media（`ufsBuildRootPlan`）。

**结论：我们的机制是上游 extent 表的超集**（多了 SHA256 与 GPT 校验），缺的不是算法，而是
（a）在 fuxi 上把 UFS 控制器跑起来、（b）userdata 里放好 manifest + root 镜像。

## 2. 本轮已完成的离线工作

| 项 | 结果 |
|---|---|
| 编译 `drivers/NativeUfs` 参考实现 | ✅ 成功（484 KB 对象） |
| 归档到构建期望路径 `build/native-ufs/libNativeUfs.a` | ✅ 487 KB，86 个 NativeUfs 符号（已给 `build-nativeufs.py` 补上归档步骤） |
| 确认 ADT 已有 `ufshc` 节点 | ✅ `make-fuxi-adt.py:208`：`compatible="qcom,sm8550-ufshc"` |
| 确认构建脚本有 `XNU_NATIVE_UFS` 接线 | ✅ 第 216/638 行（`PERSONALITY_CFLAGS`、`-force_load`） |
| 打开 `CONFIG_XNU_NATIVE_UFS=y` 做链接验证 | ❌ 内核构建在 pass 2 处**静默失败**（kernel.log 只有 2 行，stderr 无输出）。已恢复 defconfig 为 `n` |

## 3. 下一步实验（按顺序，全部可用方块诊断）

### D1（最便宜，现有代码即可）：只读 UFS 可见性

- 我们的 `ShengPlatformPower.cpp` 里已有 `fuxi_ufs_inspect()`：读 GCC/UFS 寄存器后 `panic` 打印。
- **关键**：HFS 版内核的 panic 屏目前不渲染，所以要用 **无 HFS 内核**（能显示 panic 文字）来跑：
  - 构建：`sweet-hfs` 归档移开 + 注册调用去掉（对照基线，已验证能出文本）
  - 内核参数加 `fuxi-ufs-probe=2`
- 判据：panic 文字里出现 `gdsc/axi/ahb/uic` 寄存器值，且 `gdsc` 的电源位为 1 → 控制器可见。

### D2：修链接失败（下一会话第一步）

- 现象：`CONFIG_XNU_NATIVE_UFS=y` 时 pass 2 静默退出。
- 定位方法：在 `local-build.sh` 调用内核脚本处加 `set -x`/`bash -x` 或把 `unixnu` 的 stderr 一并落盘；
  逐个检查 216 行之后的 guard（这些 guard 都会 `echo ... >&2; exit 1`，但本次没看到，说明失败点更早）。

### D3：写 userdata manifest + root 镜像

- 格式来自 `RootLayout.h`：需要在 userdata 起始处放 manifest（含 magic/version/count/窗口表），
  再用 `fastboot flash userdata <image>` 写入（用户已允许写 userdata；恢复 = `fastboot erase userdata`）。
- 也可以**先用我们自己的简化版**：单个窗口、offset=0、size=镜像大小，跳过 GPT/ext4 校验，
  只要驱动按窗口读就行——这正是 extent 表的最小形态。

### D4：HFS 当根

- root 窗口里放 **HFS+ 镜像**（带真实 `/sbin/launchd`）→ HFS 挂载 → PID 1 从 HFS 启动。
- 仍未解决：Linux 侧如何往 HFS+ 镜像里放文件（`hpmount` 不认新卷）。候选：
  ① QEMU + Alpine 里 `mount -t hfsplus` 填充；② 自写最小 HFS+ 写入器；③ 用上游预置镜像抽文件。

## 4. 风险与红线

- 写 userdata 已获允许（数据可丢，`fastboot erase userdata` 可恢复）；**boot/bootloader/GPT 一律不碰**。
- extent 机制的写保护（`fx_write_allowed`）值得照抄：即使 root 挂在 userdata 上，内核也只会写 root 自己的块。
- 驱动默认 `opt-in`：`native-ufs-probe=*` / `native-fuxi-ufs-root=1` 才动作，默认不改任何 MMIO。
