#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v9 / 补齐 Tampered kernel 剩余通道（kernel/utsname_sysctl.c）

v8 已让 App 视角 `uname(2)` / `uname -r` / `/proc/version` 一致，但实测
/proc/sys/kernel/osrelease = 4.19.325-perf+、/proc/sys/kernel/version =
2026 构建时间，仍是真实值 -> 春秋仍能看到内核版本不自洽。

4.19 的 osrelease / version 两个 sysctl 共用 proc_do_uts_string()，其
table->data 指向 init_uts_ns.name.release / .version。本补丁在这个 handler
里对 App uid（>=10000）把读缓冲换成与 SUSFS set_uname 相同的伪装值；
root/system 完全走原路径。不改 utsname 全局结构、不动 vermagic、
不新增 hook 点。幂等；锚点缺失 exit 2。
"""
import os
import sys

MARK = "KSU-MUNCH-FIX-V9-UTSNAME"
SPOOF_RELEASE = "4.19.157-perf-g8554f215acb9"
SPOOF_VERSION = "#1 SMP PREEMPT Sat Dec 23 07:13:10 UTC 2023"

HEADER = (
    "/* " + MARK + ": App-visible osrelease/version must agree with the\n"
    " * spoofed uname view served by SUSFS set_uname and fs/proc/version.c. */\n"
    "#define KSU_UTS_SPOOF_RELEASE \"" + SPOOF_RELEASE + "\"\n"
    "#define KSU_UTS_SPOOF_VERSION \"" + SPOOF_VERSION + "\"\n"
)

OLD_BLOCK = (
    "\tdown_read(&uts_sem);\n"
    "\tmemcpy(tmp_data, get_uts(table), sizeof(tmp_data));\n"
    "\tup_read(&uts_sem);\n"
    "\tr = proc_dostring(&uts_table, write, buffer, lenp, ppos);\n"
)

NEW_BLOCK = (
    "\tdown_read(&uts_sem);\n"
    "\tmemcpy(tmp_data, get_uts(table), sizeof(tmp_data));\n"
    "\tup_read(&uts_sem);\n"
    "\n"
    "\t/* " + MARK + ": only the detector's view is filtered; the real\n"
    "\t * init_uts_ns and all root/system readers keep the original value. */\n"
    "\tif (!write && current_uid().val >= 10000) {\n"
    "\t\tif (table->data == init_uts_ns.name.release)\n"
    "\t\t\tstrlcpy(tmp_data, KSU_UTS_SPOOF_RELEASE, sizeof(tmp_data));\n"
    "\t\telse if (table->data == init_uts_ns.name.version)\n"
    "\t\t\tstrlcpy(tmp_data, KSU_UTS_SPOOF_VERSION, sizeof(tmp_data));\n"
    "\t}\n"
    "\n"
    "\tr = proc_dostring(&uts_table, write, buffer, lenp, ppos);\n"
)


def main(root):
    path = os.path.join(root, "kernel", "utsname_sysctl.c")
    if not os.path.isfile(path):
        print("FATAL: %s not found" % path)
        return 2
    with open(path, encoding="utf-8") as f:
        src = f.read()

    if MARK in src:
        print("kernel/utsname_sysctl.c: already v9, skip")
        return 0

    if OLD_BLOCK not in src:
        print("FATAL: proc_do_uts_string anchor missing (kernel tree changed?)")
        return 2
    src = src.replace(OLD_BLOCK, NEW_BLOCK, 1)

    if HEADER.strip() not in src:
        anchor = "#include <linux/rwsem.h>"
        if anchor not in src:
            print("FATAL: include anchor missing")
            return 2
        src = src.replace(anchor, anchor + "\n" + HEADER.rstrip("\n"), 1)
    for inc in ("#include <linux/cred.h>", "#include <linux/string.h>"):
        if inc not in src:
            anchor = "#include <linux/rwsem.h>"
            src = src.replace(anchor, anchor + "\n" + inc, 1)

    required = [MARK, "current_uid().val >= 10000",
                "KSU_UTS_SPOOF_RELEASE", "KSU_UTS_SPOOF_VERSION",
                "#include <linux/cred.h>", "#include <linux/string.h>",
                "init_uts_ns.name.release", "init_uts_ns.name.version"]
    missing = [x for x in required if x not in src]
    if missing:
        print("FATAL: post-patch verification failed, missing: %s" % missing)
        return 2
    if src.count(MARK) != 2:
        print("FATAL: patch marker count unexpected: %d" % src.count(MARK))
        return 2

    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(src)
    print("kernel/utsname_sysctl.c: v9 App-uid osrelease/version spoof applied")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: %s <kernel source root>" % sys.argv[0])
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
