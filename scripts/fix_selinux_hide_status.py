#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v4：修 v3 的空指针 panic，仍保持「动态同步 status 视图」方向不变。

v3 崩溃根因（pstore 实测）：
  fake_status 的声明是 `struct page *`（不是虚拟地址）：
      void *data;
      data = fake_status;            // data 实际是 struct page *
      filp->private_data = data;    // 原生 sel_read_status 会 page_address(private_data)
  v3 把 data 直接强转成 struct selinux_kernel_status * 并写 4 个 u32，
  等于覆写了 struct page 头部（flags / lru / mapping），随后
  simple_read_from_buffer -> __check_object_size 访问被破坏的 page，
  触发 "NULL pointer dereference at virtual address 0x8" 并 kernel_panic。

v4 修正：先 page_address((struct page *)data) 再写字段；同时兼容
  - 原始上游源码（全新插入）
  - 已打过 v3 错误补丁的源码（就地改正）
幂等；锚点缺失 exit 2 让 CI 失败，绝不静默产出未修复内核。

用法：python3 fix_selinux_hide_status.py <KSU源码根目录>
"""
import os
import sys

MARK = "KSU-MUNCH-FIX-V3"          # 标记沿用，便于就地升级 v3 -> v4
V4_TAG = "KSU-MUNCH-FIX-V4"

DECL = (
    "/* " + MARK + ": remember the real status page so the fake view stays coherent */\n"
    "static struct selinux_kernel_status *ksu_real_status_ptr;\n"
)

ASSIGN = (
    "    /* " + MARK + ": keep a handle on the real page for later sync */\n"
    "    ksu_real_status_ptr = status;\n"
)

BAD_CAST = "                struct selinux_kernel_status *fake = (struct selinux_kernel_status *)data;\n"
GOOD_CAST = (
    "                /* " + V4_TAG + ": data is a struct page *, NOT a virtual address.\n"
    "                 * v3 cast it directly and corrupted struct page -> kernel panic.\n"
    "                 * arm64 lowmem page_address() is always valid, no NULL check needed. */\n"
    "                struct selinux_kernel_status *fake =\n"
    "                    (struct selinux_kernel_status *)page_address((struct page *)data);\n"
)

SYNC_BLOCK = (
    "        if (data) {\n"
    "            /* " + MARK + ": a stale snapshot (sequence=0 or clamped to 1 while\n"
    "             * the real page is 6/0) is exactly what sequence/policyload\n"
    "             * consistency checks flag. Mirror the dynamic fields on every\n"
    "             * open so App and root see the same status. */\n"
    "            if (ksu_real_status_ptr) {\n"
    + GOOD_CAST +
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

    # 情况 A：已打过 v3（错误强转）-> 就地升级为 v4
    if BAD_CAST in src:
        src = src.replace(BAD_CAST, GOOD_CAST, 1)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(src)
        print("selinux_hide.c: v3->v4 upgraded (page_address fix applied)")
        return 0 if V4_TAG in src and BAD_CAST not in src else 2

    if V4_TAG in src and MARK in src:
        print("selinux_hide.c: already v4, skip")
        return 0

    # 情况 B：原始上游源码 -> 全新插入 v4
    if MARK in src:
        print("FATAL: v3 marker present but bad cast not found; source layout unexpected")
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
        "fake->sequence     = READ_ONCE",
        "fake->policyload   = READ_ONCE",
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
    print("selinux_hide.c: v4 sync=1 decl=1 assign=1 removed_v2_clamp=%d" % n_clamp)
    print("NOTE: data is struct page *; page_address() applied. Live policy untouched.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: %s <KSU source root>" % sys.argv[0])
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
