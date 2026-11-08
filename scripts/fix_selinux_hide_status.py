#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v6（D1）：让 /sys/fs/selinux/access 回包的 avd.seqno 与 App 侧 status.sequence
使用同一个值，消除「access/status 读数不一致」。

定位过程（重要，避免再走错方向）：
  v2/v4/v5 都在改 status 数值，均无效；
  受控实验：关掉 selinux_hide 后「检测SELinux Policy」仍在 -> 与 fake status 页无关；
  官方 4.6.2 判据：「判据落在 access / status 的读数一致性上」，
    access 侧校验内核回填的 avdSeqNo/seqno；
  上游 my_write_access() 结尾硬编码 `avd.seqno = 1;`，而 v5 把 status.sequence
  改成 4 -> 两个读数不自洽。

原生内核里 avd.seqno 与 selinux_kernel_status.sequence 同源于
avc_cache.latest_notif，本应相等；KSU 把前者写死成 1，破坏了这一关系。

v6 做法（在 v5 基础上）：
  1) 新增全局 `static u32 ksu_fake_status_seq = 4;`
  2) status 同步：fake->sequence = ksu_fake_status_seq（替代字面量 4）
  3) access 回包：avd.seqno = ksu_fake_status_seq（替代字面量 1）
  -> 两处恒等，且仍是上游给 >=6.10 的 stock 值 4。
  保留 v4 的 page_address() panic 修复、v5 的 policyload=1。

