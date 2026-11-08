#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v5：让 App 侧 fake status 页呈现「原厂开机结束时的自洽值」。

实测证据（三次构建都命中同一项「status.sequence/policyload」）：
  v1 快照      App 读到 1 0 1 0 1   -> 报
  v2 钳制      App 读到 1 1 1 0 1   -> 报
  v4 同步真实  App 读到 1 6 1 0 1   -> 报（与 root 完全一致仍报）
  共同破绽：policyload == 0。

上游 initialize_fake_status() 的注释与取值：
  // Serve what a stock boot ends with instead: creation sentinel below
  // 6.10, one load plus one setenforce above.
  #if >= 6.10 : sequence=4, policyload=1
  #else       : sequence=0, policyload=0   (creation sentinel)
本机 CONFIG_KSU=y（Built-in），ksu_late_loaded=false，不走该分支，
拿到的是真实页 6/0 —— 既非 stock 值，policyload 也不为 1。

v5 修正：在 v4 的 page_address() 基础上，把 sequence/policyload 固定为
上游给 6.10+ 的 stock 值 4/1；enforcing/deny_unknown 仍与真实页同步。
不改 live policy、不改真实 status 页、不改任何功能状态。

兼容：原始上游源码（全新插入）、已打 v4 的源码（就地升级）、已打 v3 的源码。
幂等；锚点缺失 exit 2 让 CI 失败。
"""
import os
import sys

MARK = "KSU-MUNCH-FIX-V5"

DECL = (
    "/* " + MARK + ": remember the real status page for enforcing/deny_unknown sync */\n"
    "static struct selinux_kernel_status *ksu_real_status_ptr;\n"
)

ASSIGN = (
    "    /* " + MARK + ": keep a handle on the real page */\n"
    "    ksu_real_status_ptr = status;\n"
)

SYNC_BLOCK = (
    "        if (data) {\n"
    "            /* " + MARK + ": serve the stock-boot status tuple instead of a\n"
    "             * stale snapshot (0/0) or the KSU-polluted real page (6/0).\n"
    "             * Upstream uses sequence=4 / policyload=1 for >=6.10; 4.19\n"
    "             * built-in never enters that branch, hence the detector flags\n"
    "             * sequence/policyload unexpected on non-GKI kernels. */\n"
    "            if (ksu_real_status_ptr) {\n"
    "                /* fake_status is struct page *, NOT a virtual address. */\n"
    "                struct selinux_kernel_status *fake =\n"
    "                    (struct selinux_kernel_status *)page_address((struct page *)data);\n"
    "                fake->sequence   = 4;\n"
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

CLAMP_BLOCK = (
    "    /* 原生 Android boot 至少加载过一次策略，sequence 不可能为 0；\n"
    "       钳制为 1，避免被判定 sequence/policyload unexpected*/\n"
    "    if (!ksu_late_loaded && new_status->sequence == 0)\n"
    "        new_status->sequence = 1;\n"
)

V4_SEQ = "                fake->sequence     = READ_ONCE(ksu_real_status_ptr->sequence);\n"
V4_LOAD = "                fake->policyload   = READ_ONCE(ksu_real_status_ptr->policyload);\n"
BAD_CAST = "                struct selinux_kernel_status *fake = (struct selinux_kernel_status *)data;\n"
V4_CAST = (
    "                struct selinux_kernel_status *fake =\n"
    "                    (struct selinux_kernel_status *)page_address((struct page *)data);\n"
)


def main(root):
    path = os.path.join(root, "kernel", "feature", "selinux_hide.c")
    if not os.path.isfile(path):
        print("FATAL: %s not found" % path)
        return 2
    with open(path, encoding="utf-8") as f:
        src = f.read()

    # 情况 A：已打过 v4（或 v3 修过强转）-> 就地升级两行赋值
    if V4_SEQ in src or V4_LOAD in src:
        if V4_SEQ in src:
            src = src.replace(V4_SEQ, "                fake->sequence   = 4;\n", 1)
        if V4_LOAD in src:
            src = src.replace(V4_LOAD, "                fake->policyload = 1;\n", 1)
        if BAD_CAST in src:
            src = src.replace(BAD_CAST, V4_CAST, 1)
        src = src.replace("KSU-MUNCH-FIX-V4", MARK).replace("KSU-MUNCH-FIX-V3", MARK)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(src)
        print("selinux_hide.c: v4->v5 upgraded (stock 4/1)")
        return 0 if "fake->policyload = 1;" in src else 2

    if MARK in src:
        print("selinux_hide.c: already v5, skip")
        return 0

    # 情况 B：原始上游源码
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
            r"[ \t]*new_status->sequence = 1;\n"
        )
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

    required = [
        "ksu_real_status_ptr;",
        "ksu_real_status_ptr = status;",
        "page_address((struct page *)data)",
        "fake->sequence   = 4;",
        "fake->policyload = 1;",
    ]
    missing = [x for x in required if x not in src]
    if missing:
        print("FATAL: post-patch verification failed, missing: %s" % missing)
        return 2
    if BAD_CAST in src:
        print("FATAL: v3 bad cast still present")
        return 2

    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(src)
    print("selinux_hide.c: v5 stock 4/1 sync=1 decl=1 assign=1 removed_v2_clamp=%d" % n_clamp)
    print("NOTE: page_address() applied; live policy and real status page untouched.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: %s <KSU source root>" % sys.argv[0])
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
