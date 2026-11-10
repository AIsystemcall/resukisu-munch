#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v8 = v6 基线 - v7 兜底 + seqno 动态同源（阶段 2 / SELinux 项）

依据（2026-11-10 静态核查推翻了「干净 policydb 视图」这一前提）：
  - upstream my_write_access() 本来就用 backup_sepolicy / backup_policydb
    计算 AVD，干净策略视图早已实现，不是缺口。
  - initialize_fake_status() 是一次性 memcpy 真实 status 页，此后 fake 页的
    sequence 被冻结（本机 4），真实页已涨到 6；access 回包又是固定值
    -> 正命中 4.6.2 判据「seqno 不前进 / 回填不一致」。
  - 失败路径原本 goto out 返回 -EINVAL（无效上下文应保持），v7 的兜底块
    把它改成了 allow/deny 元组 -> 新增 invalid-context access decision。

v8 做法（相对 v6 只有一处逻辑改动）：
  1) 删除 v7 的兜底块，恢复无效上下文的 -EINVAL 原语义；
  2) seqno 不再用固定常量，改为每次查询从真实 status 页读
     READ_ONCE(ksu_real_status_ptr->sequence)，并同步刷新 App 侧
     fake status 页的 sequence。真实页 / fake 页 / access 回包三者
     同源且随真实 AVC / policyload 更新前进。

