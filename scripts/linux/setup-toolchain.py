#!/usr/bin/env python3
"""Install the toolchain the kernel step uses on a Linux host. Run once, as root:

    sudo scripts/linux/setup-toolchain.py [PREFIX]      (default /opt/unixnu-linux, or $UNIXNU_TOOLCHAIN)

It builds clang with __builtin_xnu_type_* (llvm-xnu-builtin-type.patch; xnu's kalloc_type signatures have no fallback
without them) into PREFIX/llvm, cctools-port 1030.6.3-ld64-956.6 (ld64, libtool, nm, lipo; its master, ld64-609,
corrupts the kernel link), mig from bootstrap_cmds, iig-port as iig, the host compiler wrapper for xnu's SETUP tools,
and lzfse for the kernelcache, and installs the shims of this directory. The Command Line Tools and Xcode toolchain
layouts that xnu's makefiles expect are under PREFIX.

Outside PREFIX it writes what xnu's makefiles and the host linker look for at fixed paths: the xcrun, plutil and
codesign shims in /usr/bin, libmacho.a and libstuff.a in /usr/lib, libdispatch (which ld64 needs) in /usr/local, and
the apt.llvm.org source list. Each step is skipped when its output exists.
"""
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
LLVM_VERSION = '24'
LLVM_COMMIT = '58c46bae21181fb4240cf964461bea738d96d50e'
CCTOOLS_PORT_REF = '1030.6.3-ld64-956.6'
LIBDISPATCH_REF = 'swift-6.4.0-RELEASE'
BOOTSTRAP_CMDS_TAG = 'bootstrap_cmds-138'
IIG_PORT_COMMIT = 'ea6d147d668ca15d490da638012243fcd705f30d'
LZFSE_COMMIT = 'e634ca58b4821d9f3d560cdc6df5dec02ffc93fd'
CLT_SHIMS = ('clang', 'clang++', 'cc', 'install', 'gm4')
USR_BIN_SHIMS = ('xcrun', 'plutil', 'codesign')
PACKAGES = ['build-essential', 'git', 'patch', 'pkg-config', 'ninja-build', 'flex', 'bison', 'python3', 'python3-dev',
            'xz-utils', 'bzip2', 'cpio', 'rsync', 'curl', 'wget', 'ca-certificates', 'gnupg', 'lsb-release',
            'libssl-dev', 'zlib1g-dev', 'tcsh', 'unifdef', 'cmake', 'libbsd-dev', 'uuid-dev']
XNU_BUILTIN_CHECK = '#if __has_builtin(__builtin_xnu_type_summary)\nok\n#endif\n'

# Darwin names glibc lacks, for xnu's SETUP host tools (installfile uses setmode/getmode, getprogname).
HOST_PREFIX_H = '''/* Darwin-isms xnu's SETUP host tools use that glibc does not declare. */
#ifndef _UNIXNU_HOST_PREFIX_H_
#define _UNIXNU_HOST_PREFIX_H_
#include <bsd/unistd.h>   /* setmode(3)/getmode(3), used by SETUP/installfile */
static inline const char *getprogname(void)
{
	extern char *program_invocation_short_name;
	return program_invocation_short_name;
}
#endif
'''

# SETUP/installfile makes exactly one call into <copyfile.h>: fcopyfile(src, dst, NULL, COPYFILE_DATA).
COPYFILE_H = '''/* <copyfile.h> for xnu's SETUP/installfile.c, which uses exactly one call,
 * fcopyfile(src, dst, NULL, COPYFILE_DATA). */
#ifndef _UNIXNU_COPYFILE_H_
#define _UNIXNU_COPYFILE_H_
#include <unistd.h>
#include <errno.h>
#include <sys/types.h>

typedef void *copyfile_state_t;
#define COPYFILE_DATA  (1 << 3)
#define COPYFILE_ALL   COPYFILE_DATA

static inline int
fcopyfile(int from_fd, int to_fd, copyfile_state_t state, unsigned int flags)
{
	char buf[65536];
	ssize_t n;
	(void)state; (void)flags;
	if (lseek(from_fd, 0, SEEK_SET) == (off_t)-1 && errno != ESPIPE) {
		return -1;
	}
	while ((n = read(from_fd, buf, sizeof(buf))) > 0) {
		ssize_t off = 0;
		while (off < n) {
			ssize_t w = write(to_fd, buf + off, (size_t)(n - off));
			if (w < 0) {
				if (errno == EINTR) {
					continue;
				}
				return -1;
			}
			off += w;
		}
	}
	return n < 0 ? -1 : 0;
}
#endif
'''

