# fuxi 板级常量审计（对照 Xyvern 文档的实测事实）

> 目的：把我们 ADT 生成器 / 板级配置里的常量，与 `Xyvern08371/unixnu-fuxi` 文档中
> 记录的真机实测值逐条对照，找出「我们与实测不符」的地方。
> 结论先行：**GIC 正确；panic 日志地址与定时器中断号两处与 fuxi 不符**。

## 1. 已核对且一致

| 项 | 我们的值 | Xyvern 实测 | 结论 |
|---|---|---|---|
| GIC distributor | `0x17100000`，size `0x10000` | 同 | ✅ 一致 |
| GIC redistributor | `0x17180000` | 同 | ✅ 一致 |
| GICR 每核大小 | `0x40000`（生成器头注释） | `GICR_PE_SIZE = 0x40000` | ✅ 一致 |
| arm-io 窗口 | `0x17000000` | 同（APSS 块） | ✅ 一致 |
| ADT 中 gic reg 相对父节点 | `0x100000` / `0x180000`（相对 arm-io） | 「必须相对 /soc，XNU 会再加 `soc_base_phys=0x01000000`」 | ✅ 等价（`0x17000000+0x100000=0x17100000`） |
| splash 帧缓冲 | `0xB8000000`（panic 屏 patch 直接写） | ABL splash `0xB8000000`，size `0x2B00000` | ✅ 一致 |
| 内核窗口下移（低 4 GB） | board.toml `owned_memory_size = 0x1C000000`，窗口在 `0x89800000` 附近 | Gunyah stage-2 只映射低 4 GB → 必须下移 | ✅ 一致 |

## 2. 与 fuxi 不符、需要注意的两处

### 2.1 panic 日志物理地址是树莓派的

`pexpert/pexpert/arm64/BCM2837.h`（我们的内核实际用的板级头）：

```c
#define PLATFORM_PANIC_LOG_PADDR   0x3c0fc000
#define PLATFORM_PANIC_LOG_SIZE    16384
```

`0x3c0fc000` 是 Raspberry Pi 3 的地址，在 fuxi 上未映射。证据：QEMU 启动日志里
（`devices/fuxi/scripts/qemu-console.sh` 读出的 msgbuf）出现两次

```
debug_log_init: Error!! gPanicBase is still not initialized
```

影响：Apple 的 debug log（`gPanicBase`）不可用。msgbuf 仍然工作，所以我们的 panic 屏
能显示文本，但这条错误说明该板级常量对 fuxi 是错的。

**已修复（2026-10-27，离线验证）**：内核在 fuxi defconfig 里已启用
`CONFIG_XNU_DT_PANIC_REGION=y`，所以 panic 区域本应从设备树读取，而我们的 ADT 缺少对应
节点。`pexpert/arm/pe_init.c` 的实际读取方式是：

```
SecureDTLookupEntry(0, "pram", &entry)        -> reg = <phys, size>
SecureDTLookupEntry(0, "/chosen")             -> embedded-panic-log-size
```

（最初误以为是 `/chosen/embedded-panic-log`，QEMU 复现证明那样无效。）

已在 `devices/fuxi/scripts/make-fuxi-adt.py` 增加顶层 `pram { reg = <addr, 0x4000> }`
和 `chosen/embedded-panic-log-size = <0x4000>`，地址取 `DRAM_BASE + dram_size - 0x10000`。
离线验证：给 `scripts/qemu-entry/make-dt.py` 加 `--panic-log-address` 后，
QEMU 内核日志中的 `debug_log_init: Error!! gPanicBase is still not initialized`
**两次全部消失**（修复前每次启动固定出现两次）。

待真机确认：地址 0xFFFF0000（2 GiB 低窗口末端）是否在 fuxi 可写 DRAM 内；若不可写，
`ml_io_map_wcomb` 会失败并安全回退（错误重新出现，不会崩溃）。

### 2.2 定时器中断号没有 fuxi 的来源

- 我们的内核用 `BCM2837.h` 的 `#define ARM_ARCH_TIMER`，走通用定时器；
- 我们的 ADT 生成器**没有 timer 节点**，也没有板级覆盖中断号；
- Xyvern 的实测结论：**fuxi 的 vtimer INTID 是 19**，需要 ADT 与板级配置都写 19。

影响：定时器中断可能挂在错误的 INTID 上。真机上系统能启动（启动路径基本是轮询），
但一旦进入依赖时钟中断的阶段（调度、超时、wait/wakeup）就会出问题。这是一个**尚未验证
的潜在缺陷**，不是已确认故障——下次上机可用「定时器是否真的触发」来验证
（例如内核侧计数器 + 我们的 panic 屏打印，属于已有手段）。

## 3. 可直接落地的离线改动（待实现）

1. `patches/xnu/zzz-fuxi-panic-log.patch`：把 `PLATFORM_PANIC_LOG_PADDR` 改为 fuxi
   owned 窗口内的地址，并在构建时断言该地址落在 board.toml 声明的窗口内。
2. `devices/fuxi/scripts/make-fuxi-adt.py`：可选地给 ADT 增加 timer 描述（若 XNU 的
   arm64 通用定时器路径能从 ADT 取中断号；需要先在 QEMU 里验证 schema）。
3. 定时器验证实验（下一次上机）：
   - init 读一个内核侧暴露的"定时器中断计数"（需要内核小补丁 + sysctl 或 panic 打印）；
   - 或直接用我们已有的 panic 屏，在 `clock_deadline` 回调里计数并在 panic 时打印。
   判据：计数 > 0 表示 INTID 19 的假设成立；恒为 0 表示中断号不对。

## 4. 小结

- 显示/GIC/内存窗口三条关键参数**与实测一致**，说明当前能启动到挂载根不是偶然。
- panic 日志地址、定时器中断号两处**来自树莓派板级头**，是 fuxi 上真实存在的适配缺口，
  且都可在离线补齐补丁，再上机验证。
