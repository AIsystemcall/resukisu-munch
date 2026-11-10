#!/usr/bin/env bash
# This file is tri-licensed under GPLv2, Apache 2.0, or the SQLite Blessing.
# You may choose any of these licenses to use this code.
#
# In place of a legal notice, here is a blessing:
#  - May you do good and not evil.
#  - May you find forgiveness for yourself and forgive others.
#  - May you share freely, never taking more than you give.
#
# SPDX-License-Identifier: GPL-2.0-only OR Apache-2.0 OR blessing

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKDIR="${WORKDIR:-$ROOT_DIR/work}"
CLANG_PREBUILT_DIR="$WORKDIR/aosp-clang-ref"
REF_VARIANT="${REF_VARIANT:-strict-susfs}"
REF_KERNEL_REPO="${REF_KERNEL_REPO:-https://github.com/liyafe1997/kernel_xiaomi_sm8250_mod.git}"
REF_KERNEL_REF="${REF_KERNEL_REF:-android15-lineage22-mod}"
RESUKISU_REPO="${RESUKISU_REPO:-https://github.com/Baka-SU/BakaSU.git}"
RESUKISU_REF="${RESUKISU_REF:-main}"
# Version lock: installed manager is CI 35257, built from BakaSU main
# 516d3e87fbc986ccefdde2c2765f1700612441a9. Refuse to build if upstream main moved, otherwise the
# driver/manager pair would silently mismatch (feature switches lost).
RESUKISU_PIN="${RESUKISU_PIN:-516d3e87fbc986ccefdde2c2765f1700612441a9}"
NONGKI_REPO="${NONGKI_REPO:-https://github.com/JackA1ltman/NonGKI_Kernel_Build_2nd.git}"
NONGKI_REF="${NONGKI_REF:-mainline}"
NONGKI_DIR="$WORKDIR/non-gki-ref-build"
ANYKERNEL_REPO="${ANYKERNEL_REPO:-https://github.com/osm0sis/AnyKernel3.git}"
AOSP_CLANG_REPO="${AOSP_CLANG_REPO:-https://android.googlesource.com/platform/prebuilts/clang/host/linux-x86}"
AOSP_CLANG_REF="${AOSP_CLANG_REF:-android-msm-redbull-4.19-android12-qpr3}"
AOSP_CLANG_VERSION="${AOSP_CLANG_VERSION:-clang-r399163b}"
USE_AOSP_CLANG="${USE_AOSP_CLANG:-0}"
ARCH="${ARCH:-arm64}"
TARGET_DEVICE="${TARGET_DEVICE:-munch}"
CROSS_COMPILE="${CROSS_COMPILE:-aarch64-linux-gnu-}"
CROSS_COMPILE_ARM32="${CROSS_COMPILE_ARM32:-arm-linux-gnueabi-}"
CLANG_TRIPLE="${CLANG_TRIPLE:-aarch64-linux-gnu-}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
JOBS="${JOBS:-$(nproc)}"

case "$REF_VARIANT" in
  strict-susfs)
    DEFAULT_ARTIFACT_DIR="$WORKDIR/artifacts-ref"
    DEFAULT_KERNEL_DIR="$WORKDIR/ref-kernel-sm8250-build"
    DEFAULT_ANYKERNEL_DIR="$WORKDIR/AnyKernel3-ref"
    DEFAULT_OUT_DIR="$WORKDIR/ref-out"
    DEFAULT_ZIP_NAME="munch-miui14-resukisu-ref-strict-experimental.zip"
    ARTIFACT_TRACK="ref"
    BUILD_TRACK="reference-strict-experimental"
    SUSFS_LABEL="strict-v2.3.0"
    SUSFS_VERSION_LABEL="v2.3.0"
    KERNEL_STRING="ReSukiSU munch MIUI14 reference SUSFS v2.3.0"
    PACKAGE_COMMENT="reference-kernel package for Redmi K40S / munch MIUI14 with upstream SUSFS v2.3.0"
    ;;
  nosusfs)
    DEFAULT_ARTIFACT_DIR="$WORKDIR/artifacts-ref-nosusfs"
    DEFAULT_KERNEL_DIR="$WORKDIR/ref-kernel-sm8250-nosusfs-build"
    DEFAULT_ANYKERNEL_DIR="$WORKDIR/AnyKernel3-ref-nosusfs"
    DEFAULT_OUT_DIR="$WORKDIR/ref-nosusfs-out"
    DEFAULT_ZIP_NAME="munch-miui14-resukisu-ref-nosusfs-experimental.zip"
    ARTIFACT_TRACK="ref-nosusfs"
    BUILD_TRACK="reference-nosusfs-experimental"
    SUSFS_LABEL="disabled"
    SUSFS_VERSION_LABEL="none"
    KERNEL_STRING="ReSukiSU munch MIUI14 reference no-SUSFS experimental"
    PACKAGE_COMMENT="no-SUSFS experimental reference-kernel package for Redmi K40S / munch MIUI14"
    ;;
  *)
    echo "unknown REF_VARIANT: $REF_VARIANT" >&2
    exit 1
    ;;
