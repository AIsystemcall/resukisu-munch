#!/usr/bin/env bash
# This file is tri-licensed under GPLv2, Apache 2.0, or the SQLite Blessing.
# You may choose any of these licenses to use this code.
#
# In place of a legal notice, here is a blessing:
#  - May you do good and not evil.
#  - May you find forgiveness for yourself and forgive others.
#  - May you share freely, never taking more than you give.
#
# SPDX-License-Identifier: GPL-2.0-only OR Apache-2.0 OR blessing

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKDIR="${WORKDIR:-$ROOT_DIR/work}"
CLANG_PREBUILT_DIR="$WORKDIR/aosp-clang-ref"
REF_VARIANT="${REF_VARIANT:-strict-susfs}"
REF_KERNEL_REPO="${REF_KERNEL_REPO:-https://github.com/liyafe1997/kernel_xiaomi_sm8250_mod.git}"
REF_KERNEL_REF="${REF_KERNEL_REF:-android15-lineage22-mod}"
RESUKISU_REPO="${RESUKISU_REPO:-https://github.com/ReSukiSU/ReSukiSU.git}"
RESUKISU_REF="${RESUKISU_REF:-main}"
NONGKI_REPO="${NONGKI_REPO:-https://github.com/JackA1ltman/NonGKI_Kernel_Build_2nd.git}"
NONGKI_REF="${NONGKI_REF:-mainline}"
NONGKI_DIR="$WORKDIR/non-gki-ref-build"
ANYKERNEL_REPO="${ANYKERNEL_REPO:-https://github.com/osm0sis/AnyKernel3.git}"
AOSP_CLANG_REPO="${AOSP_CLANG_REPO:-https://android.googlesource.com/platform/prebuilts/clang/host/linux-x86}"
AOSP_CLANG_REF="${AOSP_CLANG_REF:-android-msm-redbull-4.19-android12-qpr3}"
AOSP_CLANG_VERSION="${AOSP_CLANG_VERSION:-clang-r399163b}"
USE_AOSP_CLANG="${USE_AOSP_CLANG:-0}"
ARCH="${ARCH:-arm64}"
TARGET_DEVICE="${TARGET_DEVICE:-munch}"
CROSS_COMPILE="${CROSS_COMPILE:-aarch64-linux-gnu-}"
CROSS_COMPILE_ARM32="${CROSS_COMPILE_ARM32:-arm-linux-gnueabi-}"
CLANG_TRIPLE="${CLANG_TRIPLE:-aarch64-linux-gnu-}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
JOBS="${JOBS:-$(nproc)}"

case "$REF_VARIANT" in
  strict-susfs)
    DEFAULT_ARTIFACT_DIR="$WORKDIR/artifacts-ref"
    DEFAULT_KERNEL_DIR="$WORKDIR/ref-kernel-sm8250-build"
    DEFAULT_ANYKERNEL_DIR="$WORKDIR/AnyKernel3-ref"
    DEFAULT_OUT_DIR="$WORKDIR/ref-out"
    DEFAULT_ZIP_NAME="munch-miui14-resukisu-ref-strict-experimental.zip"
    ARTIFACT_TRACK="ref"
    BUILD_TRACK="reference-strict-experimental"
    SUSFS_LABEL="strict-v2.3.0"
    SUSFS_VERSION_LABEL="v2.3.0"
    KERNEL_STRING="ReSukiSU munch MIUI14 reference SUSFS v2.3.0"
    PACKAGE_COMMENT="reference-kernel package for Redmi K40S / munch MIUI14 with upstream SUSFS v2.3.0"
    ;;
  nosusfs)
    DEFAULT_ARTIFACT_DIR="$WORKDIR/artifacts-ref-nosusfs"
    DEFAULT_KERNEL_DIR="$WORKDIR/ref-kernel-sm8250-nosusfs-build"
    DEFAULT_ANYKERNEL_DIR="$WORKDIR/AnyKernel3-ref-nosusfs"
    DEFAULT_OUT_DIR="$WORKDIR/ref-nosusfs-out"
    DEFAULT_ZIP_NAME="munch-miui14-resukisu-ref-nosusfs-experimental.zip"
    ARTIFACT_TRACK="ref-nosusfs"
    BUILD_TRACK="reference-nosusfs-experimental"
    SUSFS_LABEL="disabled"
    SUSFS_VERSION_LABEL="none"
    KERNEL_STRING="ReSukiSU munch MIUI14 reference no-SUSFS experimental"
    PACKAGE_COMMENT="no-SUSFS experimental reference-kernel package for Redmi K40S / munch MIUI14"
    ;;
  *)
    echo "unknown REF_VARIANT: $REF_VARIANT" >&2
    exit 1
    ;;
esac

