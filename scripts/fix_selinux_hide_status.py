#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v7 = v6 基线 + **唯一一处**内核侧补齐：App 视图下 /sys/fs/selinux/access
查询在所有路径都返回格式完整、自洽、可重复的 AVD 回包。

本文件以 v6 脚本为基线，代码增量只有 OUT_TAIL_NEW 一处（外加一条自检）。
v6 的 DECL / ASSIGN / SYNC_BLOCK / SEQ_ASSIGN_NEW 与标记全部原样保留。

定位依据（与 v2/v4/v5/v6 的「改 status 数值」路线彻底区分）：
  - 春秋该项详情全文：「检测到可疑 SELinux 上下文/规则关键词：
    status.sequence/policyload、access seqno」。
  - 一手文档判据：以哨兵 context 建立正/负/文件控制三组对照；查询走
    raw selinuxfs 写并校验内核回填的 avdSeqNo；用 selinux_check_access /
    getfilecon 与 raw 结果交叉比对，加扰动后再比对一次，并检查重复性。
  - 上游 my_write_access() 的失败分支（avc_has_perm / sscanf /
    context-to-sid 查找）全部 `goto out`，而 out: 只有 kfree + `return
    length`（length 为负 errno 或 0），buf 完全未填充。
    -> 检测方拿不到结构一致、可重复的回包；事务缓冲未正确结束，
       下一次写得到 EBUSY。本机实测 root 与 App 视角写 access 均
       `Device or resource busy`、读回为空，与此吻合。

v7 做法：在 out: 之前增加**错误路径兜底回包**——对 App uid 视图，任何
解析/权限失败都回填一条 allowed=0（拒绝）但格式完整、seqno 与 App 侧
status.sequence 同源（ksu_fake_status_seq）的 AVD 元组。三组对照、
加扰动、重复查询都拿到结构一致结果；拒绝元组不含任何 live policy
规则信息，不泄露 KSU 注入规则。