esac

ARTIFACT_DIR="${ARTIFACT_DIR:-$DEFAULT_ARTIFACT_DIR}"
KERNEL_DIR="${KERNEL_DIR:-$DEFAULT_KERNEL_DIR}"
ANYKERNEL_DIR="${ANYKERNEL_DIR:-$DEFAULT_ANYKERNEL_DIR}"
OUT_DIR="${OUT_DIR:-$DEFAULT_OUT_DIR}"
ZIP_NAME="${ZIP_NAME:-$DEFAULT_ZIP_NAME}"

mkdir -p "$WORKDIR"
rm -rf "$KERNEL_DIR" "$ANYKERNEL_DIR" "$ARTIFACT_DIR" "$OUT_DIR" "$NONGKI_DIR"
mkdir -p "$ARTIFACT_DIR"

if [[ "$USE_AOSP_CLANG" == "1" ]]; then
  rm -rf "$CLANG_PREBUILT_DIR"
  git clone --depth 1 --branch "$AOSP_CLANG_REF" "$AOSP_CLANG_REPO" "$CLANG_PREBUILT_DIR"
  export PATH="$CLANG_PREBUILT_DIR/$AOSP_CLANG_VERSION/bin:$PATH"
fi

git clone --depth 1 --branch "$REF_KERNEL_REF" "$REF_KERNEL_REPO" "$KERNEL_DIR"
# RESUKISU_REF may be a branch/tag name, or an exact commit SHA when the
# installed manager is pinned to an older CI build. git clone --branch does
# not accept a raw SHA, so use init + fetch-by-SHA in that case. This keeps
# the driver/manager pair byte-for-byte matched even after upstream main
# advances (GitHub supports reachable-SHA shallow fetch).
if [[ "$RESUKISU_REF" =~ ^[0-9a-f]{40}$ ]]; then
  git init -q "$KERNEL_DIR/KernelSU"
  git -C "$KERNEL_DIR/KernelSU" remote add origin "$RESUKISU_REPO"
  git -C "$KERNEL_DIR/KernelSU" fetch --depth 1 origin "$RESUKISU_REF"
  git -C "$KERNEL_DIR/KernelSU" checkout --detach FETCH_HEAD
else
  git clone --depth 1 --branch "$RESUKISU_REF" "$RESUKISU_REPO" "$KERNEL_DIR/KernelSU"
fi
if [[ -n "$RESUKISU_PIN" ]]; then
  ksu_head="$(git -C "$KERNEL_DIR/KernelSU" rev-parse HEAD)"
  if [[ "$ksu_head" != "$RESUKISU_PIN" ]]; then
    echo "FATAL: BakaSU main advanced to $ksu_head, but the installed manager is CI 35257" >&2
    echo "       (pinned $RESUKISU_PIN). Refusing to build a mismatched kernel." >&2
    echo "       Upgrade the manager to the matching CI build, then update RESUKISU_PIN." >&2
    exit 1
  fi
  echo "KSU version lock OK: $ksu_head (manager 35257)"
