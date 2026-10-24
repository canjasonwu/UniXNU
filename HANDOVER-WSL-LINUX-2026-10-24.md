# HANDOVER — Linux 本地构建（WSL）与当前状态，2026-10-24 深夜

本文件给下一段 desktop 会话用。QQ 侧已经把 WSL 本地构建要用的东西下载到本机，
并验证过 Linux 构建在 CI 上跑通；回到 desktop 后只需要“装一次工具链 + 跑命令”。

## 1. 已经完成的事

- 把上游 UniXNU PR #100 的 Linux 构建支持合并进我们的 fork：
  `scripts/linux/`（clang xnu builtin patch、cctools-port、mig、iig、lzfse、xcrun/plutil/codesign shim、prepare-xnu.py、setup-toolchain.py）、
  `scripts/pbxproj.py`，以及 `build-xnu-4k-baseline.sh`、`kernel.py`、`ipsw.py`、`cli.py`、
  `build-firehose.sh`、`build-corecrypto-14.py` 的合并（桌面之前的 fuxi 记录遍修复、占位归档、
  link.filelist、IORPCMessageFromMach shim 都保留）。
- 新增 workflow `.github/workflows/build-fuxi-kernel-linux.yml`（ubuntu-24.04，kernel-only）。
- 第一次跑失败是 LF/CRLF 问题（Windows 文本模式写出 648 个 `\r`，`git apply` 拒绝 LLVM patch），
  已把全部文件转回 LF 并重新提交，第二次跑通。

验证结果（run `40102457782`，commit `f1ef0cd3`）：

| 步骤 | 耗时 |
| --- | --- |
| Install the Linux toolchain | 30 分 15 秒 |
| defconfig | 秒级 |
| fetch sources | 26 秒 |
| build kernel | 5 分 55 秒 |
| 总计 | 约 37 分钟 |

产物：artifact `fuxi-kernel-linux-40102457782`，其中
`kernel.macho` 50,978,816 字节，sha256 `218371123417e9ed57ac65498d163549f3524ed8ad08465adbc59fa714bceea2`，
prelink 到 iPad Pro 17.7.11 的 kernelcache，8 个 archive 全部构建成功。
（macOS 构建的同一个 kernel.macho 是 53,731,328 字节；Linux 版小约 5%，符合上游文档
“符号/对象一致、代码段大小差几个百分点”的说明。）

## 2. 本机已经下载好的东西

都在 QQ 会话的工作区里（desktop 会话可直接读）：

| 路径 | 内容 |
| --- | --- |
| `C:\Users\Jason Wu\.dsh\profiles\desktop\community-fuxi\wsl\UniXNU-f1ef0cd3.tar.gz` | 我们的 fork 在 Linux 构建提交（f1ef0cd3）上的完整快照，2,104,001 字节，sha256 前缀 `92b2e1b2289cef5f`。含 scripts/linux、scripts/unixnu、devices/fuxi（board.toml + configs/fuxi_defconfig）、kits、patches/xnu 全部 |
| `C:\Users\Jason Wu\.dsh\profiles\desktop\community-fuxi\linux-kernel-40102457782\` | Linux CI 产物：kernel.macho（50,978,816 B）、kernel.json、kernel-build.log、archive 日志等 |
| `C:\Users\Jason Wu\.dsh\profiles\desktop\community-fuxi\` 其他目录 | 之前从社区仓库拉的参考材料（Xyvern 的 fuxi 实测数据、UEFI UFS 驱动、hanzi 的 iosroot/fiemap、上游 Linux 脚本） |

注意：WSL 里可以直接读 `/mnt/c/...`，但**不要**在 `/mnt/c` 下编译（9p 文件系统又慢、permission 会坏），
先复制/解压到 WSL 自己的 home。

## 3. 回到 desktop 之后：WSL 本地构建步骤

准备：WSL2 + Ubuntu 24.04（22.04 应该也行），磁盘 ≥ 25 GB 空闲，内存 ≥ 8 GB，
网络能访问 GitHub（需要拉 LLVM 源码、cctools、mig、iig、xnu 源码；国内建议挂代理）。

```bash
# 1) 把快照放到 WSL 的 home 并解压
mkdir -p ~/UniXNU && cd ~/UniXNU
tar -xzf "/mnt/c/Users/Jason Wu/.dsh/profiles/desktop/community-fuxi/wsl/UniXNU-f1ef0cd3.tar.gz" --strip-components=1

