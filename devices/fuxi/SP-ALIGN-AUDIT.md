# SP 对齐修复的审计记录（2026-10-31）

针对 `bsd/kern/kern_exec.c` 里 `exec_copyout_strings()` 的改动（字符串区按 16 取整、指针区补齐），
逐项核对"是否引入新问题"。

## 1. 栈基址是否 16 对齐

- `kern_exec.c:6963` 起：`user_stack = load_result->user_stack`，随后 `p->user_stack = user_stack`
- `mach_loader.c` 里该值来自 `vm_map_round_page(vm_end + user_stack_size + reserved_aot_size + slide, mask)`
- 结论：**至少按页对齐（≥4 KiB）→ 天然 16 字节对齐** ✓

## 2. 多预留最多 15 字节会不会越出缓冲区

- `kern_exec.c:7802-7803`：
  ```c
  imgp->ip_argspace = NCARGS;
  imgp->ip_strspace = (NCARGS + PAGE_SIZE);
  ```
- 即字符串区容量是 `NCARGS + PAGE_SIZE`（多出**整整一页**的余量），而预留量取整最多多 15 字节
- 结论：**在余量之内** ✓

## 3. 对齐算术是否正确

`total = strings(16 的倍数) + ptr_area(补齐) + argc 槽(8)`，要求 `total % 16 == 0`。
对 5 种参数形状验证（脚本实算）：

| argc/envc/apple | total | 对齐 |
|---|---|---|
| 1/0/1 | 48 | ✓ |
| 1/0/0 | 48 | ✓ |
| 2/1/1 | 64 | ✓ |
| 0/0/1 | 48 | ✓ |
| 3/0/1 | 64 | ✓ |

内核自己 exec `/sbin/launchd` 的形状是 `1/0/0` → 48 ✓

## 4. 预留与拷贝分离了吗

- 字符串：`string_size`（预留，取整）、`string_copy_size`（实际拷贝）
- 指针区：`ptr_area_size`（预留，补齐）、`ptr_array_bytes`（实际拷贝）
- 拷贝量均用"真实字节数"，缓冲区读不会越界 ✓
  （这一条是第一版漏掉、第二轮审计发现并修掉的：`copyout(ptr_buffer_start, ptr_area, ptr_area_size)`
  会在补齐后越界读最多 16 字节）

## 5. argv 指针计算是否仍然正确

- 指针写入处：`cur_address = string_area + (cur_string - imgp->ip_strings)`
- 字符串仍被拷贝到 `string_area`（预留区低端）→ 偏移基准不变 ✓
- 补齐的空洞位于"数组与字符串之间 / 字符串之上"，不被任何指针引用 ✓

## 6. `p_argslen` 语义变化

`p->p_argslen = *stackp - string_area` 现在包含补齐字节（最多 16）。
影响：`sysctl_procargs` 用它限定返回长度，略微偏大但仍在真实字符串之后，**不会读到未初始化数据之前** ✓
（如需精确可改用 `string_copy_size + ptr_array_bytes`，目前保持最小改动。）

## 7. 结论

修复方案在以上 6 点均无问题；A3 镜像会在真机上直接验证第 3 点（`low` 应为 0）。