fi
# No NonGKI clone: this tree ships legacy KernelSU-Next hooks, so the
# NonGKI inline-hook script skips every file (it assumes "already patched").
# The tree-specific ref-resukisu-inline-hooks.patch below is the correct port.

pushd "$KERNEL_DIR" >/dev/null
bash KernelSU/kernel/setup.sh "$RESUKISU_REF"
# setup.sh performs its own checkout using RESUKISU_REF; re-assert that the
# final tree is still the exact commit matching the installed manager.
if [[ -n "$RESUKISU_PIN" ]]; then
  ksu_head_after="$(git -C "$KERNEL_DIR/KernelSU" rev-parse HEAD)"
  if [[ "$ksu_head_after" != "$RESUKISU_PIN" ]]; then
    echo "FATAL: setup.sh moved KernelSU to $ksu_head_after, expected $RESUKISU_PIN" >&2
    exit 1
  fi
  echo "KSU version lock re-asserted after setup.sh: $ksu_head_after (manager 35257)"
fi

case "$REF_VARIANT" in
  strict-susfs)
    # This tree (android15-lineage22-mod) already ships SUSFS v1.5.7, so the
    # upstream "new file" v2.3.00 patch cannot be applied directly. Use the
    # tree-specific differential patch: v1.5.7 hooks are removed and replaced
    # with v2.3.0, while the CFI/ThinLTO kallsyms change from later merges is
    # preserved. Verified offline with `git apply --check` against branch HEAD.
    grep -q 'SUSFS_VERSION "v1.5.7"' include/linux/susfs.h || {
      echo "FATAL: kernel tree is not the expected v1.5.7 baseline; patch will not apply" >&2
      exit 1
    }
    git apply --verbose "$ROOT_DIR/patches/ref-susfs-v230-strict.patch"

    grep -q 'SUSFS_VERSION "v2.3.0"' include/linux/susfs.h || {
      echo "FATAL: SUSFS v2.3.0 was not installed" >&2
      exit 1
    }
    ! grep -rq 'INODE_STATE_SUS_PATH' fs/ || {
      echo "FATAL: stale SUSFS v1.5.7 hooks survived the patch" >&2
      exit 1
    }

    # Upstream v2.3.00 adds extern declarations to fs/stat.c but forgets to
    # include susfs_def.h. STATX_SUS_KSTAT / STATX_SUS_KSTAT_FUSE and
    # susfs_is_current_app_uid() live there; the 157 tree got them indirectly,
    # this tree does not (CI #2 failed with 11 undeclared-identifier errors).
    python3 - <<'PY'
p = 'fs/stat.c'
s = open(p).read()
needle = '#ifdef CONFIG_KSU_SUSFS_SUS_KSTAT\nextern bool susfs_is_inode_sus_kstat'
if '#include <linux/susfs_def.h>' not in s:
    assert needle in s, 'fs/stat.c SUS_KSTAT block not found'
    s = s.replace(needle,
                  '#ifdef CONFIG_KSU_SUSFS_SUS_KSTAT\n#include <linux/susfs_def.h>\nextern bool susfs_is_inode_sus_kstat', 1)
    open(p, 'w').write(s)
    print('fs/stat.c: added linux/susfs_def.h include')
PY

    # 4.19 has no inotify_mark_user_mask(); the v2.3.00 patch calls the 5.x
    # helper. Do a context-free text fix instead of a hunk-based git apply so
    # it survives line-number drift on this tree.
    python3 - <<'PY'
p = 'fs/notify/fdinfo.c'
s = open(p).read()
n = s.count('inotify_mark_user_mask(mark)')
s = s.replace('inotify_mark_user_mask(mark)', 'mask')
decl = '\t\tu32 mask = mark->mask & IN_ALL_EVENTS;\n'
if decl not in s:
    s = s.replace('\tif (inode) {', '\tif (inode) {\n' + decl, 1)