# The cross clang is built for AArch64 only, so xnu's SETUP tools (built for this machine) go through the
# distribution's clang. --ld-path: clang looks for "ld" next to itself first, and the Command Line Tools bin holds
# cctools' ld64; host tools link with the system ELF ld. A link (no -c) also gets cctools' libmacho and libstuff
# (the Mach-O arch table and byte swappers kextsymboltool and setsegname call; libmacho first, it calls into
# libstuff), libuuid and libbsd, all on the host linker's default path; xnu's SETUP makefiles set LDFLAGS literally.
HOST_CC = '''#!/bin/sh
# xnu's HOST_CC on Linux: the distribution's clang with the headers and libraries xnu's SETUP tools need.
libs=""
compile_only=0
for a in "$@"; do [ "$a" = "-c" ] && compile_only=1; done
[ "$compile_only" = 0 ] && libs="-lmacho -lstuff -luuid -lbsd"
exec "{clang}" \\
    --ld-path=/usr/bin/ld {arch_defs} \\
    -include "{include}/unixnu-host-prefix.h" \\
    -idirafter "{include}" \\
    -idirafter "{include}/foreign" "$@" $libs
'''


def run(command, cwd=None, env=None, input=None):
    command = [str(part) for part in command]
    print('+ ' + ' '.join(command), flush=True)
    return subprocess.run(command, cwd=cwd, env=env, input=input, check=True, text=True,
                          stdout=subprocess.PIPE if input is not None else None).stdout


def git_checkout(directory, url, ref):
    """One commit (or tag) of a repository, without its history."""
    directory.mkdir(parents=True, exist_ok=True)
    run(['git', 'init', '-q', directory])
    run(['git', '-C', directory, 'fetch', '-q', '--depth', '1', url, ref])
    run(['git', '-C', directory, 'checkout', '-q', '--force', 'FETCH_HEAD'])


def has_xnu_builtins(command, env=None):
    out = run([*command, '-E', '-'], env=env, input=XNU_BUILTIN_CHECK)
    return any(line.strip() == 'ok' for line in out.splitlines())


def apt():
    """Build tools, the host packages xnu's own build uses, and apt.llvm.org's clang, lld and libclang."""
    env = dict(os.environ, DEBIAN_FRONTEND='noninteractive')
    run(['apt-get', 'update'], env=env)
    run(['apt-get', 'install', '-y', '--no-install-recommends', *PACKAGES], env=env)
    key = subprocess.run(['curl', '-fsSL', 'https://apt.llvm.org/llvm-snapshot.gpg.key'], check=True,
                         capture_output=True).stdout
    subprocess.run(['gpg', '--dearmor', '--yes', '-o', '/usr/share/keyrings/llvm-archive-keyring.gpg'],
                   input=key, check=True)
    codename = platform.freedesktop_os_release()['VERSION_CODENAME']
    suite = f'llvm-toolchain-{codename}-{LLVM_VERSION}'
    if subprocess.run(['curl', '-fsI', f'https://apt.llvm.org/{codename}/dists/{suite}/Release'],
                      capture_output=True).returncode:
        suite = f'llvm-toolchain-{codename}'
    Path('/etc/apt/sources.list.d/llvm.list').write_text(
        f'deb [signed-by=/usr/share/keyrings/llvm-archive-keyring.gpg] https://apt.llvm.org/{codename}/ {suite} main\n')
    run(['apt-get', 'update'], env=env)
    run(['apt-get', 'install', '-y', '--no-install-recommends', f'clang-{LLVM_VERSION}', f'lld-{LLVM_VERSION}',
         f'llvm-{LLVM_VERSION}', f'libclang-{LLVM_VERSION}-dev'], env=env)