# 2) 一次性安装 Linux 工具链（30-60 分钟，视 CPU；会编 LLVM 24 + cctools + mig + iig + lzfse）
sudo python3 scripts/linux/setup-toolchain.py

# 3) 之后每次迭代（约 6-15 分钟）
python3 ./unixnu defconfig fuxi_defconfig -D devices/fuxi
python3 ./unixnu fetch
python3 ./unixnu build kernel

# 4) 产物
ls -l out/fuxi/kernel/kernel.macho
```

Windows 侧取产物：`\\wsl$\<发行版名>\home\<用户名>\UniXNU\out\fuxi\kernel\`。

说明：
- 工具链装在 `/opt/unixnu-linux`（可用 `UNIXNU_TOOLCHAIN` 改），装一次就够，不要重复装。
- `unixnu fetch` 需要网络（拉 xnu 等源码）；也可以设 `GITHUB_TOKEN` 避免 API 限流。
- 这个脚本只在 WSL 内部写 `/usr/bin/xcrun`、`/usr/bin/plutil`、`/usr/bin/codesign` 等 shim，不影响 Windows。
- 本地只能编 kernel；payload / firmware / boot.img 仍然要走 macOS 那条 workflow（EDK II + Xcode）。

## 4. 常见坑（这次踩过或差点踩的）

1. **不要在 Windows 侧用文本模式工具修改仓库文件**：Python `Path.write_text()` 在 Windows 会把
   `\n` 翻成 `\r\n`，patch 和 shell 脚本立刻坏。要用二进制写（`write_bytes`）或统一转 LF。
2. DSH 沙箱可能拦 WSL（`Wsl/E_ACCESSDENIED`）：如果 desktop 会话里 `wsl -l -v` 报这个，
   就在普通终端里手动跑 WSL 命令，别让 agent 代跑。
3. WSL 访问 `/mnt/c` 编译会非常慢且可能权限错误，务必先复制到 `~/`。
4. 磁盘不够时 LLVM 链接会失败；`df -h ~` 先看一眼。
5. Ubuntu 22.04 的 apt 源和 24.04 略有差异；`setup-toolchain.py` 会自己处理 apt 源，失败时看它输出的第一个报错。

## 5. 当前未完成 / 接下来

- **手机不在 USB 上**（上次测试镜像把看门狗装错位置导致卡住，需要长按电源 10-15 秒）。
  手机回来后跑 `pwsh "C:\Users\Jason Wu\dsh\test-image.ps1"`，判词见 `kernel-autopilot.md`。
- Linux workflow 还没加 `actions/cache`；加上后 CI 每轮可从 37 分钟降到 ~10 分钟（未做）。
- 社区 fuxi 情报：`COMMUNITY-FUXI-2026-10-24.md`（仓库根目录）与
  `C:\Users\Jason Wu\.dsh\profiles\desktop\community-fuxi\README.md`。
- 之前两份交接：`HANDOVER-2026-10-23.md`（CI 基础设施/preflight）、
  `HANDOVER-PANIC-2026-10-24.md`（panic screen）。

## 6. 仓库状态

- 仓库：`canjasonwu/UniXNU`（public fork），HEAD：`f1ef0cd3`（fix: write the Linux build files with LF, not CRLF）
- 最新运行：`40102457782` success（build fuxi kernel on linux）
- macOS 全流水线最新成功：`40072261368`（commit `0ef7b0b7`，含 watchdog/payload reporting 改动）
- Actions：enabled；fork 的 push 触发已移除，派发前用 `python tools/ci-preflight.py --workflow <wf> [--dispatch]`
