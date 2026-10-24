# HANDOVER — panic screen（2026-10-24，QQ 会话 → desktop 会话）

本文件接续 `HANDOVER-2026-10-23.md`，只讲 panic screen 这件事：已经做完的部分、
验证证据、怎么复现，以及等手机接上后怎么验收。

## 1. 结论

- panic screen 已从 sheng 移植到 fuxi，并已通过 CI 端到端验证：
  run `40030352743`（commit `b1a42380`）全绿，kernel → payload → firmware → boot.img → upload 全部 success。
- 仓库 HEAD 现在是 `b1a42380d1f8`；Actions enabled；没有在跑/排队的 run。
- 真机验证还没做（手机暂时没接）。

## 2. 改了什么（3 个 commit）

| commit | 文件 | 内容 |
| --- | --- | --- |
| `6175f6b241` | `devices/fuxi/scripts/patch-xnu-entry-probe.py` | 移植 sheng 的入口探针/panic 补丁脚本，并做两处修复（见下） |
| `91cdacf1cc` | `devices/fuxi/scripts/xnu-panic-screen.c` | 原样复制 sheng 的 panic 屏 C 源码（freestanding，Font.h 来自 uefi-fuxi 的 FrameBufferSerialPortLib） |
| `b1a42380d1` | `devices/fuxi/board.toml` | `panic_screen = true`（原来 false，注释说脚本未移植） |

对 `patch-xnu-entry-probe.py` 的两处修复，都是实测出来的、原样复制会踩的坑：

1. **自动找 cave**：sheng 的 CAVES 是固定偏移，按 entry 平移后在 fuxi 内核上落在真实代码里，
   原脚本会报 `Padding at ... is not empty` 直接拒绝。现在改为在可执行段
   （`__TEXT` / `__TEXT_EXEC` / `__PLK_TEXT_EXEC`）里找连续零填充：4 字节对齐、
   长度 >= 4*0x60 + 0x1000、且 entry / MMU 站点 / `_panic` / 公共 panic 漏斗都能
   ±128 MiB 分支到达。
2. **重名符号**：fuxi kernel.macho 里 `_panic` 在同一地址有两个符号，`_vinfo` 还有一个
   `N_ABS` 的 0 地址符号；原脚本的“地址→名字”映射会把它们丢掉，然后静默回退到 sheng 的
   平移常量，等于把 branch 写到错误地址。现在改成逐行解析 `llvm-nm` 输出建立
   “名字→地址”表，优先非零地址。

## 3. 验证证据

本地用 `C:\Users\Jason Wu\dsh\artifacts-kernel\kernel.macho`（53,731,328 字节，
来自 run `40010884906`）做可行性检查：

- entry `0xfffffff0073f4488`（相对 sheng 平移 -0x45DC000）
- `_panic` `0xfffffff007991e68`，`_vinfo` `0xfffffff007ac6970`
- `_panic_trap_to_debugger` `0xfffffff007272574`，`_start_first_cpu` `0xfffffff0073f605c`
- 诊断符号 `_max_mem` / `_vm_page_pages` / `_vm_page_free_count` 都存在
- sheng 的 CAVES 平移后全部非零 → 原脚本必拒
- 自动选中的 cave：`__TEXT_EXEC` 末尾 `0xfffffff007a21f10 .. 0xfffffff007a24000`（0x20F0 字节）

CI 端（run `40030352743`）artifact 里的 `payload-patch.log`：

```
cave: zero padding at 0xfffffff007a21f10..0xfffffff007a24000
patched kernel.macho: stage 1 blue bar, stage 2 magenta bar, no reset probe,
  panic screen (0x9ed bytes at 0xfffffff007a21fd0; common funnel at 0xfffffff007272574)
```

`payload.json`（同一个 artifact）：

- `"panic_screen": true`
- `"kernel_sha256": "cc12428372d39b..."`，`"payload_kernel_sha256": "e414872a..."`：
  两者不同，说明补丁只改 payload 里的 kernel 副本，原始 kernel.macho 不变。
- `"payload_bytes": 201326592`（192 MiB，仍是老尺寸，未缩小）。

artifact：`fuxi-kernel-40030352743`，35,287,816 字节。

## 4. 怎么复现 / 继续

- 派发前一律先预检（8 分钟间隔、每小时 3 次、同 commit 不重跑）：
  ```
  python tools/ci-preflight.py --workflow build-fuxi-kernel.yml
  python tools/ci-preflight.py --workflow build-fuxi-kernel.yml --dispatch
  ```
- 单轮约 18 分钟（kernel ~10 分钟、payload ~2 分钟、firmware + boot image ~3 分钟）。
- 失败时先看 artifact 里的 `out/fuxi/logs/payload-patch.log` / `payload.log`，
  不要重跑猜原因。
- 如果以后内核配置改动导致布局再变：自动找 cave 会自己重新选；仍然失败时看报错：
  - `Padding at ... is not empty`：选中的 cave 被占，检查 `find_cave` 的段过滤；
  - `panic screen is ... bytes, padding ends at ...`：panic 屏代码超出现有空隙，扩大 needed 或换段；
  - `Missing or invalid diagnostic symbol ...`：内核缺 `_max_mem` / `_vm_page_pages` / `_vm_page_free_count`。

## 5. 真机验证（等手机接上）

- 开机时会先看到两个彩条信号（patch 进 payload 副本的 stage 1/2 探针）：
  屏幕下方第 1 个 1/16 带**蓝色**（内核入口、MMU 关），第 2 个 1/16 带**品红**
  （MMU 打开）。看到它们就说明补丁确实执行了。
- panic 时 `panic()` 与公共 funnel 会被改写到 `0xfffffff007a21fd0` 的 panic 屏代码，
  用 `vinfo.v_baseaddr` 画 format string 到 framebuffer；这段代码在硬件上还没验证过。
- 注意：现在 boot.img 仍然是 203 MB（payload 卷 192 MiB），真机 fastboot 传会很慢；
  建议先把 `devices/fuxi/board.toml` 里 payload 的 `size_mib` 降到 8 再出一版。
- 恢复手段：fastboot boot 是主救援路径；EDL 只作为最后手段（fuxi 需要短接测试点，
  且小米 EDL 刷机可能需要授权账号）。

## 6. 当前状态

- HEAD：`b1a42380d1f8`（fuxi: enable the panic screen in the payload）
- 最新 run：`40030352743` success（2026-10-23T21:56:57Z 触发）
- artifact：`fuxi-kernel-40030352743`
- 无在跑 run，Actions enabled，fork 的 push 触发已经移除（firmware workflow 只剩手动）。
- 上一份交接（CI 基础设施、fork 被禁用原因、preflight 用法）见 `HANDOVER-2026-10-23.md`。