open(p, 'w').write(s)
print('fdinfo 4.19 compat: replaced %d helper call(s)' % n)
PY

    # ReSukiSU 4.2 dropped the legacy KernelSU-Next bool switches
    # (ksu_vfs_read_hook / ksu_execveat_hook / ksu_input_hook) and its
    # kernel/tools/inline_hook_check.mk aborts the build with
    # "You should integrate ReSukiSU in your kernel correctly." if they survive
    # (CI #3, 9m05s). The 325 tree still carries them because the NonGKI
    # inline-hook script saw the old ksu_handle_* calls and skipped every file.
    # Apply the tree-specific ReSukiSU 4.2 hook port from the reference track:
    # it converts the hooks to CONFIG_KSU_SUSFS direct calls and adds the
    # setresuid / reboot hooks required by the inline hook checker.
    git apply "$ROOT_DIR/patches/ref-resukisu-inline-hooks.patch"

    # CI #4 booted into a kernel panic (pstore: ksu_handle_faccessat+0x68,
    # "Unable to handle kernel paging request at 002f0074696e692f" =
    # ASCII "/init" dereferenced as struct filename::name).
    # ReSukiSU v4.2.0-rc3 compiles ksu_handle_faccessat / ksu_handle_stat
    # with `struct filename **` under CONFIG_KSU_SUSFS, while the NonGKI
    # SUSFS v2.3.0 file set still wires them with `const char __user **`.
    # Port the two 4.19 call sites the same way current upstream NonGKI
    # susfs_inline_hook_patches.sh does: getname_flags() + filename_lookup(),
    # guarded by susfs_is_current_proc_no_su() / ksu_su_compat_enabled /
    # __ksu_is_allow_uid_for_current(). SUSFS v2.3.0 itself is untouched.
    python3 - <<'PYPORT'
#!/usr/bin/env python3
"""Port the 4.19 faccessat/stat hook sites to the ReSukiSU 4.2 SUSFS API.

ReSukiSU v4.2.0-rc3 (and main) compile `ksu_handle_faccessat` /
`ksu_handle_stat` with `struct filename **` when CONFIG_KSU_SUSFS=y.  The
NonGKI SUSFS v2.3.0 file set still wires those two hooks with the old
`const char __user **` signature, which makes the driver read the first eight
bytes of the pathname as `struct filename::name` -> data abort on
kernel_init_freeable()'s init_eaccess("/init").

This mirrors the current upstream NonGKI `susfs_inline_hook_patches.sh`:
  * make fs/namei.c::filename_lookup() non-static;
  * build a `struct filename *` with getname_flags() in do_faccessat()
    and vfs_statx();
  * call the hooks with `struct filename **` guarded by
    susfs_is_current_proc_no_su() / ksu_su_compat_enabled /
    __ksu_is_allow_uid_for_current();
  * resolve the path with filename_lookup(), which consumes fname via
    putname().

Only these three files are touched; the SUSFS v2.3.0 implementation itself
is left byte-for-byte as generated by ref-susfs-v230-strict.patch.
"""
import os
import re
import sys

OPEN_EXTERN = """#ifdef CONFIG_KSU_SUSFS
#include <linux/susfs_def.h>
#include <linux/jump_label.h>
extern struct static_key_true ksu_su_compat_enabled;
extern bool __ksu_is_allow_uid_for_current(uid_t uid);
extern int ksu_handle_faccessat(int *dfd, struct filename **filename, int *mode,
			 int *flags);
extern int filename_lookup(int dfd, struct filename *name, unsigned flags,
			   struct path *path, struct path *root);
#endif"""

STAT_EXTERN = """#ifdef CONFIG_KSU_SUSFS
#include <linux/jump_label.h>
extern struct static_key_true ksu_su_compat_enabled;
extern bool __ksu_is_allow_uid_for_current(uid_t uid);
extern int ksu_handle_stat(int *dfd, struct filename **filename, int *flags);
extern int filename_lookup(int dfd, struct filename *name, unsigned flags,
			   struct path *path, struct path *root);
#endif"""