def llvm(prefix, work, jobs):
    """clang with llvm-xnu-builtin-type.patch, for AArch64 only."""
    clang = prefix / 'llvm/bin/clang'
    if not clang.exists():
        src = work / 'llvm-project'
        git_checkout(src, 'https://github.com/llvm/llvm-project', LLVM_COMMIT)
        run(['git', '-C', src, 'apply', HERE / 'llvm-xnu-builtin-type.patch'])
        run(['cmake', '-G', 'Ninja', '-S', src / 'llvm', '-B', src / 'build',
             '-DLLVM_ENABLE_PROJECTS=clang', '-DLLVM_TARGETS_TO_BUILD=AArch64',
             '-DCMAKE_BUILD_TYPE=Release', '-DLLVM_ENABLE_ASSERTIONS=ON',
             '-DLLVM_INCLUDE_TESTS=OFF', '-DLLVM_INCLUDE_EXAMPLES=OFF', '-DLLVM_INCLUDE_BENCHMARKS=OFF',
             '-DCLANG_ENABLE_STATIC_ANALYZER=OFF', '-DCLANG_ENABLE_ARCMT=OFF', '-DLLVM_PARALLEL_LINK_JOBS=1',
             f'-DCMAKE_C_COMPILER=clang-{LLVM_VERSION}', f'-DCMAKE_CXX_COMPILER=clang++-{LLVM_VERSION}',
             f'-DCMAKE_INSTALL_PREFIX={prefix / "llvm"}'])
        run(['ninja', '-C', src / 'build', f'-j{jobs}', 'install-clang', 'install-clang-resource-headers'])
        shutil.rmtree(src)
    if not has_xnu_builtins([clang, '-target', 'arm64e-apple-macos26.0']):
        sys.exit(f'{clang} has no __builtin_xnu_type_summary')
    return clang


def shims(clt, xctoolchain, clang):
    (clt / 'bin').mkdir(parents=True, exist_ok=True)
    for name, directory in [(n, clt / 'bin') for n in CLT_SHIMS] + [(n, Path('/usr/bin')) for n in USR_BIN_SHIMS]:
        shutil.copyfile(HERE / name, directory / name)
        (directory / name).chmod(0o755)
    env = dict(os.environ, CROSS_CLANG=str(clang), XNU_TOOLCHAIN_BIN=f'{clt / "bin"} {xctoolchain / "bin"}')
    found = run(['xcrun', '-find', 'clang'], env=env, input='').strip()
    if found != str(clt / 'bin/clang'):
        sys.exit(f'xcrun -find clang gives {found}, not {clt / "bin/clang"}')
    if not has_xnu_builtins(['xcrun', 'clang'], env):
        sys.exit('xcrun clang has no __builtin_xnu_type_summary')


