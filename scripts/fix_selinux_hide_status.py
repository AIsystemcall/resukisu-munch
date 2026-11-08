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

为什么不能靠换序解决
--------------------
把 handle_second_stage() 挪到 apply_kernelsu_rules() 之后（2026-11-08 实测）
会导致 initialize_fake_status() 里 selinux_state.ss->status_page 已被策略加载
重建为空，直接 "status_page not exist" 跳过 -> fake_status 根本不存在 ->
app 读到真实 sequence（Abnormal(k) 消失），但 ksu 域等全部暴露（异常 1 项变 9 项）。
因此 **必须保持原调用顺序**，只在快照内容上修正 sequence。

修复
----
在 initialize_fake_status() 的 memcpy 之后：若 sequence 为 0（说明快照发生在
首次策略加载之前），钳制为 1，模拟原生 Android 开机「至少加载过一次策略」的状态。

用法：python3 fix_selinux_hide_status.py <KernelSU 源码根目录>
"""
import os
import re
import sys

KSUD = os.path.join("kernel", "runtime", "ksud_integration.c")
HIDE = os.path.join("kernel", "feature", "selinux_hide.c")

# 错误顺序形态：apply_kernelsu_rules 在前、handle_second_stage 在后
PAT_WRONG_ORDER = re.compile(
    r"^(?P<i2>[ \t]*)apply_kernelsu_rules\(\);[ \t]*\n"
    r"(?P<blank>(?:[ \t]*\n)?)"
    r"^(?P<i1>[ \t]*)ksu_selinux_hide_handle_second_stage\(\);[ \t]*$",
    re.M,
)


def revert_bad_order(path):
    """若源码被错误地换过序，还原为上游原始顺序。返回还原处数。"""
    src = open(path, encoding="utf-8").read()

    def repl(m):
        return (
            "%sksu_selinux_hide_handle_second_stage();\n"
            "%s"
            "%sapply_kernelsu_rules();"
            % (m.group("i1"), m.group("blank"), m.group("i2"))
        )

    out, n = PAT_WRONG_ORDER.subn(repl, src)
    if n:
        open(path, "w", encoding="utf-8", newline="").write(out)
    return n


def patch_hide(path):
    """memcpy 之后钳制 sequence。返回 (是否新插入, 是否已存在)。"""
    src = open(path, encoding="utf-8").read()

    anchor = "    memcpy(new_status, status, sizeof(*status));\n"
    if anchor not in src:
        return 0, 0
    if "new_status->sequence = 1;" in src:
        return 0, 1  # 已修过

    inject = (
        "    /* 原生 Android boot 至少加载过一次策略，sequence 不可能为 0；\n"
        "       本快照发生在 apply_kernelsu_rules() 之前，所以捕获到的是 0。\n"
        "       钳制为 1，避免被判定 sequence/policyload unexpected\n"
        "       -> Abnormal Environment(k) */\n"
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

    if not os.path.isfile(hide):
        sys.stderr.write("FATAL: 找不到 %s\n" % hide)
        return 1

    n_revert = revert_bad_order(ksud) if os.path.isfile(ksud) else 0
    h_new, h_exist = patch_hide(hide)

    print("ksud_integration.c: reverted_bad_order=%d" % n_revert)
    print("selinux_hide.c    : inserted=%d, already_fixed=%d" % (h_new, h_exist))

    if h_new == 0 and h_exist == 0:
        sys.stderr.write(
            "FATAL: 未能在 initialize_fake_status() 定位 memcpy 锚点，"
            "源码结构已变化（不要使用未修复的内核）\n"
        )
        return 2
    print("NOTE: 调用顺序保持上游原样，仅修正 sequence。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