OPEN_BODY = """#ifdef CONFIG_KSU_SUSFS
	fname = getname_flags(filename, lookup_flags, NULL);

	if (likely(susfs_is_current_proc_no_su()))
		goto orig_flow;

	if (static_branch_likely(&ksu_su_compat_enabled)) {
		if (unlikely(__ksu_is_allow_uid_for_current(current_uid().val)))
			ksu_handle_faccessat(&dfd, &fname, &mode, NULL);
	}

orig_flow:
	res = filename_lookup(dfd, fname, lookup_flags, &path, NULL);
	/* filename_lookup() consumes fname via putname() */
#else
	res = user_path_at(dfd, filename, lookup_flags, &path);
#endif"""

STAT_BODY = """#ifdef CONFIG_KSU_SUSFS
	fname = getname_flags(filename, lookup_flags, NULL);

	if (likely(susfs_is_current_proc_no_su()))
		goto orig_flow;

	if (static_branch_likely(&ksu_su_compat_enabled)) {
		if (unlikely(__ksu_is_allow_uid_for_current(current_uid().val)))
			ksu_handle_stat(&dfd, &fname, &flags);
	}

orig_flow:
	error = filename_lookup(dfd, fname, lookup_flags, &path, NULL);
	/* filename_lookup() consumes fname via putname() */
#else
	error = user_path_at(dfd, filename, lookup_flags, &path);
#endif"""


_EOL = {}


def read(p):
    with open(p, "r", encoding="utf-8", newline="") as f:
        s = f.read()
    # Some locally staged copies are CRLF while the CI checkout is LF.
    # Work in LF internally, then restore the original convention on write.
    _EOL[p] = "\r\n" if "\r\n" in s else "\n"
    return s.replace("\r\n", "\n")


def write(p, s):
    eol = _EOL.get(p, "\n")
    if eol == "\r\n":
        s = s.replace("\n", "\r\n")
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(s)


def replace_extern(s, symbol, new_block, path):
    i = s.find("extern int %s(" % symbol)
    assert i >= 0, "%s: no extern declaration for %s" % (path, symbol)
    start = s.rfind("#ifdef CONFIG_KSU", 0, i)
    assert start >= 0, "%s: #ifdef before %s not found" % (path, symbol)
    end = s.find("#endif", i)
    assert end >= 0, "%s: #endif after %s not found" % (path, symbol)
    end += len("#endif")
    return s[:start] + new_block.rstrip("\n") + s[end:]


def drop_old_call(s, call, path):
    pat = re.compile(
        r"\n[ \t]*#ifdef CONFIG_KSU[ \t]*\n[ \t]*" + re.escape(call)
        + r"[ \t]*\n[ \t]*#endif[ \t]*\n", re.S)
    s2, n = pat.subn("\n", s, count=1)
    assert n == 1, "%s: old unconditional call not found: %s" % (path, call)
    return s2


def insert_decl(s, anchor, path, scope=None):
    # `unsigned int lookup_flags = LOOKUP_FOLLOW;` appears three times in
    # fs/open.c, so anchor the search inside the target function only.
    if scope is not None:
        start = s.find(scope)
        assert start >= 0, "%s: scope %r not found" % (path, scope)
        pos = s.find(anchor, start)
        assert pos >= 0, "%s: anchor %r not found inside scope" % (path, anchor)
        end = pos + len(anchor)
        return (s[:end] + "\n#ifdef CONFIG_KSU_SUSFS\n\tstruct filename *fname = NULL;\n#endif\n"
                + s[end:])
    assert s.count(anchor) == 1, "%s: anchor not unique: %r" % (path, anchor)
    return s.replace(
        anchor,
        anchor + "\n#ifdef CONFIG_KSU_SUSFS\n\tstruct filename *fname = NULL;\n#endif\n",
        1)


