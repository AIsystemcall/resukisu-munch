#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v3 修复：让 selinux_hide 的伪造 status 页「动态同步真实页」，而不是靠静态快照/钳制。

背景（实测）：
  root/shell 读 /sys/fs/selinux/status = (version, sequence, enforcing, policyload,
  deny_unknown) = 1 6 1 0 1；App(uid>=10000) 通过 fake_status 页读到 1 0 1 0 1（v1 快照）
  或 1 1 1 0 1（v2 钳制）。两者都与真实不一致，命中
  「sequence/policyload unexpected」->「检测SELinux Policy时发现可疑问题」。

v1 = 换序（已证伪：会让 initialize_fake_status 拿不到 status page）
v2 = 保持原顺序 + 把 sequence 钳成 1（仍不自洽，且用户未认可该方向）
v3 = 保持上游原顺序不动，只做两件事：
   1) initialize_fake_status() 记住真实 status 页指针；
   2) 每次 App 打开 status 节点时，把 sequence/policyload/enforcing/deny_unknown
      从真实页同步到伪造页。这样 App 与 root 视角完全一致、视图内部自洽。

真实 live policy / 真实 status 页 / 系统功能一律不改动；只改「检测方看到的视图」。

用法： python3 fix_selinux_hide_status_v3.py <KSU源码根目录>
退出码：0 成功（或已修复）；2 关键锚点缺失 -> 让 CI 失败，绝不静默产出未修复内核。
"""
import os
import sys

MARK = "KSU-MUNCH-FIX-V3"

DECL = (
    "/* " + MARK + ": remember the real status page so the fake view stays coherent */\n"
    "static struct selinux_kernel_status *ksu_real_status_ptr;\n"
)

ASSIGN = (
    "    /* " + MARK + ": keep a handle on the real page for later sync */\n"
    "    ksu_real_status_ptr = status;\n"
)

SYNC_BLOCK = (
    "        if (data) {\n"
    "            /* " + MARK + ": a stale snapshot (sequence=0 or clamped to 1 while\n"
    "             * the real page is 6/0) is exactly what sequence/policyload\n"
    "             * consistency checks flag. Mirror the dynamic fields on every\n"
    "             * open so App and root see the same status. */\n"
    "            if (ksu_real_status_ptr) {\n"
    "                struct selinux_kernel_status *fake = (struct selinux_kernel_status *)data;\n"
    "                fake->sequence     = READ_ONCE(ksu_real_status_ptr->sequence);\n"
    "                fake->policyload   = READ_ONCE(ksu_real_status_ptr->policyload);\n"
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


def main(root):
    path = os.path.join(root, "kernel", "feature", "selinux_hide.c")
    if not os.path.isfile(path):
        print("FATAL: %s not found" % path)
        return 2
    with open(path, encoding="utf-8") as f:
        src = f.read()

    if MARK in src:
        print("selinux_hide.c: already patched (v3), skip")
        return 0

    # 1) 移除 v2 的静态钳制块（如果存在）；v3 由每次 open 同步取代它。
    n_clamp = 0
    if CLAMP_BLOCK in src:
        src = src.replace(CLAMP_BLOCK, "", 1)
        n_clamp = 1
    else:
        # 兼容只有核心两行、注释略有不同的写法
        import re
        pat = re.compile(
            r"[ \t]*if \(!ksu_late_loaded && new_status->sequence == 0\)\n"
            r"[ \t]*new_status->sequence = 1;\n"
        )
        src, n_clamp = pat.subn("", src, count=1)

    # 2) 全局声明：插在 fake_status 声明之后
    decl_anchor = "__maybe_static struct page *fake_status = NULL;"
    if decl_anchor not in src:
        print("FATAL: fake_status declaration anchor missing")
        return 2
    src = src.replace(decl_anchor, decl_anchor + "\n" + DECL.rstrip("\n"), 1)
    n_decl = 1

    # 3) initialize_fake_status() 里记住真实页
    assign_anchor = "    struct selinux_kernel_status *status = page_address(selinux_status_page);"
    if assign_anchor not in src:
        print("FATAL: initialize_fake_status real-page anchor missing")
        return 2
    src = src.replace(assign_anchor, assign_anchor + "\n" + ASSIGN.rstrip("\n"), 1)
    n_assign = 1

    # 4) open 时同步动态字段
    if OLD_OPEN not in src:
        print("FATAL: my_sel_open_handle_status 'if (data)' block missing")
        return 2
    src = src.replace(OLD_OPEN, SYNC_BLOCK.rstrip("\n"), 1)
    n_sync = 1

    # 严格自检
    required = [
        "ksu_real_status_ptr;",
        "ksu_real_status_ptr = status;",
        "fake->sequence     = READ_ONCE",
        "fake->policyload   = READ_ONCE",
    ]
    missing = [x for x in required if x not in src]
    if missing:
        print("FATAL: post-patch verification failed, missing: %s" % missing)
        return 2
    if n_decl != 1 or n_assign != 1 or n_sync != 1:
        print("FATAL: unexpected patch counts")
        return 2

    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(src)

    print("selinux_hide.c: v3 sync=1 decl=1 assign=1 removed_v2_clamp=%d" % n_clamp)
    print("NOTE: upstream call order untouched; live policy and real status page untouched.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: %s <KSU source root>" % sys.argv[0])
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
