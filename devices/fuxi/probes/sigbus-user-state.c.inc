/*
 * fuxi SIGBUS 路径探针（用户态寄存器版）
 *
 * 位置：bsd/kern/kern_sig.c 的 psignal() 开头
 * 目的：内核一投 SIGBUS 就把"哪个 pid、谁投的、用户态 pc/far/sp"打到屏幕上。
 *       有 pc 就能直接定位故障指令，有 far 就能判断是取指、读还是写失败。
 *
 * 依赖：kern_sig.c 需要 #include <mach/arm/thread_status.h>（提供 get_saved_state_* 内联函数），
 *       以及 extern struct arm_saved_state *get_user_regs(thread_t);（osfmk/arm/thread.h 声明）。
 *       注意 get_saved_state_* 是 static inline，**不要**再写 extern 声明，否则编译报错。
 *
 * 判读：
 *   pc 落在用户代码段（0x100000xxx）→ 取指或首条指令执行阶段出问题
 *   far 与 pc 接近            → 取指保护故障（代码页不可执行）
 *   far 在栈范围              → 栈映射/写保护问题
 *   far 在文件映射范围        → 文件页读取失败（HFS 页入）
 */

	if (signum == SIGBUS) {
		extern struct arm_saved_state *get_user_regs(thread_t);
		struct arm_saved_state *state = get_user_regs(current_thread());
		register_t pc = state ? get_saved_state_pc(state) : 0;
		register_t far = state ? get_saved_state_far(state) : 0;
		register_t sp = state ? get_saved_state_sp(state) : 0;
		fuxi_trace_mark(46);
		panic("fuxi-sigbus: caller=%p pid=%d pc=%llx far=%llx sp=%llx",
		    __builtin_return_address(0), proc_getpid(p),
		    (unsigned long long)pc, (unsigned long long)far, (unsigned long long)sp);
	}