def cctools(clt, include, work, jobs):
    """ld64 that links a kernel Mach-O (ld64.lld rejects -kernel), the Mach-O tools, and the Darwin <mach-o/*.h>,
    <mach/*.h> and libmacho that xnu's SETUP tools use. libtapi is not built: a kernel links from objects and
    archives only."""
    if (clt / 'bin/ld').exists():
        return
    env = dict(os.environ, CC=f'clang-{LLVM_VERSION}', CXX=f'clang++-{LLVM_VERSION}')
    src = work / 'cctools-port'
    git_checkout(src, 'https://github.com/tpoechtrager/cctools-port.git', CCTOOLS_PORT_REF)
    # ld64 from the 1009 branch on needs libdispatch and its blocks runtime, which Ubuntu does not package; cctools'
    # configure finds it in /usr/local.
    if not Path('/usr/local/lib/libdispatch.so').exists():
        dispatch = work / 'swift-corelibs-libdispatch'
        git_checkout(dispatch, 'https://github.com/swiftlang/swift-corelibs-libdispatch.git', LIBDISPATCH_REF)
        run(['cmake', '-G', 'Ninja', '-S', dispatch, '-B', dispatch / 'build', '-DCMAKE_BUILD_TYPE=Release',
             '-DCMAKE_C_FLAGS=-Wno-error=unused-but-set-global'], env=env)
        run(['ninja', '-C', dispatch / 'build', f'-j{jobs}'], env=env)
        run(['ninja', '-C', dispatch / 'build', 'install'], env=env)
        run(['ldconfig'])   # the loader finds /usr/local/lib only through ld.so.cache
    tree = src / 'cctools'
    # --disable-lto-support: the kernel link uses no LTO, and libstuff/lto.c needs LLVM headers cctools-port does not
    # ship. --target sets the default -arch; --program-prefix= and PROGRAM_PREFIX= keep the plain names (ld, lipo,
    # otool) that xcrun is asked for and that the tools use to run each other.
    run(['./configure', f'--prefix={clt}', '--target=arm64-apple-darwin', '--program-prefix=',
         '--disable-lto-support'], cwd=tree, env=env)
    run(['make', f'-j{jobs}', 'PROGRAM_PREFIX='], cwd=tree, env=env)
    run(['make', 'install', 'PROGRAM_PREFIX='], cwd=tree, env=env)
    # The headers xnu's SETUP tools include (<libc.h>, <mach-o/arch.h>, <mach-o/swap.h>, <mach/mach_init.h>); the
    # host compiler wrapper adds them with -idirafter, after glibc's.
    include.mkdir(parents=True, exist_ok=True)
    shutil.copytree(tree / 'include', include, dirs_exist_ok=True)
    # libmacho (the arch table) and libstuff (the byte swappers, and the host_info() stand-in libmacho calls), which
    # cctools-port builds but does not install. /usr/lib is on the host linker's default path, as libmacho is part
    # of libSystem on macOS.
    for name in ('libmacho/.libs/libmacho.a', 'libstuff/.libs/libstuff.a'):
        shutil.copy2(tree / name, '/usr/lib/')


def mig(clt, include, work):
    """mig and migcom from bootstrap_cmds, built with make-free compiler calls: the Xcode project's only work is the
    yacc/lex C program migcom.tproj and the mig.sh driver, which finds migcom as ../libexec/migcom."""
    if (clt / 'libexec/migcom').exists():
        return
    if not (include / 'foreign/mach/message.h').exists():
        sys.exit(f'no {include}/foreign/mach/message.h: cctools first')
    src = work / 'bootstrap_cmds'
    git_checkout(src, 'https://github.com/apple-oss-distributions/bootstrap_cmds.git', BOOTSTRAP_CMDS_TAG)
    tproj = src / 'migcom.tproj'
    run(['bison', '-y', '-d', 'parser.y'], cwd=tproj)      # y.tab.h is the name lexxer.l includes
    run(['flex', '-o', 'lexxer.c', 'lexxer.l'], cwd=tproj)
    # handler.c is dead code the Xcode target does not compile. The <mach/*.h> headers come from cctools-port's
    # include/foreign after glibc (-idirafter), so glibc keeps every name it has. -Ulinux takes type.h's macOS branch
    # (<sys/types.h>, for u_int); foreign/mach/machine/*.h select on __arm64__, which arm64 Linux clang does not define.
    sources = ['error.c', 'global.c', 'header.c', 'lexxer.c', 'y.tab.c', 'routine.c', 'server.c', 'statement.c',
               'string.c', 'type.c', 'user.c', 'utils.c', 'mig.c']
    arch = ['-D__arm64__=1'] if platform.machine() == 'aarch64' else []
    (clt / 'libexec').mkdir(parents=True, exist_ok=True)
    run([f'clang-{LLVM_VERSION}', *arch, '-Ulinux', f'-DMIG_VERSION="{BOOTSTRAP_CMDS_TAG}"',
         '-idirafter', include / 'foreign', '-o', clt / 'libexec/migcom', *sources], cwd=tproj)
    shutil.copyfile(tproj / 'mig.sh', clt / 'bin/mig')
    (clt / 'bin/mig').chmod(0o755)