def replace_lookup(s, old_line, new_block, path):
    assert s.count(old_line) == 1, "%s: lookup line not unique: %r" % (path, old_line)
    return s.replace(old_line, new_block, 1)


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    p = lambda rel: os.path.join(root, rel)

    # --- fs/namei.c: expose filename_lookup() to fs/open.c and fs/stat.c ---
    np = p("fs/namei.c")
    s = read(np)
    old = "static int filename_lookup(int dfd, struct filename *name, unsigned flags,"
    assert s.count(old) == 1, "fs/namei.c: static filename_lookup definition not unique"
    s = s.replace(old, old[len("static "):], 1)
    write(np, s)
    print("fs/namei.c: filename_lookup() made global")

    # --- fs/open.c ---
    op = p("fs/open.c")
    s = read(op)
    s = replace_extern(s, "ksu_handle_faccessat", OPEN_EXTERN, "fs/open.c")
    s = drop_old_call(s, "ksu_handle_faccessat(&dfd, &filename, &mode, NULL);", "fs/open.c")
    s = insert_decl(s, "\tunsigned int lookup_flags = LOOKUP_FOLLOW;\n", "fs/open.c",
                    scope="long do_faccessat(int dfd, const char __user *filename, int mode)")
    s = replace_lookup(s, "\tres = user_path_at(dfd, filename, lookup_flags, &path);",
                       OPEN_BODY, "fs/open.c")
    write(op, s)
    print("fs/open.c: faccessat ported to struct filename **")

    # --- fs/stat.c ---
    sp = p("fs/stat.c")
    s = read(sp)
    s = replace_extern(s, "ksu_handle_stat", STAT_EXTERN, "fs/stat.c")
    s = drop_old_call(s, "ksu_handle_stat(&dfd, &filename, &flags);", "fs/stat.c")
    s = insert_decl(s, "\tunsigned int lookup_flags = LOOKUP_FOLLOW | LOOKUP_AUTOMOUNT;\n",
                    "fs/stat.c")
    s = replace_lookup(s, "\terror = user_path_at(dfd, filename, lookup_flags, &path);",
                       STAT_BODY, "fs/stat.c")
    write(sp, s)
    print("fs/stat.c: stat ported to struct filename **")

    # --- local guards (mirror inline_hook_check.mk) ---
    assert "const char __user **filename_user" not in read(op)
    assert "const char __user **filename_user" not in read(sp)
    assert "struct filename **filename" in read(op)
    assert "struct filename **filename" in read(sp)
    for hook, f in [("ksu_handle_faccessat", op), ("ksu_handle_stat", sp)]:
        assert hook in read(f), "%s lost %s" % (f, hook)
    assert "static int filename_lookup" not in read(np)
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
PYPORT

    grep -q 'struct filename \*\*filename' fs/open.c || {
      echo "FATAL: faccessat hook was not ported to struct filename **" >&2
      exit 1
    }
    grep -q 'struct filename \*\*filename' fs/stat.c || {
      echo "FATAL: stat hook was not ported to struct filename **" >&2
      exit 1
    }
    ! grep -qw 'static int filename_lookup' fs/namei.c || {
      echo "FATAL: filename_lookup is still static" >&2
      exit 1
    }

    ! grep -qw 'ksu_vfs_read_hook' fs/read_write.c || {
      echo "FATAL: legacy ksu_vfs_read_hook survived the hook port" >&2
      exit 1
    }
    ! grep -qw 'ksu_execveat_hook' fs/exec.c || {
      echo "FATAL: legacy ksu_execveat_hook survived the hook port" >&2
      exit 1
    }
    ! grep -qw 'ksu_input_hook' drivers/input/input.c || {
      echo "FATAL: legacy ksu_input_hook survived the hook port" >&2
      exit 1
    }
    grep -qw 'ksu_handle_sys_reboot' kernel/reboot.c || {
      echo "FATAL: ksu_handle_sys_reboot hook missing" >&2
      exit 1
    }
    grep -qw 'ksu_handle_setresuid' kernel/sys.c || {
      echo "FATAL: ksu_handle_setresuid hook missing" >&2
      exit 1
    }
    ;;
esac