严格边界：不改 live policy、不改真实 status 页、不改任何数值、不新增
hook 点、不换栈、不动任何 KSU/LSP 模块；只按 App uid 过滤，root/system
全部走原路径。
幂等；锚点缺失 exit 2 让 CI 失败。
"""
import os
import re
import sys

MARK = "KSU-MUNCH-FIX-V6"          # v6 已有块原样保留
V7 = "KSU-MUNCH-FIX-V7"            # 仅新增块使用

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

# ===== v7 唯一新增补齐点 ==================================================
# 上游 my_write_access() 尾部（干净源码）
OUT_TAIL_OLD = (
    "out:\n"
    "    kfree(tcon);\n"
    "    kfree(scon);\n"
    "    return length;\n"
)
OUT_TAIL_NEW = (
    "out:\n"
    "    /* " + V7 + ": App-visible access oracle must always answer with a\n"
    "     * well-formed, repeatable decision tuple. Upstream returned a negative\n"
    "     * errno (or 0) with an unfilled transaction buffer on every failure\n"
    "     * (avc_has_perm / sscanf / context-to-sid lookup), which leaves the\n"
    "     * transaction unfinished and gives the caller nothing comparable.\n"
    "     * Reply with a denied-but-well-formed tuple instead: no live-policy\n"
    "     * rule is disclosed, seqno stays coherent with the App status view,\n"
    "     * and repeated / perturbed probes all see the same shape.\n"
    "     * App uids only: root/system keep the original error semantics. */\n"
    "    if (length <= 0 && likely(ksu_get_uid_t(current_uid()) >= 10000)) {\n"
    "        struct av_decision denied;\n"
    "        memset(&denied, 0, sizeof(denied));\n"
    "        denied.seqno = ksu_fake_status_seq;\n"
    "        length = scnprintf(buf, SIMPLE_TRANSACTION_LIMIT, \"%x %x %x %x %u %x\",\n"
    "                           denied.allowed, 0xffffffff, denied.auditallow,\n"
    "                           denied.auditdeny, denied.seqno, denied.flags);\n"
    "    }\n"
    "    kfree(tcon);\n"
    "    kfree(scon);\n"
    "    return length;\n"
)
# =======================================================================

CLAMP_BLOCK = (
    "    /* 原生 Android boot 至少加载过一次策略，sequence 不可能为 0；\n"
    "       钳制为 1，避免被判定 sequence/policyload unexpected*/\n"
    "    if (!ksu_late_loaded && new_status->sequence == 0)\n"
    "        new_status->sequence = 1;\n"
)

V7_ANCHORS = (
    "struct av_decision denied;",
    "denied.seqno = ksu_fake_status_seq;",
    "if (length <= 0 && likely(ksu_get_uid_t(current_uid()) >= 10000)) {",
)


def _insert_v7(src, tail_pos):
    """在 my_write_access 的 out: 尾部插入唯一一处补齐。"""
    if "struct av_decision denied;" in src:
        return src
    out_pos = src.find(OUT_TAIL_OLD, tail_pos)
    if out_pos < 0:
        print("FATAL: my_write_access 'out:' tail not found after seqno assignment")
        return None
    return src[:out_pos] + OUT_TAIL_NEW + src[out_pos + len(OUT_TAIL_OLD):]


def _apply_full(src):
    """从干净上游源码一次打全套（v4 panic 修复 + v5 policyload + v6 seqno + v7）。"""
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

    src = _insert_v7(src, src.find(SEQ_ASSIGN_NEW))
    if src is None:
        return None
    return src, n_clamp


def _upgrade(src):
    """CI 链路上已打过早期修复 -> 就地升级，仅追加 v7 一处。"""
    pat = re.compile(r"        if \(data\) \{\n(?:(?!        \}\n).)*?        \}\n", re.S)
    m = pat.search(src)
    if not m:
        print("FATAL: earlier if(data) block not found")
        return None
    src = src[:m.start()] + SYNC_BLOCK + src[m.end():]

    if SEQ_ASSIGN_OLD in src:
        src = src.replace(SEQ_ASSIGN_OLD, SEQ_ASSIGN_NEW, 1)
    elif "avd.seqno = 1;" in src:
        src = src.replace("    avd.seqno = 1;\n", SEQ_ASSIGN_NEW, 1)
    elif "avd.seqno = ksu_fake_status_seq;" not in src:
        print("FATAL: avd.seqno assignment not found")
        return None

    old_decl = re.search(
        r"/\* KSU-MUNCH-FIX-V[345]:[^\n]*\*/\n"
        r"(?:/\*[^\n]*\*/\n)*"
        r"static struct selinux_kernel_status \*ksu_real_status_ptr;\n", src)
    if old_decl:
        src = src[:old_decl.start()] + DECL + src[old_decl.end():]
    elif "static u32 ksu_fake_status_seq" not in src:
        src = src.replace("static struct selinux_kernel_status *ksu_real_status_ptr;\n", DECL, 1)

    src = re.sub(r"KSU-MUNCH-FIX-V[345]", MARK, src)

    src = _insert_v7(src, src.find("avd.seqno = ksu_fake_status_seq;"))
    if src is None:
        return None
    return src, 0


def _verify(src):
    required = [
        "static u32 ksu_fake_status_seq = 4;",
        "ksu_real_status_ptr;",
        "ksu_real_status_ptr = status;",
        "page_address((struct page *)data)",
        "fake->sequence   = ksu_fake_status_seq;",
        "fake->policyload = 1;",
        "avd.seqno = ksu_fake_status_seq;",
    ] + list(V7_ANCHORS)
    missing = [x for x in required if x not in src]
    if missing:
        print("FATAL: post-patch verification failed, missing: %s" % missing)
        return False
    if "avd.seqno = 1;" in src or "(struct selinux_kernel_status *)data;" in src:
        print("FATAL: stale literal / v3 bad cast still present")
        return False
    if src.count(V7) != 1 or src.count("struct av_decision denied;") != 1:
        print("FATAL: v7 fallback block must appear exactly once")
        return False
    return True


def main(root):
    path = os.path.join(root, "kernel", "feature", "selinux_hide.c")
    if not os.path.isfile(path):
        print("FATAL: %s not found" % path)
        return 2
    with open(path, encoding="utf-8") as f:
        src = f.read()

    if V7 in src and all(a in src for a in V7_ANCHORS):
        print("selinux_hide.c: already v7, skip")
        return 0

    if any("KSU-MUNCH-FIX-V%d" % n in src for n in (3, 4, 5, 6)):
        res = _upgrade(src)
        tag = "v6->v7 (one fallback block added)"
    else:
        res = _apply_full(src)
        tag = "v7 full patch (v6 baseline + one fallback block)"

    if res is None:
        return 2
    src, n_clamp = res

    if not _verify(src):
        return 2
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(src)
    print("selinux_hide.c: %s removed_v2_clamp=%d" % (tag, n_clamp))
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: %s <KSU source root>" % sys.argv[0])
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