def iig(xctoolchain, work):
    """iig-port, an open implementation of the DriverKit interface generator, where xcrun looks for iig. The patch
    defaults libclang to a Darwin target and finds libclang's builtin headers."""
    if (xctoolchain / 'bin/iig').exists():
        return
    llvm_config = shutil.which(f'llvm-config-{LLVM_VERSION}') or shutil.which('llvm-config')
    if not llvm_config:
        sys.exit('no llvm-config')
    src = work / 'iig-port'
    git_checkout(src, 'https://github.com/Nyktis/iig-port', IIG_PORT_COMMIT)
    run(['patch', '-d', src, '-p1', '--batch', '--forward', '-i', HERE / 'iig-port-darwin-target.patch'])
    flags = run([llvm_config, '--cxxflags', '--ldflags'], input='').split()
    bindir = run([llvm_config, '--bindir'], input='').strip()
    (xctoolchain / 'bin').mkdir(parents=True, exist_ok=True)
    # libclang-N.so.N is in the multiarch libdir the loader searches, so no rpath.
    run([f'{bindir}/clang++', '-std=c++17', *flags, src / 'iig.cpp', '-lclang', '-o', xctoolchain / 'bin/iig'])


def host_cc(clt, include):
    """xnu-host-cc, xnu's HOST_CC: SETUP tools built for this machine with the distribution's clang."""
    include.mkdir(parents=True, exist_ok=True)
    (include / 'unixnu-host-prefix.h').write_text(HOST_PREFIX_H)
    (include / 'copyfile.h').write_text(COPYFILE_H)
    clangs = sorted(Path('/usr/lib').glob('llvm-*/bin/clang'), key=lambda p: int(p.parts[-3].split('-')[1]))
    clang = str(clangs[-1]) if clangs else shutil.which('cc') or '/usr/bin/cc'
    # cctools' foreign/machine/*.h select on __arm64__; arm64 Linux also makes plain char unsigned, and
    # SETUP/decomment compares a char from getc() to EOF.
    arch = '-D__arm64__ -fsigned-char' if platform.machine() == 'aarch64' else ''
    wrapper = clt / 'bin/xnu-host-cc'
    wrapper.write_text(HOST_CC.format(clang=clang, arch_defs=arch, include=include))
    wrapper.chmod(0o755)


def lzfse(prefix, work):
    """Apple's reference LZFSE tool, which decodes the IPSW kernelcache (compression_tool on macOS)."""
    if (prefix / 'bin/lzfse').exists():
        return
    src = work / 'lzfse'
    git_checkout(src, 'https://github.com/lzfse/lzfse', LZFSE_COMMIT)
    run(['make', '-C', src, 'install', f'INSTALL_PREFIX={prefix}', 'CC=cc'])


def main():
    if os.geteuid():
        sys.exit('setup-toolchain.py: run as root')
    prefix = Path(sys.argv[1] if len(sys.argv) > 1 else os.environ.get('UNIXNU_TOOLCHAIN', '/opt/unixnu-linux')).resolve()
    clt, xctoolchain = prefix / 'CommandLineTools/usr', prefix / 'XcodeDefault.xctoolchain/usr'
    include = clt.parent / 'SDKs/MacOSX.sdk/usr/include'
    jobs = os.cpu_count() or 4
    prefix.mkdir(parents=True, exist_ok=True)
    os.environ['PATH'] = f'/usr/lib/llvm-{LLVM_VERSION}/bin' + os.pathsep + os.environ.get('PATH', '')
    apt()
    # The llvm-project build tree takes several GB: build on disk ($TMPDIR, else /var/tmp), not in a tmpfs /tmp.
    with tempfile.TemporaryDirectory(prefix='unixnu-toolchain-', dir=os.environ.get('TMPDIR', '/var/tmp')) as tmp:
        work = Path(tmp)
        clang = llvm(prefix, work, jobs)
        shims(clt, xctoolchain, clang)
        cctools(clt, include, work, jobs)
        mig(clt, include, work)
        iig(xctoolchain, work)
        host_cc(clt, include)
        lzfse(prefix, work)
    for tool in (clt / 'bin/ld', clt / 'bin/libtool', clt / 'libexec/migcom', xctoolchain / 'bin/iig',
                 clt / 'bin/xnu-host-cc', prefix / 'bin/lzfse'):
        if not tool.exists():
            sys.exit(f'{tool} missing')
    print(f'toolchain ready in {prefix}')


if __name__ == '__main__':
    main()