# --- fix: v8 stage-2 (TWO kernel-side points, both view-only) ---
# 1) selinux_hide.c: 撤销 v7 的兜底块（它把「无效上下文应返回 EINVAL」改成了
#    allow/deny 元组，反而新增 invalid-context access decision），并让
#    status / access 的 seqno 从真实 status 页动态读取且同步刷新 fake 页，
#    而不是冻结在常量上（4.6.2 判据「seqno 不前进 / 回填不一致」）。
#    干净 policydb 视图上游已用 backup_sepolicy/backup_policydb 实现，不动。
# 2) fs/proc/version.c: /proc/version 对 App uid 输出与 uname 伪装一致的
#    release/build-time，root/system 保持真实，修复「三路 release 不一致」。
# 边界：不换栈、不动 root 实现、不动 LSPosed 框架与其 Xposed 模块、
#       不改 live policy / 真实 status 页 / 真实 utsname、版本锁不变。
FIXPY="$ROOT_DIR/scripts/fix_selinux_hide_status.py"
if [[ ! -f "$FIXPY" ]]; then
  echo "FATAL: $FIXPY not found (selinux_hide v8 dynamic seqno)" >&2
  exit 1
fi
"$PYTHON_BIN" "$FIXPY" "$KERNEL_DIR/KernelSU"

PROCVERPY="$ROOT_DIR/scripts/fix_proc_version.py"
if [[ ! -f "$PROCVERPY" ]]; then
  echo "FATAL: $PROCVERPY not found (v8 /proc/version view)" >&2
  exit 1
fi
"$PYTHON_BIN" "$PROCVERPY" "$KERNEL_DIR"

MAKE_ARGS=(
  ARCH="$ARCH"
  SUBARCH="$ARCH"
  O="$OUT_DIR"
  CC=clang
  PYTHON="$PYTHON_BIN"
  CROSS_COMPILE="$CROSS_COMPILE"
  CROSS_COMPILE_ARM32="$CROSS_COMPILE_ARM32"
  CROSS_COMPILE_COMPAT="$CROSS_COMPILE_ARM32"
  CLANG_TRIPLE="$CLANG_TRIPLE"
)

make "${MAKE_ARGS[@]}" "${TARGET_DEVICE}_defconfig"

scripts/config --file "$OUT_DIR/.config" \
  --set-str STATIC_USERMODEHELPER_PATH /system/bin/micd \
  --enable KSU \
  --disable KPM \
  --enable XIAOMI_MIUI \
  --enable MIHW \
  --enable PACKAGE_RUNTIME_INFO \
  --enable MIGT \
  --enable MIGT_ENERGY_MODEL \
  --enable MILLET \
  --enable PERF_CRITICAL_RT_TASK \
  --enable SF_BINDER \
  --enable BINDER_OPT \
  --enable KPERFEVENTS \
  --enable PERF_HUMANTASK \
  --enable TASK_DELAY_ACCT \
  --enable MIUI_ZRAM_MEMORY_TRACKING \
  --enable MI_FRAGMENTION \
  --enable PERF_HELPER \
  --enable BOOTUP_RECLAIM \
  --enable MI_RECLAIM \
  --enable RTMM \
  --enable OVERLAY_FS \
  --disable DEBUG_FS \
  --disable MI_MEMORY_SYSFS \
  --disable LTO_CLANG \
  --disable LOCALVERSION_AUTO

if [[ "$REF_VARIANT" == "strict-susfs" ]]; then
  scripts/config --file "$OUT_DIR/.config" \
  --enable KSU_SUSFS \
  --enable KSU_SUSFS_SUS_PATH \
  --enable KSU_SUSFS_SUS_MOUNT \
  --enable KSU_SUSFS_SUS_KSTAT \
  --enable KSU_SUSFS_SPOOF_UNAME \
  --enable KSU_SUSFS_ENABLE_LOG \
  --enable KSU_SUSFS_HIDE_KSU_SUSFS_SYMBOLS \
  --enable KSU_SUSFS_SPOOF_CMDLINE_OR_BOOTCONFIG \
  --enable KSU_SUSFS_OPEN_REDIRECT \
  --enable KSU_SUSFS_SUS_MAP \
  --disable KSU_TRACEPOINT_HOOK \
  --disable KSU_MANUAL_HOOK
