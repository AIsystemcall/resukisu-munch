#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修复 ReSukiSU / BakaSU 在 Android 4.19 等 non-GKI 内核上
`Abnormal Environment(k)` 恒报错的根因。

根因
----
kernel/runtime/ksud_integration.c 中 init second_stage 分支的顺序是

    ksu_selinux_hide_handle_second_stage();   // 内部立刻 initialize_fake_status()
    apply_kernelsu_rules();                   // 之后才加载 KSU 策略

快照抢在策略加载之前，伪造 status 页的 sequence 被固定为 0，而真实值是 1..N。
检测器读 /sys/fs/selinux/status 看到 sequence=0，判定「策略从未加载过」，
归入 sequence/policyload unexpected -> Abnormal Environment(k)。

修复
----
1) 换序：先 apply_kernelsu_rules()，再 handle_second_stage()，让快照捕获 sequence=1；
2) 兜底：initialize_fake_status() 中若 sequence 仍为 0，钳制为 1。

幂等：上游若已修复，自动识别并跳过；两者都不是则报错退出（避免静默产出坏包）。

用法：python3 fix_selinux_hide_status.py <KernelSU 源码根目录>
"""
import os
import re
import sys

KSUD = os.path.join("kernel", "runtime", "ksud_integration.c")
HIDE = os.path.join("kernel", "feature", "selinux_hide.c")

# 「尚未修复」的形态：handle_second_stage 紧跟着 apply_kernelsu_rules
PAT_UNFIXED = re.compile(
    r"^(?P<i1>[ \t]*)ksu_selinux_hide_handle_second_stage\(\);[ \t]*\n"
    r"(?P<blank>(?:[ \t]*\n)?)"
    r"^(?P<i2>[ \t]*)apply_kernelsu_rules\(\);[ \t]*$",
    re.M,
)

# 「已经修复」的形态：apply_kernelsu_rules 在前、handle_second_stage 在后
PAT_FIXED = re.compile(
    r"^[ \t]*apply_kernelsu_rules\(\);[ \t]*\n"
    r"(?:[ \t]*\n)*"
    r"^[ \t]*ksu_selinux_hide_handle_second_stage\(\);[ \t]*$",
    re.M,
)


def patch_ksud(path):
    """返回 (已换序处数, 已修复处数)。"""
    src = open(path, encoding="utf-8").read()

    def repl(m):
        return (
            "%sapply_kernelsu_rules();\n"
            "%s"
            "%sksu_selinux_hide_handle_second_stage();"
            % (m.group("i2"), m.group("blank"), m.group("i1"))
        )

    out, n_fixed = PAT_UNFIXED.subn(repl, src)
    n_already = len(PAT_FIXED.findall(out))
    if n_fixed:
        open(path, "w", encoding="utf-8", newline="").write(out)
    return n_fixed, n_already


def patch_hide(path):
    """返回 (是否新插入, 是否已存在)。"""
    src = open(path, encoding="utf-8").read()

    anchor = "    memcpy(new_status, status, sizeof(*status));\n"
    if anchor not in src:
        return 0, 0
    if "new_status->sequence = 1;" in src:
        return 0, 1  # 已修过

    inject = (
        "    /* 原生 Android boot 至少加载过一次策略，sequence 不可能为 0；\n"
        "       若快照时机仍在首刷之前，钳制为 1，避免被判定\n"
        "       sequence/policyload unexpected -> Abnormal Environment(k) */\n"
        "    if (!ksu_late_loaded && new_status->sequence == 0)\n"
        "        new_status->sequence = 1;\n"
    )
    out = src.replace(anchor, anchor + inject, 1)
    open(path, "w", encoding="utf-8", newline="").write(out)
    return 1, 0


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    ksud = os.path.join(root, KSUD)
    hide = os.path.join(root, HIDE)

    if not os.path.isfile(ksud):
        sys.stderr.write("FATAL: 找不到 %s\n" % ksud)
        return 1

    n_fixed, n_already = patch_ksud(ksud)
    if os.path.isfile(hide):
        h_new, h_exist = patch_hide(hide)
    else:
        h_new, h_exist = 0, 0

    print("ksud_integration.c: reordered=%d, already_fixed=%d" % (n_fixed, n_already))
    print("selinux_hide.c    : inserted=%d, already_fixed=%d" % (h_new, h_exist))

    total = n_fixed + n_already
    if total == 0:
        sys.stderr.write(
            "FATAL: 未找到任何 second_stage 调用点，源码结构已变化，"
            "请人工核对（不要使用未修复的内核）\n"
        )
        return 2
    if n_fixed == 0 and n_already > 0:
        print("NOTE: 上游已修复调用顺序，本次仅做校验。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