不改 live policy、不改真实 status 页、不改任何真实配置。
幂等；锚点缺失 exit 2 让 CI 失败。
"""
import os
import sys

MARK = "KSU-MUNCH-FIX-V6"

DECL = (
    "/* " + MARK + ": single source of truth for App-visible status.sequence and\n"
    " * access avd.seqno (both come from avc_cache.latest_notif on stock kernels) */\n"
    "static u32 ksu_fake_status_seq = 4;\n"
    "static struct selinux_kernel_status *ksu_real_status_ptr;\n"
)

ASSIGN = (
    "    /* " + MARK + ": keep a handle on the real page */\n"
    "    ksu_real_status_ptr = status;\n"
)

SYNC_BLOCK = (
    "        if (data) {\n"
    "            /* " + MARK + ": serve a coherent (status, access) pair.\n"
    "             * policyload=1 + sequence=ksu_fake_status_seq matches what a\n"
    "             * stock boot ends with (upstream >=6.10 path); 4.19 built-in\n"
    "             * never reaches that branch, so we supply it here. */\n"
    "            if (ksu_real_status_ptr) {\n"
    "                /* fake_status is struct page *, NOT a virtual address. */\n"
    "                struct selinux_kernel_status *fake =\n"
    "                    (struct selinux_kernel_status *)page_address((struct page *)data);\n"
    "                fake->sequence   = ksu_fake_status_seq;\n"
    "                fake->policyload = 1;\n"
    "                fake->enforcing    = READ_ONCE(ksu_real_status_ptr->enforcing);\n"
    "                fake->deny_unknown = READ_ONCE(ksu_real_status_ptr->deny_unknown);\n"
    "            }\n"
    "            filp->private_data = data;\n"
    "            return 0;\n"
    "        }\n"
)

OLD_OPEN = (
    "        if (data) {\n"
    "            filp->private_data = data;\n"
    "            return 0;\n"
    "        }\n"
)

SEQ_ASSIGN_OLD = (
    "    // stock reads 1; a loader load_policy may have bumped the backup before we load\n"
    "    avd.seqno = 1;\n"
)
SEQ_ASSIGN_NEW = (
    "    /* " + MARK + ": must match the App-visible status.sequence, otherwise\n"
    "     * access/status read-back consistency checks flag the device. */\n"
    "    avd.seqno = ksu_fake_status_seq;\n"
)

CLAMP_BLOCK = (
    "    /* 原生 Android boot 至少加载过一次策略，sequence 不可能为 0；\n"
    "       钳制为 1，避免被判定 sequence/policyload unexpected*/\n"
    "    if (!ksu_late_loaded && new_status->sequence == 0)\n"
    "        new_status->sequence = 1;\n"
)


def main(root):
    path = os.path.join(root, "kernel", "feature", "selinux_hide.c")
    if not os.path.isfile(path):
        print("FATAL: %s not found" % path)
        return 2
    with open(path, encoding="utf-8") as f:
        src = f.read()

    # 已打过早期修复（v3/v4/v5）-> 就地升级：替换整个 if(data) 块 + seqno 行 + 声明
    if "KSU-MUNCH-FIX-V3" in src or "KSU-MUNCH-FIX-V4" in src or "KSU-MUNCH-FIX-V5" in src:
        import re
        pat = re.compile(r"        if \(data\) \{\n(?:(?!        \}\n).)*?        \}\n", re.S)
        m = pat.search(src)
        if not m:
            print("FATAL: earlier if(data) block not found")
            return 2
        src = src[:m.start()] + SYNC_BLOCK + src[m.end():]
        if SEQ_ASSIGN_OLD in src:
            src = src.replace(SEQ_ASSIGN_OLD, SEQ_ASSIGN_NEW, 1)
        elif "avd.seqno = 1;" in src:
            src = src.replace("    avd.seqno = 1;\n", SEQ_ASSIGN_NEW, 1)
        else:
            print("FATAL: avd.seqno assignment not found")
            return 2
        # 声明块换成 v6（含共享变量）
        old_decl = re.search(
            r"/\* KSU-MUNCH-FIX-V[345]:[^\n]*\*/\n"
            r"(?:/\*[^\n]*\*/\n)*"
            r"static struct selinux_kernel_status \*ksu_real_status_ptr;\n", src)
        if old_decl:
            src = src[:old_decl.start()] + DECL + src[old_decl.end():]
        elif "static u32 ksu_fake_status_seq" not in src:
            src = src.replace("static struct selinux_kernel_status *ksu_real_status_ptr;\n", DECL, 1)
        src = re.sub(r"KSU-MUNCH-FIX-V[345]", MARK, src)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(src)
        print("selinux_hide.c: upgraded to v6 (shared seqno)")
        return 0 if _verify(src) else 2

    if MARK in src:
        print("selinux_hide.c: already v6, skip")
        return 0

    # 原始上游源码
    if "KSU-MUNCH-FIX-V" in src:
        print("FATAL: unknown earlier fix marker present")
        return 2

    if CLAMP_BLOCK in src:
        src = src.replace(CLAMP_BLOCK, "", 1)
        n_clamp = 1
    else:
        import re
        pat = re.compile(
            r"[ \t]*if \(!ksu_late_loaded && new_status->sequence == 0\)\n"
            r"[ \t]*new_status->sequence = 1;\n")
        src, n_clamp = pat.subn("", src, count=1)

    decl_anchor = "__maybe_static struct page *fake_status = NULL;"
    if decl_anchor not in src:
        print("FATAL: fake_status declaration anchor missing")
        return 2
    src = src.replace(decl_anchor, decl_anchor + "\n" + DECL.rstrip("\n"), 1)

    assign_anchor = "    struct selinux_kernel_status *status = page_address(selinux_status_page);"
    if assign_anchor not in src:
        print("FATAL: initialize_fake_status real-page anchor missing")
        return 2
    src = src.replace(assign_anchor, assign_anchor + "\n" + ASSIGN.rstrip("\n"), 1)

    if OLD_OPEN not in src:
        print("FATAL: my_sel_open_handle_status 'if (data)' block missing")
        return 2
    src = src.replace(OLD_OPEN, SYNC_BLOCK, 1)

    if SEQ_ASSIGN_OLD not in src:
        print("FATAL: 'avd.seqno = 1;' anchor missing (upstream changed?)")
        return 2
    src = src.replace(SEQ_ASSIGN_OLD, SEQ_ASSIGN_NEW, 1)

    if not _verify(src):
        return 2
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(src)
    print("selinux_hide.c: v6 shared seqno=4 sync=1 decl=1 assign=1 removed_v2_clamp=%d" % n_clamp)
    return 0


def _verify(src):
    required = [
        "static u32 ksu_fake_status_seq = 4;",
        "ksu_real_status_ptr;",
        "ksu_real_status_ptr = status;",
        "page_address((struct page *)data)",
        "fake->sequence   = ksu_fake_status_seq;",
        "fake->policyload = 1;",
        "avd.seqno = ksu_fake_status_seq;",
    ]
    missing = [x for x in required if x not in src]
    if missing:
        print("FATAL: post-patch verification failed, missing: %s" % missing)
        return False
    if "avd.seqno = 1;" in src or "(struct selinux_kernel_status *)data;" in src:
        print("FATAL: stale literal / v3 bad cast still present")
        return False
    return True


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: %s <KSU source root>" % sys.argv[0])
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
