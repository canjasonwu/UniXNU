# 诊断内核：探针清单与自检

> 目的：把"往内核里插探针"这件事变成可复现、可自检的流程，避免两类已经踩过的坑。

## 一、两类已经踩过的坑

| 坑 | 表现 | 根因 | 现在怎么防 |
|---|---|---|---|
| 探针插进死代码 | 构建成功、上机没反应 | 插到了 `return` 之后 / `if` 块内部 | `devices/fuxi/scripts/verify-probes.sh` 检查目标文件是否真的引用了 stub 符号 |
| 对象没重编 | 改了源码、行为没变 | 增量构建按时间戳跳过；或删掉 `.o` 反而让归档步骤失败 | 自检脚本对每个 `.o` 跑 `nm -u`；构建前 `touch` 源文件 |

**自检命令**（仓库根目录）：

```bash
bash devices/fuxi/scripts/verify-probes.sh
```

全部通过输出 `RESULT: ok`（11 项）；任一失败输出 `RESULT: FAIL` 并给出是哪一项。

## 二、现有探针一览

### 显示（七段数字，稳定不覆盖）

| 符号 | 位置 | 作用 |
|---|---|---|
| `fuxi_trace_mark(i)` | 多处 | 更新"阶段号"数字 |
| `fuxi_trace_wait(status)` | `kern_exit.c` | 显示 `WEXITSTATUS` / `WTERMSIG` |
| `fuxi_trace_abort(esr)` / `fuxi_trace_page(page)` | `sleh.c` | 显示 EL1 故障的 ESR / PC 页号 |

### panic 类（有文字，先发生者先 panic）

| 字符串 | 位置 | 触发条件 | 输出 |
|---|---|---|---|
| `fuxi-iabort-el0` | `osfmk/arm64/sleh.c` | 用户态取指异常（class 0x20） | `esr=… far=… pc=…` |
| `fuxi-dabort-el0` | 同上 | 用户态数据异常（class 0x24） | `esr=… far=… pc=…` |
| `fuxi-sigbus` | `bsd/kern/kern_sig.c` `psignal` | 内核要投递 SIGBUS | `caller=… pid=…` |
| `fuxi-vmfault` | `osfmk/arm64/sleh.c` | 用户故障 `vm_fault` 失败 | `ret=… addr=… prot=… pc=… sp=…` |
| `fuxi-load-bad` | `bsd/kern/mach_loader.c` | 加载结果不可用（entry/sp/size 为 0 或 sp 非法） | `entry=… sp=… size=…` |
| `fuxi-decrypt` | `bsd/kern/mach_loader.c` | 进入 `LC_ENCRYPTION_INFO` 分支 | `cmd/cmdsize/cryptid/cryptoff/cryptsize/at` |

### 关键语义

- `ux_exception.c:54`：`EXC_BAD_ACCESS` 且 code ≠ `KERN_INVALID_ADDRESS` → **SIGBUS**，即 SIGBUS = 保护故障。
- `vm_fault` 返回值就是被翻译成信号的 `kern_return_t`：`2` = `KERN_PROTECTION_FAILURE`。
- init 退出原因里的 **namespace 2 = `OS_REASON_SIGNAL`**（`OS_REASON_EXEC` 是 9）。

## 三、构建注意事项

1. 改完 `bsd/kern/*.c`、`osfmk/arm64/sleh.c` 等文件后，**先 `touch` 该文件**再构建，否则增量 make 可能跳过。
2. **不要删除 `.o` 文件**：内核归档步骤（`build-corecrypto-14.py` 等）按固定清单跑 `nm`，缺一个对象整个构建就会在最前面失败。
3. 若 `compile_commands.json` 损坏（我们的构建曾写出 `[$` 前缀且尾部截断），可以逐条容错解析取到所需编译命令，用它单独重编目标对象。
4. panic 屏的代码 blob 必须落在 **`__TEXT_EXEC`**（`initprot=5`）；`__TEXT` 是只读不可执行（`initprot=1`），放进去会在执行时触发 EL1 取指权限故障。