else
  scripts/config --file "$OUT_DIR/.config" \
  --disable KSU_TRACEPOINT_HOOK \
  --enable KSU_MANUAL_HOOK \
  --enable KSU_MANUAL_HOOK_AUTO_SETUID_HOOK \
  --enable KSU_MANUAL_HOOK_AUTO_INITRC_HOOK \
  --enable KSU_MANUAL_HOOK_AUTO_INPUT_HOOK \
  --disable KSU_SUSFS \
  --disable KSU_SUSFS_SUS_PATH \
  --disable KSU_SUSFS_SUS_MOUNT \
  --disable KSU_SUSFS_SUS_KSTAT \
  --disable KSU_SUSFS_SPOOF_UNAME \
  --disable KSU_SUSFS_ENABLE_LOG \
  --disable KSU_SUSFS_HIDE_KSU_SUSFS_SYMBOLS \
  --disable KSU_SUSFS_SPOOF_CMDLINE_OR_BOOTCONFIG \
  --disable KSU_SUSFS_OPEN_REDIRECT \
  --disable KSU_SUSFS_SUS_MAP
fi

make "${MAKE_ARGS[@]}" olddefconfig
make "${MAKE_ARGS[@]}" -j"$JOBS" Image

test -s "$OUT_DIR/arch/$ARCH/boot/Image"
test -s "$OUT_DIR/System.map"
popd >/dev/null

git clone --depth 1 "$ANYKERNEL_REPO" "$ANYKERNEL_DIR"

cat > "$ANYKERNEL_DIR/anykernel.sh" <<EOF
### AnyKernel3 Ramdisk Mod Script
## $PACKAGE_COMMENT

properties() { '
kernel.string=$KERNEL_STRING
do.devicecheck=1
do.modules=0
do.systemless=1
do.cleanup=1
do.cleanuponabort=1
device.name1=munch
device.name2=22021211RC
device.name3=22021211RG
supported.versions=13
supported.patchlevels=
supported.vendorpatchlevels=
'; }

boot_attributes() {
set_perm_recursive 0 0 755 644 \$RAMDISK/*
set_perm_recursive 0 0 750 750 \$RAMDISK/init* \$RAMDISK/sbin
}

BLOCK=/dev/block/bootdevice/by-name/boot
IS_SLOT_DEVICE=1
RAMDISK_COMPRESSION=auto
PATCH_VBMETA_FLAG=auto

. tools/ak3-core.sh

dump_boot
write_boot
EOF

cp "$OUT_DIR/arch/$ARCH/boot/Image" "$ANYKERNEL_DIR/Image"
(cd "$ANYKERNEL_DIR" && zip -r9 "$ARTIFACT_DIR/$ZIP_NAME" . -x '*.git*')
cp "$OUT_DIR/arch/$ARCH/boot/Image" "$ARTIFACT_DIR/Image"
cp "$OUT_DIR/.config" "$ARTIFACT_DIR/kernel.config"
cp "$OUT_DIR/System.map" "$ARTIFACT_DIR/System.map"

cat > "$ARTIFACT_DIR/build-info.txt" <<EOF
track=$BUILD_TRACK
kernel_ref=$REF_KERNEL_REF
kernel_sha=$(git -C "$KERNEL_DIR" rev-parse HEAD)
resukisu_ref=$RESUKISU_REF
resukisu_sha=$(git -C "$KERNEL_DIR/KernelSU" rev-parse HEAD)
susfs=$SUSFS_LABEL
susfs_version=$SUSFS_VERSION_LABEL
susfs_patch=ref-v230-differential
hook_patch=ref-resukisu-inline-hooks
hook_mode=inline-susfs
hook_api=struct-filename-port
use_aosp_clang=$USE_AOSP_CLANG
aosp_clang_ref=$AOSP_CLANG_REF
aosp_clang_version=$AOSP_CLANG_VERSION
display_mi=reference-miui
zip_name=$ZIP_NAME
EOF

bash "$ROOT_DIR/scripts/verify-artifact.sh" "$ARTIFACT_DIR" "$ARTIFACT_TRACK"

echo "Reference $REF_VARIANT artifacts are in $ARTIFACT_DIR"