ARTIFACT_DIR="${ARTIFACT_DIR:-$DEFAULT_ARTIFACT_DIR}"
KERNEL_DIR="${KERNEL_DIR:-$DEFAULT_KERNEL_DIR}"
ANYKERNEL_DIR="${ANYKERNEL_DIR:-$DEFAULT_ANYKERNEL_DIR}"
OUT_DIR="${OUT_DIR:-$DEFAULT_OUT_DIR}"
ZIP_NAME="${ZIP_NAME:-$DEFAULT_ZIP_NAME}"

mkdir -p "$WORKDIR"
rm -rf "$KERNEL_DIR" "$ANYKERNEL_DIR" "$ARTIFACT_DIR" "$OUT_DIR" "$NONGKI_DIR"
mkdir -p "$ARTIFACT_DIR"

if [[ "$USE_AOSP_CLANG" == "1" ]]; then
  rm -rf "$CLANG_PREBUILT_DIR"
  git clone --depth 1 --branch "$AOSP_CLANG_REF" "$AOSP_CLANG_REPO" "$CLANG_PREBUILT_DIR"
  export PATH="$CLANG_PREBUILT_DIR/$AOSP_CLANG_VERSION/bin:$PATH"
fi

git clone --depth 1 --branch "$REF_KERNEL_REF" "$REF_KERNEL_REPO" "$KERNEL_DIR"
git clone --depth 1 --branch "$RESUKISU_REF" "$RESUKISU_REPO" "$KERNEL_DIR/KernelSU"
if [[ "$REF_VARIANT" == "strict-susfs" ]]; then
  git clone --depth 1 --branch "$NONGKI_REF" "$NONGKI_REPO" "$NONGKI_DIR"
fi

pushd "$KERNEL_DIR" >/dev/null
bash KernelSU/kernel/setup.sh "$RESUKISU_REF"

case "$REF_VARIANT" in
  strict-susfs)
    # Upstream SUSFS v2.3.00 for 4.19. F3 fuzz is required: this reference tree
    # is 4.19.325 (LineageOS 22 mod), not the munch-s-oss tree the patch targets.
    patch -p1 -F3 --forward < "$NONGKI_DIR/Patches/Patch/susfs_patch_to_4.19.patch"

    # 4.19 has no inotify_mark_user_mask(); the v2.3.00 patch calls the 5.x
    # helper. Do a context-free text fix instead of a hunk-based git apply so
    # it survives line-number drift on this tree.
    python3 - <<'PY'
p = 'fs/notify/fdinfo.c'
s = open(p).read()
n = s.count('inotify_mark_user_mask(mark)')
s = s.replace('inotify_mark_user_mask(mark)', 'mask')
decl = '\t\tu32 mask = mark->mask & IN_ALL_EVENTS;\n'
if decl not in s:
    s = s.replace('\tif (inode) {', '\tif (inode) {\n' + decl, 1)
open(p, 'w').write(s)
print('fdinfo 4.19 compat: replaced %d helper call(s)' % n)
PY

    # v2.3.00-specific inline hooks (the script refuses older SUSFS versions).
    bash "$NONGKI_DIR/Patches/susfs_inline_hook_patches.sh"
    ;;
  nosusfs)
    git apply "$ROOT_DIR/patches/ref-resukisu-manual-hooks.patch"
    ;;
esac

MAKE_ARGS=(
  ARCH="$ARCH"
  SUBARCH="$ARCH"
  O="$OUT_DIR"
  CC=clang
  PYTHON="$PYTHON_BIN"
  CROSS_COMPILE="$CROSS_COMPILE"
  CROSS_COMPILE_ARM32="$CROSS_COMPILE_ARM32"
  CROSS_COMPILE_COMPAT="$CROSS_COMPILE_ARM32"
  CLANG_TRIPLE="$CLANG_TRIPLE"
)

make "${MAKE_ARGS[@]}" "${TARGET_DEVICE}_defconfig"

scripts/config --file "$OUT_DIR/.config" \
  --set-str STATIC_USERMODEHELPER_PATH /system/bin/micd \
  --enable KSU \
  --disable KPM \
  --enable XIAOMI_MIUI \
  --enable MIHW \
  --enable PACKAGE_RUNTIME_INFO \
  --enable MIGT \
  --enable MIGT_ENERGY_MODEL \
  --enable MILLET \
  --enable PERF_CRITICAL_RT_TASK \
  --enable SF_BINDER \
  --enable BINDER_OPT \
  --enable KPERFEVENTS \
  --enable PERF_HUMANTASK \
  --enable TASK_DELAY_ACCT \
  --enable MIUI_ZRAM_MEMORY_TRACKING \
  --enable MI_FRAGMENTION \
  --enable PERF_HELPER \
  --enable BOOTUP_RECLAIM \
  --enable MI_RECLAIM \
  --enable RTMM \
  --enable OVERLAY_FS \
  --disable DEBUG_FS \
  --disable MI_MEMORY_SYSFS \
  --disable LTO_CLANG \
  --disable LOCALVERSION_AUTO

