# 待测镜像批次（2026-10-30 22:10）

> 目标：定位 HFS 根上 PID 1 被 SIGBUS（signal 10）杀死的来源。
> 背景纠正：`OS_REASON_EXEC` 是 **9**，`namespace 2` 是 `OS_REASON_SIGNAL`；所以之前屏上
> `exit reason namespace 2 subcode 0xa` 的真实含义是 **signal 10 = SIGBUS**，不是"解密失败"。

## 本轮同时按了三个探针（同一内核）

| 探针 | 触发条件 | 屏幕文字 |
|---|---|---|
| EL0 取指故障 | class 0x20（用户态取指异常） | `fuxi-iabort-el0: esr=… far=… pc=…` |
| EL0 数据故障 | class 0x24（用户态数据异常） | `fuxi-dabort-el0: esr=… far=… pc=…` |
| SIGBUS 投递 | 内核 `psignal(p, SIGBUS)` | `fuxi-sigbus: caller=… pid=…` |

先发生的那一个会 panic（panic 屏已修好，能显示文字）。

## 镜像清单

| 编号 | 文件 | sha256 | 根文件系统 | 说明 |
|---|---|---|---|---|
| A | `artifacts-SIGBUS/boot.img` | `3ff5efdff5534600e3cb14dcfd126753f512038e5632a2f6de012b6f46122cde` | HFS+ 卷（探针：getpid → open("/hello.txt") → write → 空转） | **主实验**：抓 SIGBUS 来源 |
| B | `artifacts-SIGBUS-CONTROL/boot.img` | `e305ac6622458b7bdd3e2f0c9a372fb6d4ac597e0786ef186932a98188489eb5` | mockfs（普通 Mach-O init，退出码 1） | **对照**：同一内核、无 HFS，验证 panic 屏与用户态 exec 正常 |

## 判读表

### 镜像 A（HFS 根）

| 屏幕 | 结论 | 下一步 |
|---|---|---|
| `fuxi-dabort-el0: esr=… far=… pc=…` | 用户态**数据访问**触发故障 | 用 ESR 的 FSC（低 6 位）判类型：13–15=权限故障，4–7=地址未映射，16=总线错误；far 是出错地址，pc 是用户态指令地址 |
| `fuxi-iabort-el0: esr=… far=… pc=…` | 用户态**取指**故障 | 若 pc ≈ 程序入口，说明代码页没有执行权限或没映射 |
| `fuxi-sigbus: caller=… pid=…` | SIGBUS 是**内核主动投递**的 | caller 地址可反查函数（把地址发我，我用 payload 内核符号表解析） |
| 只有三个数字（无 panic） | init 在正常空转 | ✅ 用户态跑起来了，SIGBUS 问题消失 |

### 镜像 B（对照）

| 屏幕 | 结论 |
|---|---|
| `initproc failed to start … (signal 0, exit status 1)` | ✅ panic 屏正常 + mockfs 下 init 能执行并正常退出 |
| `fuxi-*` 探针文字 | ❗ 说明故障与 HFS 无关，是内核侧问题（exec/线程状态） |
| 只有数字 | 说明 mockfs 下 init 也没跑起来（退化） |

## 上机步骤（每个镜像）

1. 手机长按电源 10–15 秒进 fastboot（`adb devices`/`fastboot devices` 能看到即可）。
2. `fastboot boot <镜像路径>`。
3. 等 20–30 秒，拍屏幕（或把文字念给我）。
4. 想测下一个：再长按电源，重复。

## 备注

- 两个镜像的内核完全相同，只有 data ramdisk 不同（HFS 卷 vs Mach-O init）。
- 162 MB 镜像加载正常（实测 `Sending 165644 KB OKAY`）。
- 镜像 A 里的 HFS 卷沿用的是上一轮的探针（getpid/open/write/空转），不影响本轮定位。