严格边界：不改 live policy、不改真实 status 页、不新增 hook 点、
不动任何模块；只按 App uid 过滤（沿用上游 my_write_access 的
uid<10000 提前返回 orig_access_write）。幂等；锚点缺失 exit 2。
"""
import os
import re
import sys

MARK = "KSU-MUNCH-FIX-V6"          # v6 已有块原样保留
V8 = "KSU-MUNCH-FIX-V8"

DECL = (
    "/* " + MARK + ": single source of truth for App-visible status.sequence and\n"
    " * access avd.seqno (both come from avc_cache.latest_notif on stock kernels) */\n"
    "static struct selinux_kernel_status *ksu_real_status_ptr;\n"
    "/* " + V8 + ": the real sequence advances on AVC / policyload updates; a\n"
    " * frozen copy is exactly what the 4.6.2 oracle flags. */\n"
    "static inline u32 ksu_current_status_seq(void)\n"
    "{\n"
    "    return ksu_real_status_ptr ? READ_ONCE(ksu_real_status_ptr->sequence) : 1;\n"
    "}\n"
)

ASSIGN = (
    "    /* " + MARK + ": keep a handle on the real page */\n"
    "    ksu_real_status_ptr = status;\n"
)

SYNC_BLOCK = (
    "        if (data) {\n"
    "            /* " + MARK + ": serve a coherent (status, access) pair.\n"
    "             * policyload=1 matches what a stock boot ends with;\n"
    "             * 4.19 built-in never reaches the upstream >=6.10 branch. */\n"
    "            if (ksu_real_status_ptr) {\n"
    "                /* fake_status is struct page *, NOT a virtual address. */\n"
    "                struct selinux_kernel_status *fake =\n"
    "                    (struct selinux_kernel_status *)page_address((struct page *)data);\n"
    "                /* " + V8 + ": start from the live sequence, not a constant. */\n"
    "                fake->sequence   = ksu_current_status_seq();\n"
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
    "    /* " + V8 + ": refresh the App-visible mmap status page and the access\n"
    "     * reply from the SAME live sequence on every query. The detector\n"
    "     * checks that seqno advances / matches status.sequence; a frozen\n"
    "     * constant (upstream 1, v6 4) can never satisfy it. */\n"
    "    if (fake_status) {\n"
    "        struct selinux_kernel_status *fake =\n"
    "            (struct selinux_kernel_status *)page_address((struct page *)fake_status);\n"
    "        fake->sequence = ksu_current_status_seq();\n"
    "    }\n"
    "    avd.seqno = ksu_current_status_seq();\n"
)

CLAMP_BLOCK = (
    "    /* 原生 Android boot 至少加载过一次策略，sequence 不可能为 0；\n"
    "       钳制为 1，避免被判定 sequence/policyload unexpected*/\n"
    "    if (!ksu_late_loaded && new_status->sequence == 0)\n"
    "        new_status->sequence = 1;\n"
)

# v7 兜底块（若源码里存在，必须整块删除）
V7_FALLBACK_RE = re.compile(
    r"out:\n"
    r"    /\* KSU-MUNCH-FIX-V7:.*?\*/\n"
    r"    if \(length <= 0 && likely\(ksu_get_uid_t\(current_uid\(\)\) >= 10000\)\) \{\n"
    r"(?:.*?\n)*?"
    r"    \}\n"
    r"    kfree\(tcon\);\n", re.S)


def _drop_v7_fallback(src):
    """撤销 v7 兜底块，恢复 out: 的原生 -EINVAL 语义。"""
    new, n = V7_FALLBACK_RE.subn("out:\n    kfree(tcon);\n", src, count=1)
    return new, n


def _apply_full(src):
    """从干净上游源码一次打全套（v4 panic 修复 + v5 policyload + v6 + v8）。"""
    if CLAMP_BLOCK in src:
        src = src.replace(CLAMP_BLOCK, "", 1)
        n_clamp = 1
    else:
        pat = re.compile(
            r"[ \t]*if \(!ksu_late_loaded && new_status->sequence == 0\)\n"
            r"[ \t]*new_status->sequence = 1;\n")
        src, n_clamp = pat.subn("", src, count=1)

    decl_anchor = "__maybe_static struct page *fake_status = NULL;"
    if decl_anchor not in src:
        print("FATAL: fake_status declaration anchor missing")
        return None
    src = src.replace(decl_anchor, decl_anchor + "\n" + DECL.rstrip("\n"), 1)

    assign_anchor = "    struct selinux_kernel_status *status = page_address(selinux_status_page);"
    if assign_anchor not in src:
        print("FATAL: initialize_fake_status real-page anchor missing")
        return None
    src = src.replace(assign_anchor, assign_anchor + "\n" + ASSIGN.rstrip("\n"), 1)

    if OLD_OPEN not in src:
        print("FATAL: my_sel_open_handle_status 'if (data)' block missing")
        return None
    src = src.replace(OLD_OPEN, SYNC_BLOCK, 1)

    if SEQ_ASSIGN_OLD not in src:
        print("FATAL: 'avd.seqno = 1;' anchor missing (upstream changed?)")
        return None
    src = src.replace(SEQ_ASSIGN_OLD, SEQ_ASSIGN_NEW, 1)
    return src, n_clamp


def _upgrade(src):
    """已打过早期修复 -> 就地升级到 v8（CI 实际走 _apply_full，此路径保底）。

    只做定点替换，不整体替换 if (data) 块：早期块内含嵌套的
    if (ksu_real_status_ptr)，块级正则容易误切并留下残码。
    """
    # 1) 声明区整块替换（含旧的 ksu_fake_status_seq 常量），避免重复声明
    decl_pat = re.compile(
        r"/\* KSU-MUNCH-FIX-V[3456]:.*?"
        r"static struct selinux_kernel_status \*ksu_real_status_ptr;\n", re.S)
    src, n_decl = decl_pat.subn(DECL, src, count=1)
    if n_decl == 0:
        src = re.sub(r"static u32 ksu_fake_status_seq = 4;\n", "", src, count=1)
        anchor = "__maybe_static struct page *fake_status = NULL;"
        if anchor not in src:
            print("FATAL: fake_status declaration anchor missing")
            return None
        src = src.replace(anchor, anchor + "\n" + DECL.rstrip("\n"), 1)

    # 2) status 页 open 时的 sequence 同步（保留 v6 块结构，只改取值）
    src = src.replace("fake->sequence   = ksu_fake_status_seq;",
                      "fake->sequence   = ksu_current_status_seq();", 1)
    src = src.replace("sequence=ksu_fake_status_seq matches",
                      "sequence=ksu_current_status_seq() matches", 1)

    # 3) seqno 赋值：无论旧版是常量还是 v6 变量，统一换成动态同源块
    src, n_seq = re.subn(r"    avd\.seqno = (?:1|ksu_fake_status_seq);\n",
                         SEQ_ASSIGN_NEW, src, count=1)
    if n_seq == 0 and "avd.seqno = ksu_current_status_seq();" not in src:
        print("FATAL: avd.seqno assignment not found")
        return None

    src = re.sub(r"static u32 ksu_fake_status_seq = 4;\n", "", src)
    src = re.sub(r"KSU-MUNCH-FIX-V[345]", MARK, src)
    return src, 0


def _verify(src):
    required = [
        "ksu_real_status_ptr;",
        "static inline u32 ksu_current_status_seq(void)",
        "ksu_real_status_ptr = status;",
        "page_address((struct page *)data)",
        "fake->sequence   = ksu_current_status_seq();",
        "fake->policyload = 1;",
        "avd.seqno = ksu_current_status_seq();",
        "fake->sequence = ksu_current_status_seq();",
        V8,
    ]
    missing = [x for x in required if x not in src]
    if missing:
        print("FATAL: post-patch verification failed, missing: %s" % missing)
        return False
    bad = ["avd.seqno = 1;", "(struct selinux_kernel_status *)data;",
           "struct av_decision denied;", "KSU-MUNCH-FIX-V7",
           "ksu_fake_status_seq"]
    stale = [x for x in bad if x in src]
    if stale:
        print("FATAL: stale literal / v7 fallback still present: %s" % stale)
        return False
    if src.count(V8) < 2:
        print("FATAL: v8 markers missing")
        return False
    return True


def main(root):
    path = os.path.join(root, "kernel", "feature", "selinux_hide.c")
    if not os.path.isfile(path):
        print("FATAL: %s not found" % path)
        return 2
    with open(path, encoding="utf-8") as f:
        src = f.read()

    src, n_drop = _drop_v7_fallback(src)

    if V8 in src and all(a in src for a in (
            "ksu_current_status_seq()", "avd.seqno = ksu_current_status_seq();")):
        print("selinux_hide.c: already v8, skip (removed_v7_fallback=%d)" % n_drop)
        return 0

    if any("KSU-MUNCH-FIX-V%d" % n in src for n in (3, 4, 5, 6, 7)):
        res = _upgrade(src)
        tag = "v6/v7 -> v8 (dynamic seqno, v7 fallback removed)"
    else:
        res = _apply_full(src)
        tag = "v8 full patch (v6 baseline + dynamic seqno, no fallback)"

    if res is None:
        return 2
    src, n_clamp = res

    if not _verify(src):
        return 2
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(src)
    print("selinux_hide.c: %s removed_v2_clamp=%d removed_v7_fallback=%d"
          % (tag, n_clamp, n_drop))
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: %s <KSU source root>" % sys.argv[0])
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