if [[ "$REF_VARIANT" == "strict-susfs" ]]; then
  scripts/config --file "$OUT_DIR/.config" \
  --enable KSU_SUSFS \
  --enable KSU_SUSFS_SUS_PATH \
  --enable KSU_SUSFS_SUS_MOUNT \
  --enable KSU_SUSFS_SUS_KSTAT \
  --enable KSU_SUSFS_SPOOF_UNAME \
  --enable KSU_SUSFS_ENABLE_LOG \
  --enable KSU_SUSFS_HIDE_KSU_SUSFS_SYMBOLS \
  --enable KSU_SUSFS_SPOOF_CMDLINE_OR_BOOTCONFIG \
  --enable KSU_SUSFS_OPEN_REDIRECT \
  --enable KSU_SUSFS_SUS_MAP \
  --disable KSU_TRACEPOINT_HOOK \
  --disable KSU_MANUAL_HOOK
else
  scripts/config --file "$OUT_DIR/.config" \
  --disable KSU_TRACEPOINT_HOOK \
  --enable KSU_MANUAL_HOOK \
  --enable KSU_MANUAL_HOOK_AUTO_SETUID_HOOK \
  --enable KSU_MANUAL_HOOK_AUTO_INITRC_HOOK \
  --enable KSU_MANUAL_HOOK_AUTO_INPUT_HOOK \
  --disable KSU_SUSFS \
  --disable KSU_SUSFS_SUS_PATH \
  --disable KSU_SUSFS_SUS_MOUNT \
  --disable KSU_SUSFS_SUS_KSTAT \
  --disable KSU_SUSFS_SPOOF_UNAME \
  --disable KSU_SUSFS_ENABLE_LOG \
  --disable KSU_SUSFS_HIDE_KSU_SUSFS_SYMBOLS \
  --disable KSU_SUSFS_SPOOF_CMDLINE_OR_BOOTCONFIG \
  --disable KSU_SUSFS_OPEN_REDIRECT \
  --disable KSU_SUSFS_SUS_MAP
fi

make "${MAKE_ARGS[@]}" olddefconfig
make "${MAKE_ARGS[@]}" -j"$JOBS" Image

test -s "$OUT_DIR/arch/$ARCH/boot/Image"
test -s "$OUT_DIR/System.map"
popd >/dev/null

git clone --depth 1 "$ANYKERNEL_REPO" "$ANYKERNEL_DIR"

cat > "$ANYKERNEL_DIR/anykernel.sh" <<EOF
### AnyKernel3 Ramdisk Mod Script
## $PACKAGE_COMMENT

properties() { '
kernel.string=$KERNEL_STRING
do.devicecheck=1
do.modules=0
do.systemless=1
do.cleanup=1
do.cleanuponabort=1
device.name1=munch
device.name2=22021211RC
device.name3=22021211RG
supported.versions=13
supported.patchlevels=
supported.vendorpatchlevels=
'; }

boot_attributes() {
set_perm_recursive 0 0 755 644 \$RAMDISK/*
set_perm_recursive 0 0 750 750 \$RAMDISK/init* \$RAMDISK/sbin
}

BLOCK=/dev/block/bootdevice/by-name/boot
IS_SLOT_DEVICE=1
RAMDISK_COMPRESSION=auto
PATCH_VBMETA_FLAG=auto

. tools/ak3-core.sh

dump_boot
write_boot
EOF

cp "$OUT_DIR/arch/$ARCH/boot/Image" "$ANYKERNEL_DIR/Image"
(cd "$ANYKERNEL_DIR" && zip -r9 "$ARTIFACT_DIR/$ZIP_NAME" . -x '*.git*')
cp "$OUT_DIR/arch/$ARCH/boot/Image" "$ARTIFACT_DIR/Image"
cp "$OUT_DIR/.config" "$ARTIFACT_DIR/kernel.config"
cp "$OUT_DIR/System.map" "$ARTIFACT_DIR/System.map"

cat > "$ARTIFACT_DIR/build-info.txt" <<EOF
track=$BUILD_TRACK
kernel_ref=$REF_KERNEL_REF
kernel_sha=$(git -C "$KERNEL_DIR" rev-parse HEAD)
resukisu_ref=$RESUKISU_REF
resukisu_sha=$(git -C "$KERNEL_DIR/KernelSU" rev-parse HEAD)
susfs=$SUSFS_LABEL
susfs_version=$SUSFS_VERSION_LABEL
susfs_patch=upstream-2.3.00
nongki_ref=$NONGKI_REF
nongki_sha=$(git -C "$NONGKI_DIR" rev-parse HEAD 2>/dev/null || echo none)
use_aosp_clang=$USE_AOSP_CLANG
aosp_clang_ref=$AOSP_CLANG_REF
aosp_clang_version=$AOSP_CLANG_VERSION
display_mi=reference-miui
zip_name=$ZIP_NAME
EOF

bash "$ROOT_DIR/scripts/verify-artifact.sh" "$ARTIFACT_DIR" "$ARTIFACT_TRACK"

echo "Reference $REF_VARIANT artifacts are in $ARTIFACT_DIR"
