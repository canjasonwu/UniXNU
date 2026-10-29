#!/usr/bin/env python3
"""Replace the cumulative ruler with a binary display, and show the init exit status as bits.

Counting filled squares was ambiguous (a photo reading of "32" where the code implies 36). A
binary display removes counting: six big squares carry the stage index (bit 5..0) and four carry
the init exit status (bit 3..0); lit = 1. Positions and square size are fixed and large.

Implementation notes:
- The frame buffer VA comes from the video console global vinfo (the panic screen uses the same);
  PE_state.video.v_baseAddr is physical and faults with the MMU on (measured earlier).
- fuxi_trace_status() is called from kern_exit once the exit status is known, before any of the
  exit path calls that can hang, so the status bits appear even if a later call blocks.
"""
import re
import subprocess
import sys
from pathlib import Path

UNIXNU = Path('/root/UniXNU')
TREE = UNIXNU / 'out/fuxi/tree/.cache/xnu-work'
STUB = UNIXNU / 'devices/fuxi/platform-power/ShengPlatformPower.cpp'
KERN_EXIT = TREE / 'bsd/kern/kern_exit.c'

BODY = '''/* Stage/status display: bit 5..0 of the stage index, then bit 3..0 of the init exit status.
 * Lit square = 1. No counting needed, which the earlier cumulative ruler required. */
static void
fuxi_draw_bits(unsigned stage, int have_status, unsigned status)
{
	struct fuxi_vc_info *v = &vinfo;
	if (v->v_depth != 32 || v->v_baseaddr == 0 || v->v_rowbytes == 0) {
		return;
	}
	unsigned long size = v->v_height / 26;
	unsigned long step = v->v_width / 11;
	unsigned long y0 = v->v_height * 52 / 100;
	if (y0 + size >= v->v_height) {
		return;
	}
	for (int slot = 0; slot < 10; slot++) {
		unsigned long x0 = 8UL + (unsigned long)slot * step;
		if (x0 + size >= v->v_width) {
			continue;
		}
		unsigned lit;
		if (slot < 6) {
			lit = (stage >> (5 - slot)) & 1U;
		} else if (!have_status) {
			lit = 0;
		} else {
			lit = (status >> (3 - (slot - 6))) & 1U;
		}
		uint32_t colour = lit ? 0xffffffffu : 0xff303030u;
		for (unsigned long y = y0; y < y0 + size; y++) {
			volatile uint32_t *p =
			    (volatile uint32_t *)(v->v_baseaddr + y * v->v_rowbytes + x0 * 4UL);
			for (unsigned long x = 0; x < size; x++) {
				p[x] = colour;
			}
		}
	}
}

extern "C" void
fuxi_trace_mark(int index)
{
	fuxi_draw_bits((unsigned)index, 0, 0);
}

extern "C" void
fuxi_trace_status(int status)
{
	fuxi_draw_bits(99, 1, (unsigned)status & 0xfU);
}
'''


def replace_stub():
    text = STUB.read_text()
    for name in ('fuxi_trace_mark', 'fuxi_trace_status', 'fuxi_draw_bits'):
        at = text.find(name + '(int')
        if at < 0:
            at = text.find(name + '(')
        if at < 0:
            continue
        start = text.rfind('extern "C" void', 0, at)
        if start < 0:
            start = text.rfind('\n', 0, at) + 1
        brace = text.find('{', at)
        depth = 0
        end = brace
        for index in range(brace, len(text)):
            if text[index] == '{':
                depth += 1
            elif text[index] == '}':
                depth -= 1
                if depth == 0:
                    end = index + 1
                    break
        text = text[:start] + text[end:]
    # append the new implementation before the closing of the file's last function namespace
    text = text.rstrip() + '\n\n' + BODY
    STUB.write_text(text)
    print('stub: binary stage/status display installed')


def instrument_kern_exit():
    text = KERN_EXIT.read_text()
    if 'extern void fuxi_trace_status' not in text:
        text = 'extern void fuxi_trace_status(int status);\n' + text
    # show the status as early as possible in the init exit path
    anchor = '\tfuxi_trace_mark(29);\n'
    if anchor in text and 'fuxi_trace_status((int)WEXITSTATUS' not in text:
        text = text.replace(anchor,
                            '\tfuxi_trace_status((int)WEXITSTATUS(rv));\n' + anchor, 1)
    # the old cumulative status encode is superseded
    text = text.replace('\t/* Encode the init exit status on screen: slots 34 + status. */\n'
                        '\tfuxi_trace_mark(34 + (int)(WEXITSTATUS(rv) & 7));\n', '')
    KERN_EXIT.write_text(text)
    print('kern_exit: status bits shown before the exit path')


def build(tag):
    token = Path('/mnt/c/Users/Jason Wu/dsh/fork-add/tools/.gh-token').read_text().strip()
    env = {'PATH': '/opt/unixnu-linux/bin:/usr/bin:/bin', 'HOME': '/root', 'GITHUB_TOKEN': token}
    for stale in (UNIXNU / 'out/fuxi/kernel/kernel.macho',
                  UNIXNU / 'out/fuxi/payload/payload.img',
                  UNIXNU / 'out/fuxi/boot/boot.img'):
        stale.unlink(missing_ok=True)
    log = Path('/root/build-%s.log' % tag)
    with log.open('w') as handle:
        result = subprocess.run(['bash', str(UNIXNU / 'devices/fuxi/scripts/local-build.sh')],
                                stdout=handle, stderr=subprocess.STDOUT, env=env)
    image = UNIXNU / 'out/fuxi/boot/boot.img'
    if result.returncode != 0 or not image.is_file():
        print('BUILD %s FAILED' % tag)
        print('\n'.join(log.read_text().splitlines()[-3:]))
        return False
    target = Path('/mnt/c/Users/Jason Wu/dsh/artifacts-%s' % tag)
    target.mkdir(parents=True, exist_ok=True)
    subprocess.run(['cp', str(image), str(target / 'boot.img')], check=True)
    print('BUILT %s: %s' % (tag, subprocess.run(['sha256sum', str(image)], capture_output=True,
                                                text=True).stdout.strip()))
    return True


if __name__ == '__main__':
    replace_stub()
    instrument_kern_exit()
    sys.exit(0 if build('BITS1') else 1)
