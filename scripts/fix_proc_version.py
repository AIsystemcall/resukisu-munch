#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
阶段 2 / Tampered kernel 补丁（4.19 fs/proc/version.c）

依据（原版 answer_zh.md）：
  - 「检测到内核版本欺骗：%1$s」原因：
    `uname(2)`、`uname -r` 子进程、`/proc/version` 三路 release 字符串不一致。
  - 「Tampered kernel / 伪装内核」：读取内核 uname（版本字符串、构建时间）
    与预设名单/基线比对；内核信息被伪装后的一致性判定也在本条。

本机实测：SUSFS set_uname 只改 uname(2) 视图（4.19.157-perf-... / 2023），
/proc/version 仍输出真实 4.19.325-perf+ / 2026 构建时间 -> 三路不一致。

做法：/proc/version 的 show 回调对 App uid 复用同一份伪装 release / version，
root/system 保持真实 banner。只改检测方读取的视图，不动真实运行环境，
不改 utsname 全局结构，不影响模块 vermagic。幂等；锚点缺失 exit 2。
"""
import os
import sys

MARK = "KSU-MUNCH-FIX-V8-PROCVERSION"
SPOOF_RELEASE = "4.19.157-perf-g8554f215acb9"
SPOOF_VERSION = "#1 SMP PREEMPT Sat Dec 23 07:13:10 UTC 2023"

OLD_SHOW = (
    "static int version_proc_show(struct seq_file *m, void *v)\n"
    "{\n"
    "\tseq_printf(m, linux_proc_banner,\n"
    "\t\tutsname()->sysname,\n"
    "\t\tutsname()->release,\n"
    "\t\tutsname()->version);\n"
    "\treturn 0;\n"
    "}\n"
)

NEW_SHOW = (
    "/* " + MARK + ": keep /proc/version aligned with the spoofed uname view.\n"
    " * SUSFS set_uname only changes uname(2); the procfs banner still leaks the\n"
    " * real release and build time, which the detector cross-checks. */\n"
    "#define KSU_SPOOF_RELEASE \"" + SPOOF_RELEASE + "\"\n"
    "#define KSU_SPOOF_VERSION \"" + SPOOF_VERSION + "\"\n"
    "static int version_proc_show(struct seq_file *m, void *v)\n"
    "{\n"
    "\tif (current_uid().val >= 10000) {\n"
    "\t\tseq_printf(m, linux_proc_banner,\n"
    "\t\t\tutsname()->sysname,\n"
    "\t\t\tKSU_SPOOF_RELEASE,\n"
    "\t\t\tKSU_SPOOF_VERSION);\n"
    "\t} else {\n"
    "\t\tseq_printf(m, linux_proc_banner,\n"
    "\t\t\tutsname()->sysname,\n"
    "\t\t\tutsname()->release,\n"
    "\t\t\tutsname()->version);\n"
    "\t}\n"
    "\treturn 0;\n"
    "}\n"
)


def main(root):
    path = os.path.join(root, "fs", "proc", "version.c")
    if not os.path.isfile(path):
        print("FATAL: %s not found" % path)
        return 2
    with open(path, encoding="utf-8") as f:
        src = f.read()

    if MARK in src:
        print("fs/proc/version.c: already v8, skip")
        return 0

    if OLD_SHOW not in src:
        print("FATAL: version_proc_show anchor missing (kernel tree changed?)")
        return 2
    src = src.replace(OLD_SHOW, NEW_SHOW, 1)

    if "#include <linux/cred.h>" not in src:
        anchor = "#include <linux/utsname.h>"
        if anchor not in src:
            print("FATAL: utsname.h include anchor missing")
            return 2
        src = src.replace(anchor, anchor + "\n#include <linux/cred.h>", 1)

    required = [MARK, "current_uid().val >= 10000",
                "KSU_SPOOF_RELEASE", "KSU_SPOOF_VERSION",
                "#include <linux/cred.h>"]
    missing = [x for x in required if x not in src]
    if missing:
        print("FATAL: post-patch verification failed, missing: %s" % missing)
        return 2
    if src.count(MARK) != 1:
        print("FATAL: patch marker must appear exactly once")
        return 2

    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(src)
    print("fs/proc/version.c: v8 App-uid banner spoof applied")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: %s <kernel source root>" % sys.argv[0])
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
